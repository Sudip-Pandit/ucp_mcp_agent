"""
mcp_server/server.py
────────────────────────────────────────────────────────────────
UCP-over-MCP Server  —  Universal Commerce Protocol exposed as
a FastMCP server with HTTP/SSE transport.

Tools follow UCP semantics:
  • discover_capabilities  — merchant profile + capability list
  • search_products        — product discovery (catalog)
  • create_checkout_session— open a stateful UCP checkout session
  • add_line_item          — add product to session cart
  • remove_line_item       — remove product from session cart
  • get_cart               — current cart state + line items
  • calculate_totals       — totals with tax + shipping
  • authorize_payment      — mock AP2 cryptographic auth flow
  • get_order_status       — post-purchase order tracking

UCP Concepts modelled here:
  Session   = stateful checkout context (UCP core primitive)
  Cart      = live line-item collection within a session
  AP2       = Agent Payments Protocol (mocked with UUID token)
  Merchant  = declares capabilities, remains merchant-of-record
"""

import uuid
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP

# ─────────────────────────────────────────────────────────────
# Mock merchant data  (in production: pull from real catalog)
# ─────────────────────────────────────────────────────────────
MERCHANT_PROFILE = {
    "merchant_id": "merchant_techstore_001",
    "name": "TechStore Pro",
    "currency": "USD",
    "locale": "en-US",
    "capabilities": [
        "product_discovery",
        "checkout_session",
        "flexible_payments",
        "credential_sharing",
        "order_tracking",
    ],
    "payment_methods": ["google_pay", "stripe", "paypal"],
    "supported_protocols": ["UCP/1.0", "A2A", "MCP"],
    "merchant_of_record": True,
}

PRODUCT_CATALOG = [
    {
        "product_id": "prod_001",
        "name": "MacBook Pro 16-inch M4",
        "description": "Apple M4 Pro chip, 24GB RAM, 512GB SSD",
        "price": 2499.00,
        "currency": "USD",
        "category": "laptops",
        "in_stock": True,
        "stock_qty": 15,
        "sku": "APPL-MBP16-M4-24",
        "tags": ["apple", "laptop", "m4", "pro"],
    },
    {
        "product_id": "prod_002",
        "name": "Samsung 27\" 4K Monitor",
        "description": "IPS panel, 144Hz, USB-C 65W charging",
        "price": 449.99,
        "currency": "USD",
        "category": "monitors",
        "in_stock": True,
        "stock_qty": 42,
        "sku": "SMSG-MON27-4K-144",
        "tags": ["samsung", "monitor", "4k", "144hz"],
    },
    {
        "product_id": "prod_003",
        "name": "Keychron Q1 Pro Keyboard",
        "description": "75% layout, QMK/VIA, aluminum CNC body",
        "price": 199.00,
        "currency": "USD",
        "category": "peripherals",
        "in_stock": True,
        "stock_qty": 88,
        "sku": "KYCHRON-Q1PRO",
        "tags": ["keyboard", "mechanical", "keychron", "qmk"],
    },
    {
        "product_id": "prod_004",
        "name": "Logitech MX Master 3S",
        "description": "8K DPI, MagSpeed scroll, USB-C, quiet clicks",
        "price": 99.99,
        "currency": "USD",
        "category": "peripherals",
        "in_stock": True,
        "stock_qty": 120,
        "sku": "LOGI-MXM3S",
        "tags": ["mouse", "logitech", "wireless", "ergonomic"],
    },
    {
        "product_id": "prod_005",
        "name": "Dell XPS 15 (2025)",
        "description": "Intel Core Ultra 9, RTX 4060, 32GB, 1TB",
        "price": 1999.00,
        "currency": "USD",
        "category": "laptops",
        "in_stock": False,
        "stock_qty": 0,
        "sku": "DELL-XPS15-2025",
        "tags": ["dell", "laptop", "xps", "gaming"],
    },
]

TAX_RATE = 0.08       # 8%
SHIPPING_THRESHOLD = 100.00   # free shipping above this
SHIPPING_COST = 9.99

# ─────────────────────────────────────────────────────────────
# In-memory session store  (UCP stateful sessions)
# ─────────────────────────────────────────────────────────────
sessions: dict[str, dict[str, Any]] = {}
orders: dict[str, dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────
# FastMCP app
# ─────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="TechStore UCP Server",
    instructions=(
        "You are a UCP-compliant commerce server. "
        "Use these tools to help agents discover products, "
        "manage checkout sessions, and authorize payments."
    ),
)


# ── Tool 1: Discover capabilities ────────────────────────────
@mcp.tool()
def discover_capabilities() -> dict:
    """
    [UCP] Return the merchant profile and declared capabilities.
    Call this first to understand what the merchant supports
    before initiating any commerce flow.
    """
    return {
        "status": "ok",
        "merchant": MERCHANT_PROFILE,
        "ucp_version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Tool 2: Search products ───────────────────────────────────
@mcp.tool()
def search_products(
    query: str,
    category: str = "",
    max_price: float = 0.0,
    in_stock_only: bool = True,
) -> dict:
    """
    [UCP] Search the merchant product catalog.

    Args:
        query:         Natural language search term (e.g. 'laptop', 'keyboard').
        category:      Filter by category: laptops, monitors, peripherals.
        max_price:     Maximum price filter (0 = no limit).
        in_stock_only: If True, only return items with stock_qty > 0.

    Returns:
        List of matching products with id, name, price, stock status.
    """
    results = PRODUCT_CATALOG.copy()

    # Text filter
    if query:
        q = query.lower()
        results = [
            p for p in results
            if q in p["name"].lower()
            or q in p["description"].lower()
            or any(q in t for t in p["tags"])
        ]

    # Category filter
    if category:
        results = [p for p in results if p["category"] == category.lower()]

    # Price filter
    if max_price > 0:
        results = [p for p in results if p["price"] <= max_price]

    # Stock filter
    if in_stock_only:
        results = [p for p in results if p["in_stock"]]

    return {
        "status": "ok",
        "total_results": len(results),
        "products": [
            {
                "product_id": p["product_id"],
                "name": p["name"],
                "description": p["description"],
                "price": p["price"],
                "currency": p["currency"],
                "in_stock": p["in_stock"],
                "stock_qty": p["stock_qty"],
                "sku": p["sku"],
            }
            for p in results
        ],
    }


# ── Tool 3: Create checkout session ──────────────────────────
@mcp.tool()
def create_checkout_session(buyer_id: str = "anonymous") -> dict:
    """
    [UCP] Open a new stateful checkout session.
    This is the UCP core primitive — all subsequent cart and
    payment operations reference this session_id.

    Args:
        buyer_id: Optional buyer identifier (for loyalty/credential linking).

    Returns:
        session_id to use in all subsequent UCP tool calls.
    """
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    sessions[session_id] = {
        "session_id": session_id,
        "buyer_id": buyer_id,
        "status": "open",        # open → confirmed → paid → fulfilled
        "line_items": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "merchant_id": MERCHANT_PROFILE["merchant_id"],
    }
    return {
        "status": "ok",
        "session_id": session_id,
        "message": "Checkout session created. Use session_id for all cart operations.",
    }


# ── Tool 4: Add line item ─────────────────────────────────────
@mcp.tool()
def add_line_item(session_id: str, product_id: str, quantity: int = 1) -> dict:
    """
    [UCP] Add a product to the checkout session cart.

    Args:
        session_id: Session from create_checkout_session.
        product_id: Product ID from search_products results.
        quantity:   Number of units to add (default 1).

    Returns:
        Updated cart line items.
    """
    if session_id not in sessions:
        return {"status": "error", "message": f"Session '{session_id}' not found."}

    session = sessions[session_id]
    if session["status"] != "open":
        return {"status": "error", "message": "Session is not open for modifications."}

    # Find product
    product = next((p for p in PRODUCT_CATALOG if p["product_id"] == product_id), None)
    if not product:
        return {"status": "error", "message": f"Product '{product_id}' not found."}
    if not product["in_stock"] or product["stock_qty"] < quantity:
        return {"status": "error", "message": f"Insufficient stock for '{product['name']}'."}

    # Check if item already in cart
    existing = next(
        (item for item in session["line_items"] if item["product_id"] == product_id), None
    )
    if existing:
        existing["quantity"] += quantity
        existing["subtotal"] = round(existing["unit_price"] * existing["quantity"], 2)
    else:
        session["line_items"].append({
            "line_item_id": f"li_{uuid.uuid4().hex[:8]}",
            "product_id": product_id,
            "sku": product["sku"],
            "name": product["name"],
            "unit_price": product["price"],
            "quantity": quantity,
            "subtotal": round(product["price"] * quantity, 2),
        })

    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    return {
        "status": "ok",
        "message": f"Added {quantity}x '{product['name']}' to cart.",
        "line_items": session["line_items"],
    }


# ── Tool 5: Remove line item ──────────────────────────────────
@mcp.tool()
def remove_line_item(session_id: str, product_id: str) -> dict:
    """
    [UCP] Remove a product from the checkout session cart.

    Args:
        session_id: Active session ID.
        product_id: Product ID to remove.
    """
    if session_id not in sessions:
        return {"status": "error", "message": f"Session '{session_id}' not found."}

    session = sessions[session_id]
    before = len(session["line_items"])
    session["line_items"] = [
        i for i in session["line_items"] if i["product_id"] != product_id
    ]
    removed = before - len(session["line_items"])
    session["updated_at"] = datetime.now(timezone.utc).isoformat()

    return {
        "status": "ok" if removed else "not_found",
        "message": f"Removed {removed} item(s) from cart.",
        "line_items": session["line_items"],
    }


# ── Tool 6: Get cart ──────────────────────────────────────────
@mcp.tool()
def get_cart(session_id: str) -> dict:
    """
    [UCP] Retrieve the current cart state for a session.
    Shows all line items, quantities, and subtotals.

    Args:
        session_id: Active session ID.
    """
    if session_id not in sessions:
        return {"status": "error", "message": f"Session '{session_id}' not found."}

    session = sessions[session_id]
    return {
        "status": "ok",
        "session_id": session_id,
        "session_status": session["status"],
        "buyer_id": session["buyer_id"],
        "line_items": session["line_items"],
        "item_count": sum(i["quantity"] for i in session["line_items"]),
        "updated_at": session["updated_at"],
    }


# ── Tool 7: Calculate totals ──────────────────────────────────
@mcp.tool()
def calculate_totals(session_id: str, promo_code: str = "") -> dict:
    """
    [UCP] Calculate order totals including tax, shipping, and discounts.

    Args:
        session_id: Active session ID.
        promo_code: Optional promo code (use 'SAVE10' for 10% off).

    Returns:
        Full breakdown: subtotal, discount, shipping, tax, total.
    """
    if session_id not in sessions:
        return {"status": "error", "message": f"Session '{session_id}' not found."}

    session = sessions[session_id]
    if not session["line_items"]:
        return {"status": "error", "message": "Cart is empty."}

    subtotal = sum(i["subtotal"] for i in session["line_items"])

    # Promo codes
    discount = 0.0
    promo_applied = None
    if promo_code.upper() == "SAVE10":
        discount = round(subtotal * 0.10, 2)
        promo_applied = "SAVE10 — 10% discount applied"
    elif promo_code.upper() == "FREESHIP":
        promo_applied = "FREESHIP — free shipping applied"

    discounted_subtotal = subtotal - discount
    shipping = 0.0 if (discounted_subtotal >= SHIPPING_THRESHOLD or promo_applied == "FREESHIP") else SHIPPING_COST
    tax = round(discounted_subtotal * TAX_RATE, 2)
    total = round(discounted_subtotal + shipping + tax, 2)

    return {
        "status": "ok",
        "session_id": session_id,
        "breakdown": {
            "subtotal": round(subtotal, 2),
            "discount": discount,
            "promo_applied": promo_applied,
            "shipping": shipping,
            "tax_rate": f"{int(TAX_RATE*100)}%",
            "tax": tax,
            "total": total,
            "currency": "USD",
        },
    }


# ── Tool 8: Authorize payment (AP2 mock) ─────────────────────
@mcp.tool()
def authorize_payment(
    session_id: str,
    payment_method: str,
    buyer_consent_token: str = "",
) -> dict:
    """
    [UCP/AP2] Authorize payment for a checkout session.
    Implements the Agent Payments Protocol (AP2) pattern:
    cryptographic proof of user consent is generated per transaction.

    AP2 Flow:
      1. Agent presents payment_method + buyer_consent_token
      2. Server verifies consent token (mocked here)
      3. Server generates auth_token = SHA256(session + consent + timestamp)
      4. Session transitions: open → paid

    Args:
        session_id:          Active session ID with items in cart.
        payment_method:      One of: google_pay, stripe, paypal.
        buyer_consent_token: Token proving explicit buyer authorization.
                             Pass 'USER_CONFIRMED' to simulate consent.

    Returns:
        Authorization result with AP2 proof token and order ID.
    """
    if session_id not in sessions:
        return {"status": "error", "message": f"Session '{session_id}' not found."}

    session = sessions[session_id]

    if session["status"] == "paid":
        return {"status": "error", "message": "Session already paid."}
    if not session["line_items"]:
        return {"status": "error", "message": "Cannot authorize payment on empty cart."}
    if payment_method not in MERCHANT_PROFILE["payment_methods"]:
        return {
            "status": "error",
            "message": f"Payment method '{payment_method}' not supported. "
                       f"Supported: {MERCHANT_PROFILE['payment_methods']}",
        }

    # AP2: Verify buyer consent  (mock — real AP2 uses OAuth2 + signed JWTs)
    if buyer_consent_token != "USER_CONFIRMED":
        return {
            "status": "consent_required",
            "message": (
                "AP2 requires explicit buyer consent before payment authorization. "
                "Please confirm the purchase and pass buyer_consent_token='USER_CONFIRMED'."
            ),
        }

    # AP2: Generate cryptographic proof of authorization
    timestamp = datetime.now(timezone.utc).isoformat()
    raw = f"{session_id}:{buyer_consent_token}:{payment_method}:{timestamp}"
    ap2_auth_token = hashlib.sha256(raw.encode()).hexdigest()

    # Calculate final total
    subtotal = sum(i["subtotal"] for i in session["line_items"])
    shipping = 0.0 if subtotal >= SHIPPING_THRESHOLD else SHIPPING_COST
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + shipping + tax, 2)

    # Create order
    order_id = f"ord_{uuid.uuid4().hex[:10].upper()}"
    orders[order_id] = {
        "order_id": order_id,
        "session_id": session_id,
        "buyer_id": session["buyer_id"],
        "line_items": session["line_items"],
        "total": total,
        "currency": "USD",
        "payment_method": payment_method,
        "ap2_auth_token": ap2_auth_token,
        "status": "confirmed",
        "estimated_delivery": "3–5 business days",
        "created_at": timestamp,
    }

    # Update session
    session["status"] = "paid"
    session["order_id"] = order_id
    session["updated_at"] = timestamp

    return {
        "status": "authorized",
        "order_id": order_id,
        "total_charged": total,
        "currency": "USD",
        "payment_method": payment_method,
        "ap2_auth_token": ap2_auth_token[:16] + "...",   # truncated for display
        "message": (
            f"Payment of ${total:.2f} authorized via {payment_method}. "
            f"Order {order_id} confirmed."
        ),
        "merchant_of_record": MERCHANT_PROFILE["name"],
        "estimated_delivery": "3–5 business days",
    }


# ── Tool 9: Get order status ──────────────────────────────────
@mcp.tool()
def get_order_status(order_id: str) -> dict:
    """
    [UCP] Post-purchase: retrieve order status and tracking information.

    Args:
        order_id: Order ID returned from authorize_payment.
    """
    if order_id not in orders:
        return {"status": "error", "message": f"Order '{order_id}' not found."}

    order = orders[order_id]
    return {
        "status": "ok",
        "order_id": order["order_id"],
        "order_status": order["status"],
        "buyer_id": order["buyer_id"],
        "total": order["total"],
        "currency": order["currency"],
        "line_items": order["line_items"],
        "payment_method": order["payment_method"],
        "estimated_delivery": order["estimated_delivery"],
        "created_at": order["created_at"],
    }


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    port = int(os.getenv("MCP_PORT", 8001))
    print(f"🛒  UCP-over-MCP Server starting on port {port}")
    print(f"    Tools: {[t for t in mcp._tool_manager._tools]}")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path="/mcp")
