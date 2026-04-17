"""
api/main.py
──────────────────────────────────────────────────────
FastAPI application exposing the UCP Shopping Agent
as a REST API with streaming support.

Endpoints:
  POST /shop           — single-turn shopping request
  POST /shop/stream    — streaming response (SSE)
  GET  /health         — liveness check
  GET  /capabilities   — show UCP merchant capabilities

Architecture:
  Client → FastAPI → LangGraph Agent → MCP Client → UCP/FastMCP Server
                                              ↕
                                       UCP Tools (9 tools)
"""

import os
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel

from agent.graph import run_shopping_agent

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────

class ShopRequest(BaseModel):
    message: str
    session_history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]


class ShopResponse(BaseModel):
    response: str
    session_id: str = ""
    order_id: str = ""
    turn_count: int = 0


# ─────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="UCP Shopping Agent API",
    description=(
        "An AI shopping agent implementing Universal Commerce Protocol (UCP) "
        "over Model Context Protocol (MCP), powered by LangGraph + Claude."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
# Helper: convert request history to LangChain messages
# ─────────────────────────────────────────────────────────────

def _to_lc_messages(history: list[dict]):
    messages = []
    for turn in history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "UCP Shopping Agent API",
        "version": "1.0.0",
        "mcp_server": os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp"),
    }


@app.get("/capabilities")
async def capabilities():
    """Show UCP merchant capabilities (calls MCP server directly)."""
    import httpx
    try:
        # Directly query the MCP server's capabilities tool
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp"),
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "discover_capabilities", "arguments": {}},
                    "id": 1,
                },
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e), "hint": "Is the MCP server running?"}


@app.post("/shop", response_model=ShopResponse)
async def shop(request: ShopRequest):
    """
    Single-turn shopping request.

    Send a natural language message and get a response from the
    UCP Shopping Agent. Include session_history for multi-turn
    conversations.

    Example:
        POST /shop
        {
          "message": "Show me laptops under $2500",
          "session_history": []
        }
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    try:
        history = _to_lc_messages(request.session_history)
        result = await run_shopping_agent(request.message, history)
        return ShopResponse(
            response=result["response"],
            session_id=result.get("session_id", ""),
            order_id=result.get("order_id", ""),
            turn_count=len(result.get("messages", [])),
        )
    except ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach MCP server. Ensure it is running on port 8001.",
        )
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shop/stream")
async def shop_stream(request: ShopRequest):
    """
    Streaming version of /shop — returns Server-Sent Events (SSE).

    Each event is a JSON chunk: {"chunk": "...", "done": false}
    Final event: {"chunk": "", "done": true, "session_id": "..."}
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            history = _to_lc_messages(request.session_history)
            result = await run_shopping_agent(request.message, history)
            response_text = result["response"]

            # Simulate streaming by chunking the response
            words = response_text.split(" ")
            for i, word in enumerate(words):
                chunk = word + (" " if i < len(words) - 1 else "")
                yield f"data: {json.dumps({'chunk': chunk, 'done': False})}\n\n"

            # Final event
            yield f"data: {json.dumps({'chunk': '', 'done': True, 'session_id': result.get('session_id', ''), 'order_id': result.get('order_id', '')})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", 8000))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=True)
