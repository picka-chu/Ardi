"""Tests for receipt intake helpers (no network except blocked-fast paths)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

from receipts import looks_like_url, fetch_receipt_url, verify_pdf_receipt, ReceiptError
from receipts import detect_bank_link, normalize_bank_result, extract_bank_receipt
import bot.handlers as h


class TestLooksLikeUrl:
    def test_full_url(self):
        assert looks_like_url("https://example.com/r.png") == "https://example.com/r.png"

    def test_bare_domain(self):
        assert looks_like_url("example.com/r.png") == "https://example.com/r.png"

    def test_rejects_text(self):
        assert looks_like_url("hello there") is None
        assert looks_like_url("not a link!") is None
        assert looks_like_url("") is None


class TestFetchGuards:
    async def test_rejects_http(self):
        with pytest.raises(ReceiptError):
            await fetch_receipt_url("http://example.com/r.png")

    async def test_rejects_userinfo(self):
        with pytest.raises(ReceiptError):
            await fetch_receipt_url("https://user:pass@example.com/r.png")

    async def test_rejects_localhost(self):
        with pytest.raises(ReceiptError):
            await fetch_receipt_url("https://localhost/r.png")

    async def test_rejects_private_ip(self):
        with pytest.raises(ReceiptError):
            await fetch_receipt_url("https://192.168.1.5/r.png")

    async def test_rejects_bad_port(self):
        with pytest.raises(ReceiptError):
            await fetch_receipt_url("https://example.com:8443/r.png")


class TestPdfVerify:
    def test_garbage_not_pdf(self):
        out = verify_pdf_receipt(b"not a pdf at all", "123", "shop", 100)
        assert out["ok"] is False

    def test_blank_pdf_no_text(self):
        from pypdf import PdfWriter
        import io
        buf = io.BytesIO()
        w = PdfWriter()
        w.add_blank_page(200, 200)
        w.write(buf)
        out = verify_pdf_receipt(buf.getvalue(), "123", "shop", 100)
        assert out["ok"] is False
        assert "photo" in out["reason"]


class TestMatchReceipt:
    def _biz(self):
        return SimpleNamespace(order_bank_account="1000602869893", order_account_holder="Bereket Tesfalem")

    def test_match(self):
        ok, issues = h._match_order_receipt(self._biz(), 1200, 1200.0, "1000602869893", "bereket tesfalem")
        assert ok is True and issues == []

    def test_amount_short(self):
        ok, issues = h._match_order_receipt(self._biz(), 1200, 500.0, "1000602869893", "bereket")
        assert ok is False and any("ETB" in i for i in issues)

    def test_wrong_account(self):
        ok, issues = h._match_order_receipt(self._biz(), 1200, 1200.0, "999999", "someone else")
        assert ok is False

    def test_empty_expected_never_matches(self):
        biz = SimpleNamespace(order_bank_account="", order_account_holder="")
        ok, _ = h._match_order_receipt(biz, 1200, 1200.0, "anything", "anyone")
        assert ok is False


class TestDetectBankLink:
    def test_cbe_url(self):
        assert detect_bank_link("https://apps.cbe.com.et:100/?id=FT25211G11JQ21827223")[0] == "cbe"

    def test_dashen_url(self):
        assert detect_bank_link("https://receipt.dashensuperapp.com/receipt/387ETAP2522000WK")[0] == "dashen"

    def test_telebirr_url(self):
        assert detect_bank_link("https://transactioninfo.ethiotelecom.et/receipt/CHQ0FJ403O")[0] == "tele"

    def test_bare_tele_id(self):
        assert detect_bank_link("CHQ0FJ403O") == ("tele", "CHQ0FJ403O")

    def test_bare_ft(self):
        bank, key = detect_bank_link("FT25211G11JQ")
        assert bank == "cbe_ft" and key == "FT25211G11JQ"

    def test_generic_link_ignored(self):
        assert detect_bank_link("https://example.com/receipt.png") is None

    def test_text_ignored(self):
        assert detect_bank_link("hello there friend") is None


class TestNormalizeBankResult:
    def test_cbe_success(self):
        out = normalize_bank_result("cbe", {
            "receiver": "XYZ Trading PLC", "receiver_account": "1000556677",
            "transferred_amount": "1,250.75", "reference_no": "FT25211G11JQ",
        })
        assert out == {"ok": True, "amount": 1250.75, "account": "1000556677",
                       "name": "XYZ Trading PLC", "ref": "FT25211G11JQ", "reason": ""}

    def test_tele_success(self):
        out = normalize_bank_result("tele", {
            "credited_party": "ABC Market", "credited_party_number": "0930529985",
            "total_paid": "500.00", "status": "SUCCESS",
            "reference": "CHQ0FJ403O",
        })
        assert out["ok"] is True and out["amount"] == 500.00

    def test_tele_failed_status(self):
        out = normalize_bank_result("tele", {"status": "FAILED", "total_paid": "100"})
        assert out["ok"] is False

    def test_dashen_success(self):
        out = normalize_bank_result("dashen", {
            "beneficiary_name": "FastPay", "beneficiary_account": "2000556677",
            "amount": "750.25 ETB", "transfer_reference": "387ETAP",
        })
        assert out["ok"] is True and out["account"] == "2000556677"

    def test_zemen_prefixed_amount(self):
        out = normalize_bank_result("zemen", {
            "Recipient name": "Shop", "Recipient Account No": "5000112233",
            "Total Amount Paid": "ETB 2,000.00", "Reference No": "ZM1",
        })
        assert out["ok"] is True and out["amount"] == 2000.00

    def test_malformed(self):
        assert normalize_bank_result("cbe", None)["ok"] is False
        assert normalize_bank_result("cbe", {})["ok"] is False
        assert normalize_bank_result("weird", {"amount": 5})["ok"] is False


class TestExtractBankReceipt:
    async def test_cbe_ft_needs_full_link_without_account(self):
        out = await extract_bank_receipt("cbe_ft", "FT25211G11JQ", "")
        assert out["ok"] is False
        assert "full receipt link" in out["reason"]
