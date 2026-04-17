"""
agent/graph.py
──────────────────────────────────────────────────────
LangGraph ReAct shopping agent that connects to the
UCP-over-MCP server and executes the full shopping flow:

  discover → search → create session → add items
            → calculate totals → authorize payment → track order

Graph topology:
  START → call_model → [tool_node | END]
               ↑______________|

Uses langchain_mcp_adapters.MultiServerMCPClient to pull
all UCP tools as LangChain-compatible tools at runtime.
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from agent.state import ShoppingAgentState

load_dotenv()
logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")

SYSTEM_PROMPT = """You are an intelligent UCP Shopping Agent powered by MCP tools.

You help users discover products, build carts, and complete purchases using the
Universal Commerce Protocol (UCP). You have access to these UCP tools:

  1. discover_capabilities   — always call this first to understand what the merchant supports
  2. search_products         — find products by query, category, or price
  3. create_checkout_session — open a stateful UCP checkout session (required before adding items)
  4. add_line_item           — add products to the cart (use product_id from search results)
  5. remove_line_item        — remove a product from the cart
  6. get_cart                — see current cart contents
  7. calculate_totals        — get full order breakdown with tax and shipping
  8. authorize_payment       — complete payment (AP2 protocol — requires buyer_consent_token='USER_CONFIRMED')
  9. get_order_status        — track an order post-purchase

UCP Shopping Flow:
  Step 1: discover_capabilities()
  Step 2: search_products(query=...)
  Step 3: create_checkout_session()
  Step 4: add_line_item(session_id, product_id)
  Step 5: calculate_totals(session_id)
  Step 6: authorize_payment(session_id, payment_method, buyer_consent_token='USER_CONFIRMED')

Important rules:
  - Always create a session before adding items
  - Always use product_id from search results (not product names)
  - For payment: the buyer must explicitly confirm before you call authorize_payment
  - AP2 consent token is 'USER_CONFIRMED' — only pass this when the user has confirmed the purchase
  - Keep the user informed at each step
"""


# ─────────────────────────────────────────────────────────────
# Graph builder — call inside async context with MCP client
# ─────────────────────────────────────────────────────────────

async def build_graph(mcp_tools: list):
    """
    Build the LangGraph ReAct agent with UCP tools bound to the LLM.

    Args:
        mcp_tools: List of LangChain tools from the MCP server.

    Returns:
        Compiled LangGraph graph.
    """
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0,
    ).bind_tools(mcp_tools)

    tool_node = ToolNode(mcp_tools)

    # ── Node: call model ─────────────────────────────────────
    async def call_model(state: ShoppingAgentState) -> dict:
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state.messages
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    # ── Routing: continue to tools or end ───────────────────
    def route_after_model(state: ShoppingAgentState) -> Literal["tools", "__end__"]:
        return tools_condition(state)

    # ── Build graph ──────────────────────────────────────────
    graph = StateGraph(ShoppingAgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", route_after_model)
    graph.add_edge("tools", "call_model")

    return graph.compile()


# ─────────────────────────────────────────────────────────────
# Public async runner — used by FastAPI
# ─────────────────────────────────────────────────────────────

async def run_shopping_agent(user_message: str, history: list = None) -> dict:
    """
    Run one turn of the shopping agent.

    Args:
        user_message: The user's input text.
        history:      Optional list of prior LangChain messages.

    Returns:
        dict with 'response' (str) and 'messages' (full history).
    """
    history = history or []

    async with MultiServerMCPClient(
        {
            "ucp_store": {
                "url": MCP_SERVER_URL,
                "transport": "streamable_http",
            }
        }
    ) as mcp_client:
        tools = mcp_client.get_tools()
        logger.info(f"Loaded {len(tools)} UCP tools from MCP server")

        graph = await build_graph(tools)

        from langchain_core.messages import HumanMessage
        input_messages = history + [HumanMessage(content=user_message)]

        state = ShoppingAgentState(messages=input_messages)
        result = await graph.ainvoke(state)

        # Extract final assistant response
        final_message = result["messages"][-1]
        return {
            "response": final_message.content,
            "messages": result["messages"],
            "session_id": result.get("session_id", ""),
            "order_id": result.get("order_id", ""),
        }


# ─────────────────────────────────────────────────────────────
# CLI test runner
# ─────────────────────────────────────────────────────────────

async def _cli_demo():
    """Quick end-to-end demo from the command line."""
    print("\n🛒  UCP Shopping Agent — CLI Demo\n" + "─" * 45)

    turns = [
        "What products do you have available? Show me your laptops.",
        "I want to buy the MacBook Pro. What's the total with tax?",
        "Go ahead and purchase it for me with Google Pay. I confirm the purchase.",
        "What's my order status?",
    ]

    history = []
    for turn in turns:
        print(f"\n👤  User: {turn}")
        result = await run_shopping_agent(turn, history)
        print(f"🤖  Agent: {result['response']}")
        history = result["messages"]


if __name__ == "__main__":
    asyncio.run(_cli_demo())
