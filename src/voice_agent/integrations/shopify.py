"""Minimal Shopify order lookup adapter.

Provides a simple, read-only helper to fetch order information by an
identifier (numeric ID or order name). Uses environment variables
`SHOPIFY_STORE_URL` and `SHOPIFY_ACCESS_TOKEN` for configuration.

This implementation is intentionally minimal and tolerant of missing
configuration: if not configured, functions return None.
"""
from typing import Optional, Dict
import os

try:
    import httpx
except Exception:
    httpx = None


SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")


def _configured() -> bool:
    return bool(SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN and httpx)


async def lookup_order(order_identifier: str) -> Optional[Dict]:
    """Attempt to retrieve an order by ID or name.

    Returns a dict with basic order fields or None if not found or not configured.
    """
    if not _configured():
        print("Shopify not configured; skipping order lookup")
        return None

    base = SHOPIFY_STORE_URL.rstrip("/")
    headers = {"X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # First, try numeric ID lookup
        if order_identifier.isdigit():
            try:
                resp = await client.get(f"{base}/admin/api/2023-10/orders/{order_identifier}.json", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("order")
                    return _extract_fields(data)
            except Exception as exc:
                print(f"Shopify numeric lookup failed: {exc}")

        # Next, try a search by name using orders.json?name=
        try:
            resp = await client.get(f"{base}/admin/api/2023-10/orders.json", params={"status": "any", "name": order_identifier}, headers=headers)
            if resp.status_code == 200:
                orders = resp.json().get("orders", [])
                if orders:
                    return _extract_fields(orders[0])
        except Exception as exc:
            print(f"Shopify search by name failed: {exc}")

        # As a last-ditch attempt, try searching by order_number (numeric)
        if order_identifier.isdigit():
            try:
                resp = await client.get(f"{base}/admin/api/2023-10/orders.json", params={"status": "any", "order_number": order_identifier}, headers=headers)
                if resp.status_code == 200:
                    orders = resp.json().get("orders", [])
                    if orders:
                        return _extract_fields(orders[0])
            except Exception as exc:
                print(f"Shopify search by order_number failed: {exc}")

    return None


def _extract_fields(order: Dict) -> Dict:
    """Return a minimal dict with friendly order info."""
    if not order:
        return {}

    return {
        "id": order.get("id"),
        "order_number": order.get("order_number") or order.get("name"),
        "email": order.get("email"),
        "financial_status": order.get("financial_status"),
        "fulfillment_status": order.get("fulfillment_status"),
        "created_at": order.get("created_at"),
        "shipping_lines": order.get("shipping_lines", []),
        "fulfillments": order.get("fulfillments", []),
    }