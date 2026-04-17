"""
agent/state.py
──────────────────────────────────────────────────────
LangGraph AgentState for the UCP Shopping Agent.

Tracks:
  - conversation messages
  - active UCP checkout session ID
  - cart snapshot (last known line items)
  - order ID once payment is authorized
"""

from typing import Annotated, Any
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


class ShoppingAgentState(BaseModel):
    """State for the UCP shopping agent graph."""

    # Full conversation history — LangGraph merges with add_messages reducer
    messages: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)

    # UCP session tracking
    session_id: str = ""          # set after create_checkout_session
    order_id: str = ""            # set after authorize_payment

    # Cart snapshot — updated after each add/remove
    cart_items: list[dict[str, Any]] = Field(default_factory=list)
    cart_total: float = 0.0

    # Flow metadata
    merchant_capabilities: list[str] = Field(default_factory=list)
    error_message: str = ""

    class Config:
        arbitrary_types_allowed = True
