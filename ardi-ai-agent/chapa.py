"""Chapa (Ethiopian payment gateway) subscription payments.

Flow: owner picks a plan -> we create a Chapa checkout -> owner pays
(Telebirr/CBE/cards inside Chapa) -> Chapa webhook or manual verify ->
subscription activates. Bank-transfer receipts remain as a fallback.
"""
import logging
import secrets
import time

import httpx

from config import CHAPA_SECRET_KEY

logger = logging.getLogger(__name__)

CHAPA_INIT_URL = "https://api.chapa.co/v1/transaction/initialize"
CHAPA_VERIFY_URL = "https://api.chapa.co/v1/transaction/verify"
REQUEST_TIMEOUT = 20


def chapa_configured() -> bool:
    return bool(CHAPA_SECRET_KEY)


def make_tx_ref(business_id: int, label: str) -> str:
    """Unique merchant reference: ardi-<label>-<biz>-<time>-<rand>."""
    rand = secrets.token_hex(3)
    safe = "".join(c if c.isalnum() else "-" for c in str(label))[:20] or "pay"
    return f"ardi-{safe}-{int(business_id)}-{int(time.time())}-{rand}"


def _headers(secret: str | None = None) -> dict:
    return {"Authorization": f"Bearer {secret or CHAPA_SECRET_KEY}"}


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


async def create_checkout(business, plan: str, amount: int, return_url: str, callback_url: str, secret: str | None = None, ref_prefix: str = "sub") -> dict | None:
    """Create a Chapa checkout. Amount comes from admin-set prices (subscriptions)
    or the invoice total (orders, with the business's own key).

    Returns {checkout_url, tx_ref} or None.
    """
    key = secret or CHAPA_SECRET_KEY
    if plan not in ("monthly", "yearly", "order") or not amount or not key:
        return None
    tx_ref = make_tx_ref(business.id, ref_prefix if plan == "order" else plan)
    name = (business.name or "Business").strip() or "Business"
    # Chapa requires a valid email (we don't collect owner emails; receipts live in-app).
    email = f"ardi-biz-{int(business.id)}@gmail.com"
    payload = {
        "amount": str(amount),
        "currency": "ETB",
        "email": email,
        "first_name": name[:50],
        "last_name": (business.phone or "Owner")[:50],
        "phone_number": (business.phone or "")[:20],
        "tx_ref": tx_ref,
        "callback_url": callback_url,
        "return_url": return_url,
        # NOTE: Chapa caps customization.title at 16 chars.
        "customization": {"title": "Ardi AI Plan" if plan != "order" else "Ardi Order Pay",
                          "description": f"{plan.capitalize()} plan" if plan != "order" else "Order payment"},
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(CHAPA_INIT_URL, json=payload, headers=_headers(key))
            body = r.json()
    except Exception as e:
        logger.error("Chapa initialize failed: %s", e)
        return None
    checkout = ((body or {}).get("data") or {}).get("checkout_url")
    if r.status_code not in (200, 201) or not checkout:
        logger.error("Chapa initialize rejected: %s", body)
        return None
    return {"checkout_url": checkout, "tx_ref": tx_ref}


async def verify_payment(tx_ref: str, secret: str | None = None) -> dict:
    """Server-side verify. Pass a business key for order checkouts.

    Returns {paid, amount, chapa_ref} (paid False on any error).
    """
    key = secret or CHAPA_SECRET_KEY
    if not tx_ref or not key:
        return {"paid": False}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(f"{CHAPA_VERIFY_URL}/{tx_ref}", headers=_headers(key))
            body = r.json()
    except Exception as e:
        logger.error("Chapa verify failed: %s", e)
        return {"paid": False}
    if r.status_code != 200:
        logger.warning("Chapa verify non-200 for %s: %s", tx_ref, body)
        return {"paid": False}
    return parse_verify_response(body)
