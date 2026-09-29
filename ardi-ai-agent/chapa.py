"""Chapa (Ethiopian payment gateway) subscription payments.

Flow: owner picks a plan -> we create a Chapa checkout -> owner pays
(Telebirr/CBE/cards inside Chapa) -> Chapa webhook or manual verify ->
subscription activates. Bank-transfer receipts remain as a fallback.
"""
import logging
import secrets
import time

import httpx

from config import (
    CHAPA_SECRET_KEY,
    SUBSCRIPTION_MONTHLY,
    SUBSCRIPTION_YEARLY,
)

logger = logging.getLogger(__name__)

CHAPA_INIT_URL = "https://api.chapa.co/v1/transaction/initialize"
CHAPA_VERIFY_URL = "https://api.chapa.co/v1/transaction/verify"
REQUEST_TIMEOUT = 20


def chapa_configured() -> bool:
    return bool(CHAPA_SECRET_KEY)


def plan_amount(plan: str) -> int | None:
    if plan == "monthly":
        return SUBSCRIPTION_MONTHLY
    if plan == "yearly":
        return SUBSCRIPTION_YEARLY
    return None


def make_tx_ref(business_id: int, plan: str) -> str:
    """Unique merchant reference: ardi-sub-<biz>-<plan>-<time>-<rand>."""
    rand = secrets.token_hex(3)
    return f"ardi-sub-{int(business_id)}-{plan}-{int(time.time())}-{rand}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {CHAPA_SECRET_KEY}"}


def parse_verify_response(data: dict) -> dict:
    """Normalize Chapa verify JSON -> {paid, amount, chapa_ref}.

    The reliable signal is data.status == "success" (Chapa's success
    message is just "Payment details", which contains no status word).
    """
    if not isinstance(data, dict):
        return {"paid": False}
    payload = data.get("data") or {}
    try:
        amount = float(payload.get("amount") or 0)
    except (ValueError, TypeError):
        amount = 0.0
    return {
        "paid": payload.get("status") == "success",
        "amount": amount,
        "chapa_ref": payload.get("reference") or payload.get("tx_ref"),
    }


async def create_checkout(business, plan: str, return_url: str, callback_url: str) -> dict | None:
    """Create a Chapa transaction. Returns {checkout_url, tx_ref} or None."""
    amount = plan_amount(plan)
    if amount is None or not chapa_configured():
        return None
    tx_ref = make_tx_ref(business.id, plan)
    name = (business.name or "Business").strip() or "Business"
    payload = {
        "amount": str(amount),
        "currency": "ETB",
        "email": f"biz-{business.id}@ardi.bot",
        "first_name": name[:50],
        "last_name": (business.phone or "Owner")[:50],
        "phone_number": (business.phone or "")[:20],
        "tx_ref": tx_ref,
        "callback_url": callback_url,
        "return_url": return_url,
        "customization": {"title": "Ardi AI Subscription", "description": f"{plan.capitalize()} plan"},
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(CHAPA_INIT_URL, json=payload, headers=_headers())
            body = r.json()
    except Exception as e:
        logger.error("Chapa initialize failed: %s", e)
        return None
    checkout = ((body or {}).get("data") or {}).get("checkout_url")
    if r.status_code not in (200, 201) or not checkout:
        logger.error("Chapa initialize rejected: %s", body)
        return None
    return {"checkout_url": checkout, "tx_ref": tx_ref}


async def verify_payment(tx_ref: str) -> dict:
    """Server-side verify. Returns {paid, amount, chapa_ref} (paid False on any error)."""
    if not tx_ref or not chapa_configured():
        return {"paid": False}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(f"{CHAPA_VERIFY_URL}/{tx_ref}", headers=_headers())
            body = r.json()
    except Exception as e:
        logger.error("Chapa verify failed: %s", e)
        return {"paid": False}
    if r.status_code != 200:
        logger.warning("Chapa verify non-200 for %s: %s", tx_ref, body)
        return {"paid": False}
    return parse_verify_response(body)
