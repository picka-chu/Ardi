"""Typed validation for AI-produced order/escalation payloads.

The model returns plain JSON — never trust its shape, types, or ranges.
Everything here coerces-or-rejects in one place. Prices and totals are
ALWAYS recomputed from the catalog in code; the model never sets money.
"""
from typing import Any

from pydantic import BaseModel, field_validator


MAX_ORDER_QTY = 50


class OrderItemModel(BaseModel):
    product: str
    quantity: int = 1

    @field_validator("product")
    @classmethod
    def _name(cls, v: Any) -> str:
        v = str(v or "").strip()[:120]
        if not v:
            raise ValueError("empty product name")
        return v

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> int:
        try:
            q = int(float(v))
        except (ValueError, TypeError):
            raise ValueError("quantity must be a number")
        return max(1, min(MAX_ORDER_QTY, q))


class OrderDataModel(BaseModel):
    customer_name: str = ""
    customer_phone: str = ""
    customer_address: str = ""
    items: list[OrderItemModel] = []

    @field_validator("customer_name", "customer_phone", "customer_address", mode="before")
    @classmethod
    def _str(cls, v: Any) -> str:
        return str(v or "").strip()[:500]

    def delivery_ok(self) -> bool:
        return bool(self.customer_name and self.customer_phone and self.customer_address)


def validate_order_data(data: dict) -> OrderDataModel:
    """Coerce raw AI JSON into a validated order. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("order data must be an object")
    items = data.get("items")
    if not items and data.get("product"):
        items = [{"product": data.get("product"), "quantity": data.get("quantity", 1)}]
    if not items:
        raise ValueError("Order must have at least one item")
    model = OrderDataModel(
        customer_name=data.get("customer_name", ""),
        customer_phone=data.get("customer_phone", ""),
        customer_address=data.get("customer_address", ""),
        items=items if isinstance(items, list) else [],
    )
    if not model.items:
        raise ValueError("Order must have at least one item")
    return model


def resolve_catalog_item(pname: str, products: list) -> object | None:
    """Match an AI-written product name to the catalog.

    Exact match first, then a single unambiguous substring match.
    Returns None when unknown or ambiguous — callers must reject, never 0.00.
    """
    want = (pname or "").strip().lower()
    if not want:
        return None
    for p in products:
        if p.name.lower() == want:
            return p
    cands = [p for p in products if want in p.name.lower() or p.name.lower() in want]
    if len(cands) == 1:
        return cands[0]
    return None
