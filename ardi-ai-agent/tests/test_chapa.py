"""Tests for Chapa payment helpers (no network — pure logic only)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import chapa
from db.settings import get_plan_prices, set_setting


class TestPlanPrices:
    async def test_defaults(self):
        prices = await get_plan_prices()
        assert prices["monthly"] >= 1
        assert prices["yearly"] >= 1

    async def test_admin_override_roundtrip(self):
        await set_setting("plan.monthly", "1500")
        try:
            prices = await get_plan_prices()
            assert prices["monthly"] == 1500
        finally:
            await set_setting("plan.monthly", "1200")

    async def test_bad_value_falls_back(self, monkeypatch):
        import db.settings as st
        from config import SUBSCRIPTION_MONTHLY

        async def fake_get(key, default=""):
            return "not-a-number"

        monkeypatch.setattr(st, "get_setting", fake_get)
        prices = await get_plan_prices()
        assert prices["monthly"] == SUBSCRIPTION_MONTHLY


class TestTxRef:
    def test_format(self):
        ref = chapa.make_tx_ref(42, "monthly")
        assert ref.startswith("ardi-monthly-42-")
        assert len(ref) < 64  # fits Telegram callback_data if ever needed

    def test_unique(self):
        assert chapa.make_tx_ref(1, "monthly") != chapa.make_tx_ref(1, "monthly")

    def test_label_sanitized(self):
        ref = chapa.make_tx_ref(1, "a/b c")
        assert "/" not in ref and " " not in ref


class TestParseVerify:
    def test_success(self):
        out = chapa.parse_verify_response({
            "message": "Payment details",
            "data": {"status": "success", "amount": "1200.00", "reference": "chapa-xyz"},
        })
        assert out["paid"] is True
        assert out["amount"] == 1200.00
        assert out["chapa_ref"] == "chapa-xyz"

    def test_failed_status(self):
        out = chapa.parse_verify_response({
            "message": "Payment details",
            "data": {"status": "failed", "amount": "1200.00"},
        })
        assert out["paid"] is False

    def test_malformed(self):
        assert chapa.parse_verify_response({})["paid"] is False
        assert chapa.parse_verify_response(None)["paid"] is False
        assert chapa.parse_verify_response({"message": "No transaction"})["paid"] is False


class TestConfigured:
    def test_missing_key_not_configured(self, monkeypatch):
        monkeypatch.setattr(chapa, "CHAPA_SECRET_KEY", "")
        assert chapa.chapa_configured() is False
