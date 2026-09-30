"""Tests for receipt intake helpers (no network except blocked-fast paths)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

from receipts import looks_like_url, fetch_receipt_url, verify_pdf_receipt, ReceiptError
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
