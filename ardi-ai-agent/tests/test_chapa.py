"""Tests for Chapa payment helpers (no network — pure logic only)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import chapa


class TestPlanAmount:
    def test_monthly(self):
        assert chapa.plan_amount("monthly") == 1200

    def test_yearly(self):
        assert chapa.plan_amount("yearly") == 12000

    def test_invalid(self):
        assert chapa.plan_amount("weekly") is None
        assert chapa.plan_amount("") is None


class TestTxRef:
    def test_format(self):
        ref = chapa.make_tx_ref(42, "monthly")
        assert ref.startswith("ardi-sub-42-monthly-")
        assert len(ref) < 64  # fits Telegram callback_data if ever needed

    def test_unique(self):
        assert chapa.make_tx_ref(1, "monthly") != chapa.make_tx_ref(1, "monthly")


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
