"""Customer receipt intake: bank receipt links (authoritative), photos (OCR
elsewhere), PDFs, and generic links.

Security model for generic links (SSRF-safe fetch):
- https only, no credentials in URL, no ports other than 443
- hostname must resolve to a PUBLIC IP (private/loopback/link-local/
  multicast/reserved ranges rejected, every resolved address checked)
- allowlisted content types, hard size cap while streaming, redirect limit

Bank receipt links (CBE/Dashen/Awash/BOA/Zemen/Telebirr share URLs) are
verified against the bank itself via ethiobank-receipts — stronger than OCR.
"""
import io
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_RECEIPT_BYTES = 6 * 1024 * 1024
ALLOWED_TYPES = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "application/pdf": "pdf",
}
REQUEST_TIMEOUT = 25
MAX_REDIRECTS = 3


class ReceiptError(Exception):
    """User-safe receipt failure (message is shown to the customer)."""


def looks_like_url(text: str) -> str | None:
    text = (text or "").strip()
    if not text or " " in text or "." not in text:
        return None
    if text.startswith(("http://", "https://")):
        return text
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", text):
        return "https://" + text
    return None


def _public_host(host: str) -> None:
    """Raise ReceiptError unless host resolves exclusively to public IPs."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ReceiptError("Couldn't open that link. Check it and try again.")
    ips = {info[4][0] for info in infos}
    if not ips:
        raise ReceiptError("Couldn't open that link. Check it and try again.")
    for ip in ips:
        try:
            if not ipaddress.ip_address(ip).is_global:
                raise ReceiptError("That link isn't allowed. Send a photo or PDF file instead.")
        except ValueError:
            raise ReceiptError("That link isn't allowed. Send a photo or PDF file instead.")


async def fetch_receipt_url(url: str) -> tuple[str, bytes]:
    """Download a receipt link. Returns (kind, bytes) where kind is image|pdf."""
    url = (url or "").strip()
    try:
        parts = urlparse(url)
    except Exception:
        raise ReceiptError("That doesn't look like a valid link.")
    if parts.scheme != "https":
        raise ReceiptError("Link must start with https://")
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        raise ReceiptError("That link isn't allowed. Send a photo or PDF file instead.")
    host = (parts.hostname or "").lower()
    if not host or host == "localhost":
        raise ReceiptError("That link isn't allowed. Send a photo or PDF file instead.")
    if parts.port and parts.port != 443:
        raise ReceiptError("That link isn't allowed. Send a photo or PDF file instead.")
    _public_host(host)

    data = bytearray()
    content_type = ""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True,
                                     max_redirects=MAX_REDIRECTS) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    raise ReceiptError("Couldn't open that link (page not found).")
                # Re-check the final host after redirects.
                final_host = (urlparse(str(r.url)).hostname or "").lower()
                if final_host != host:
                    _public_host(final_host)
                content_type = (r.headers.get("content-type", "").split(";")[0].strip().lower())
                if content_type not in ALLOWED_TYPES:
                    raise ReceiptError("Link must be an image or PDF receipt.")
                async for chunk in r.aiter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > MAX_RECEIPT_BYTES:
                        raise ReceiptError("That file is too large (max 6 MB).")
    except ReceiptError:
        raise
    except Exception as e:
        logger.warning("Receipt link fetch failed: %s", e)
        raise ReceiptError("Couldn't open that link. Check it and try again.")
    if not data:
        raise ReceiptError("That link was empty.")
    return ALLOWED_TYPES[content_type], bytes(data)


def verify_pdf_receipt(data: bytes, expected_account: str, expected_name: str,
                       amount_needed) -> dict:
    """Match a PDF receipt's text against the expected payment.

    Returns {ok, amount, account, name, reason}. amount/account may be 0/"" when
    unreadable — callers treat ok=False as 'send to owner review'.
    """
    from decimal import Decimal
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "reason": "PDF reading is unavailable right now."}
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 10:
            return {"ok": False, "amount": 0.0, "account": "", "name": "",
                    "reason": "PDF has too many pages."}
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:5])
    except Exception:
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "reason": "Couldn't read that PDF."}
    if not text.strip():
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "reason": "That PDF has no readable text (scanned image?). Send a photo instead."}

    amounts = [float(m.group(1).replace(",", ""))
               for m in re.finditer(r"(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(?:etb|birr|br)\b", text, re.IGNORECASE)]
    plain = [float(x.replace(",", "")) for x in
             re.findall(r"(?<!\d)(\d{3,9}(?:\.\d{1,2})?)(?!\d)", text)]
    candidates = amounts + plain
    got_amount = max(candidates) if candidates else 0.0

    digits = re.findall(r"\d{6,}", text.replace(" ", ""))
    exp_acc = (expected_account or "").replace(" ", "")
    account_ok = bool(exp_acc) and any(exp_acc in d or d in exp_acc for d in digits)
    exp_name = (expected_name or "").strip().lower()
    name_ok = bool(exp_name) and exp_name.split()[0] in text.lower() if exp_name else True

    need = float(amount_needed or 0)
    amount_ok = need <= 0 or got_amount >= need - 1.0
    if amount_ok and account_ok and name_ok:
        return {"ok": True, "amount": got_amount,
                "account": digits[0] if digits else "", "name": exp_name, "reason": ""}
    reasons = []
    if not amount_ok:
        reasons.append(f"expected ~{need:.0f} ETB, found {got_amount:.0f}")
    if not account_ok:
        reasons.append("account number not found")
    return {"ok": False, "amount": got_amount,
            "account": digits[0] if digits else "", "name": "",
            "reason": "; ".join(reasons) or "details unclear"}


# ─── Bank receipt links (ethiobank-receipts, authoritative) ───────────────

BANK_URL_HINTS = (
    ("cbe", ("cbe.com.et",)),
    ("dashen", ("dashensuperapp.com", "dashenbank", "dashen")),
    ("awash", ("awashbank.com", "awashpay")),
    ("boa", ("bankofabyssinia.com",)),
    ("zemen", ("zemenbank.com",)),
    ("tele", ("ethiotelecom.et", "transactioninfo", "telebirr")),
)
# Bare Telebirr receipt IDs customers paste without a link, e.g. CHQ0FJ403O.
TELE_ID_RE = re.compile(r"^[A-Z0-9]{8,14}$")


def detect_bank_link(text: str) -> tuple | None:
    """Detect a bank receipt share link (or bare Telebirr ID).

    Returns (bank, key) for extract_receipt(), else None.
    """
    t = (text or "").strip()
    if not t or " " in t:
        return None
    low = t.lower()
    if not t.startswith(("http://", "https://")):
        if low.startswith("ft") and re.match(r"^ft[0-9a-z]+$", low):
            return ("cbe_ft", t.upper())
        if TELE_ID_RE.match(t):
            return ("tele", t)
        return None
    host = (urlparse(t).hostname or "").lower()
    for bank, hints in BANK_URL_HINTS:
        if any(h in host for h in hints):
            return (bank, t)
    return None


def normalize_bank_result(bank: str, data: dict) -> dict:
    """Map ethiobank-receipts output -> {ok, amount, account, name, ref, reason}."""
    if not isinstance(data, dict):
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "ref": "", "reason": "Bank lookup returned nothing."}
    if (data.get("status") or "").upper() not in ("SUCCESS", "COMPLETED", "PAID"):
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "ref": str(data.get("reference") or ""),
                "reason": "Bank shows this receipt as not successful."}
    try:
        amount = float(data.get("amount") or 0)
    except (ValueError, TypeError):
        amount = 0.0
    return {"ok": amount > 0,
            "amount": amount,
            "account": str(data.get("receiver_account") or ""),
            "name": str(data.get("receiver_name") or ""),
            "ref": str(data.get("reference") or ""),
            "reason": "" if amount > 0 else "Bank receipt has no amount."}


async def extract_bank_receipt(bank: str, key: str, account_hint: str = "") -> dict:
    """Verify via the bank itself. Never raises — returns ok=False verdicts.

    Runs the sync scraper off the event loop with a hard timeout. BOA needs
    Chrome WebDriver and Telebirr often blocks foreign IPs; both surface as
    clean failures the caller turns into photo-fallback guidance.
    """
    import asyncio as _aio

    if bank == "cbe_ft" and len(re.sub(r"\D", "", account_hint or "")) < 8:
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "ref": key, "reason": "CBE needs the full receipt link."}

    def _run():
        try:
            from ethiobank_receipts import extract_receipt
        except ImportError:
            return {"ok": False, "amount": 0.0, "account": "", "name": "",
                    "ref": "", "reason": "Bank verification is unavailable right now."}
        try:
            if bank == "cbe_ft":
                digits = re.sub(r"\D", "", account_hint or "")
                from ethiobank_receipts.extractors.cbe import extract_cbe_receipt_info_from_ft
                data = extract_cbe_receipt_info_from_ft(key, digits[-8:])
                return normalize_bank_result("cbe", data)
            return normalize_bank_result(bank, extract_receipt(bank, key))
        except ValueError as e:
            return {"ok": False, "amount": 0.0, "account": "", "name": "",
                    "ref": "", "reason": f"Bank rejected the reference ({e})."}
        except Exception as e:
            logger.warning("Bank receipt lookup failed (%s): %s", bank, e)
            return {"ok": False, "amount": 0.0, "account": "", "name": "",
                    "ref": "", "reason": "Couldn't reach the bank. Send a photo of the receipt instead."}

    try:
        return await _aio.wait_for(_aio.to_thread(_run), timeout=45)
    except Exception as e:
        logger.warning("Bank receipt lookup timed out (%s): %s", bank, e)
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "ref": "", "reason": "Bank lookup timed out. Send a photo of the receipt instead."}
