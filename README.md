# ucp_mcp_agent

A production-grade implementation of **Universal Commerce Protocol (UCP)** over **Model Context Protocol (MCP)**, powered by **LangGraph + Claude + FastAPI**.

An AI agent executes the full UCP shopping flow — discover → search → session → cart → payment (AP2) → order — via MCP tool calls, with a REST API layer for real integrations.

```
Client (REST / SSE)
       │
       ▼
FastAPI  (api/main.py)        ← POST /shop, POST /shop/stream
       │
       ▼
LangGraph ReAct Agent         ← agent/graph.py + agent/state.py
       │  MCP tool calls (streamable-http)
       ▼
FastMCP Server                ← mcp_server/server.py  :8001
       │
       ▼
UCP Commerce Layer            ← 9 tools: discover → search → session → cart → AP2 → orders
```

---

## Repo Structure

```
ucp_mcp_agent/
├── agent/
│   ├── graph.py          # LangGraph ReAct agent + MCP client
│   └── state.py          # ShoppingAgentState (Pydantic)
├── api/
│   └── main.py           # FastAPI: POST /shop, POST /shop/stream
├── mcp_server/
│   └── server.py         # FastMCP server exposing 9 UCP tools
├── .env.example          # Copy to .env and fill in your API key
├── requirements.txt
└── README.md
```

---

## UCP Tools (9)

| # | Tool | UCP Concept |
|---|---|---|
| 1 | `discover_capabilities` | Merchant Profile |
| 2 | `search_products` | Catalog module |
| 3 | `create_checkout_session` | Session (core UCP primitive) |
| 4 | `add_line_item` | Cart mutation |
| 5 | `remove_line_item` | Cart mutation |
| 6 | `get_cart` | Session state read |
| 7 | `calculate_totals` | Pricing engine (tax + shipping + promos) |
| 8 | `authorize_payment` | AP2 — per-transaction consent token |
| 9 | `get_order_status` | Post-purchase tracking |

---

## Prerequisites

- Python 3.11+
- Anthropic API key

---

## Setup

```bash
git clone https://github.com/Sudip-Pandit/ucp_mcp_agent
cd ucp_mcp_agent

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

---

## Running

You need two terminals — the MCP server and the API run separately.

**Terminal 1 — Start the UCP/MCP server (port 8001)**
```bash
python mcp_server/server.py
```

**Terminal 2 — Start the FastAPI agent (port 8000)**
```bash
uvicorn api.main:app --reload --port 8000
```

**API is now live at `http://localhost:8000`**

---

## Usage

**Single-turn request**
```bash
curl -X POST http://localhost:8000/shop \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me laptops under $2500"}'
```

**Streaming (SSE)**
```bash
curl -X POST http://localhost:8000/shop/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "I want to buy the MacBook Pro. Confirm purchase with Google Pay."}'
```

**Check merchant capabilities**
```bash
curl http://localhost:8000/capabilities
```

**Full multi-turn flow**
```bash
# Turn 1: Browse
curl -X POST http://localhost:8000/shop \
  -d '{"message": "What laptops do you have?"}'

# Turn 2: Add to cart (paste session_history from previous response)
curl -X POST http://localhost:8000/shop \
  -d '{"message": "Add the MacBook Pro", "session_history": [...]}'

# Turn 3: Confirm and pay
curl -X POST http://localhost:8000/shop \
  -d '{"message": "Yes, confirm the purchase with Google Pay", "session_history": [...]}'
```

---

## Test Discount Codes

| Code | Effect |
|---|---|
| `SAVE10` | 10% off entire order |
| `FREESHIP` | Free standard shipping |

---

## Key Design Points

**Why two processes?** The MCP server and the agent are intentionally separate. In production, the MCP server is a merchant's deployed service — the agent connects to it over HTTP the same way it would connect to any external UCP-compliant merchant.

**AP2 consent enforcement** — `authorize_payment` rejects calls without `buyer_consent_token='USER_CONFIRMED'`. The agent's system prompt enforces that this token is only passed after explicit user confirmation.

**Session persistence** — `session_id` is the long-lived UCP checkout handle. The LangGraph state (`ShoppingAgentState`) carries it across all agent turns so it survives LLM context windows and retries.

**Streaming** — `/shop/stream` returns Server-Sent Events. Each word of the agent's response streams as it's produced, making it suitable for a chat UI frontend.

---

## License

MIT
