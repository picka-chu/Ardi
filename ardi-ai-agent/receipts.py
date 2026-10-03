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
import asyncio
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


def _check_hop(url: str) -> str:
    """Validate one URL before any request touches it. Returns the URL.

    Every redirect hop goes through this, so a malicious hop can never be
    fetched — unlike check-after-fetch designs with a DNS-rebinding window.
    """
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
    return url


async def fetch_receipt_url(url: str) -> tuple[str, bytes]:
    """Download a receipt link. Returns (kind, bytes) where kind is image|pdf."""
    current = _check_hop((url or "").strip())

    data = bytearray()
    content_type = ""
    try:
        # No automatic redirects: each hop is validated by _check_hop first.
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        nxt = r.headers.get("location", "")
                        if not nxt:
                            raise ReceiptError("Couldn't open that link (bad redirect).")
                        # Resolve relative redirects against the current URL.
                        if nxt.startswith("/"):
                            base = urlparse(current)
                            nxt = f"{base.scheme}://{base.netloc}{nxt}"
                        current = _check_hop(nxt)
                        continue
                    if r.status_code != 200:
                        raise ReceiptError("Couldn't open that link (page not found).")
                    content_type = (r.headers.get("content-type", "").split(";")[0].strip().lower())
                    if content_type not in ALLOWED_TYPES:
                        raise ReceiptError("Link must be an image or PDF receipt.")
                    async for chunk in r.aiter_bytes(65536):
                        data.extend(chunk)
                        if len(data) > MAX_RECEIPT_BYTES:
                            raise ReceiptError("That file is too large (max 6 MB).")
                    break
            else:
                raise ReceiptError("That link redirects too many times.")
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


# ─── Fast image prep (downscale for AI + uploads) ──────────────────────────

def _prep_image_sync(data: bytes, max_dim: int) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    s = min(1.0, max_dim / max(w, h))
    if s < 1.0:
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue()


async def prep_image(data: bytes, max_dim: int = 1568) -> bytes:
    """Downscale + JPEG-encode (Telegram photos can be 10MB+; AI bills pixels).

    Never raises — returns the original bytes on any failure.
    """
    if not data:
        return data
    try:
        out = await asyncio.to_thread(_prep_image_sync, data, max_dim)
        logger.debug("image prep %dKB -> %dKB", len(data) // 1024, len(out) // 1024)
        return out
    except Exception:
        return data


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


def _parse_amount(raw) -> float:
    """Parse 'ETB 1,250.00', '750.25 ETB', 1200 -> float (0.0 on garbage)."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        try:
            return float(raw)
        except (ValueError, TypeError):
            return 0.0
    s = re.sub(r"[^0-9.,]", "", str(raw)).replace(",", "")
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


# Per-bank field maps: (amount_keys, account_keys, name_keys, ref_keys).
# Verified against the library's extractor sources — each bank returns
# different names and most return NO status field at all.
BANK_FIELDS = {
    "cbe": (("transferred_amount", "total_debited"), ("receiver_account",),
            ("receiver",), ("reference_no",)),
    "dashen": (("amount", "total"), ("beneficiary_account",),
               ("beneficiary_name",), ("transfer_reference", "transaction_reference")),
    "awash": (("Amount",), ("Beneficiary Account",),
              ("Beneficiary name",), ("Transaction ID",)),
    "boa": (("Transferred Amount", "Total Amount"), ("Receiver's Account",),
            ("Receiver's Name",), ("Transaction Reference",)),
    "zemen": (("Settled Amount", "Total Amount Paid"), ("Recipient Account No",),
              ("Recipient Name",), ("Reference No", "Invoice No")),
    "tele": (("total_paid",), ("credited_party_number",),
             ("credited_party",), ()),
}
TELE_OK = ("success", "successful", "paid", "completed", "approved")


def _first(data: dict, keys: tuple) -> str:
    for k in keys:
        v = data.get(k)
        if v:
            return str(v).strip()
    return ""


def normalize_bank_result(bank: str, data: dict) -> dict:
    """Map ethiobank-receipts output -> {ok, amount, account, name, ref, reason}."""
    if not isinstance(data, dict):
        return {"ok": False, "amount": 0.0, "account": "", "name": "",
                "ref": "", "reason": "Bank lookup returned nothing."}
    fields = BANK_FIELDS.get(bank, ((), (), (), ()))
    amount = _parse_amount(_first(data, fields[0]))
    account = _first(data, fields[1])
    name = _first(data, fields[2])
    ref = _first(data, fields[3])
    if bank == "tele":
        status = str(data.get("status") or "").lower()
        if not any(w in status for w in TELE_OK):
            return {"ok": False, "amount": amount, "account": account, "name": name,
                    "ref": ref, "reason": "Bank shows this receipt as not successful."}
    if amount <= 0 or not account:
        missing = []
        if amount <= 0:
            missing.append("no amount")
        if not account:
            missing.append("no receiver account")
        return {"ok": False, "amount": amount, "account": account, "name": name,
                "ref": ref, "reason": "Bank receipt incomplete (%s)." % ", ".join(missing)}
    return {"ok": True, "amount": amount, "account": account, "name": name,
            "ref": ref, "reason": ""}


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
        except Exception as e:
            logger.warning("ethiobank-receipts unavailable: %s", e)
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
