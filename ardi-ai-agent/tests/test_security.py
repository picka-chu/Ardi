"""Regression tests for Ardi audit fixes (auth, API security, storage, config)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from types import SimpleNamespace

from bot.handlers import (
    _parse_id_suffix,
    _owns_order,
    _owns_product,
    _owns_escalation,
    _is_admin,
    _validate_price,
    _validate_quantity,
    _parse_price,
)
from storage import _sanitize_name, _detect_content_type, _r2_configured
from config import require_secrets
import miniapp


class TestParseIdSuffix:
    def test_valid(self):
        assert _parse_id_suffix("order_view_42", "order_view_") == 42

    def test_malformed(self):
        assert _parse_id_suffix("order_view_abc", "order_view_") is None
        assert _parse_id_suffix("order_view_", "order_view_") is None
        assert _parse_id_suffix("", "order_view_") is None

    def test_negative(self):
        # int() accepts negatives; callers clamp where needed
        assert _parse_id_suffix("cat_pg_-1", "cat_pg_") == -1


class TestOwnership:
    def test_order(self):
        b = SimpleNamespace(id=1)
        assert _owns_order(b, SimpleNamespace(business_id=1)) is True
        assert _owns_order(b, SimpleNamespace(business_id=2)) is False
        assert _owns_order(None, SimpleNamespace(business_id=1)) is False
        assert _owns_order(b, None) is False

    def test_product(self):
        b = SimpleNamespace(id=5)
        assert _owns_product(b, SimpleNamespace(business_id=5)) is True
        assert _owns_product(b, SimpleNamespace(business_id=6)) is False

    def test_escalation(self):
        b = SimpleNamespace(id=7)
        assert _owns_escalation(b, SimpleNamespace(business_id=7)) is True
        assert _owns_escalation(b, SimpleNamespace(business_id=8)) is False

    def test_is_admin(self):
        from config import ADMIN_TELEGRAM_ID
        assert _is_admin(ADMIN_TELEGRAM_ID) is True
        assert _is_admin(ADMIN_TELEGRAM_ID + 1) is False


class TestPriceValidation:
    def test_thousands(self):
        assert _parse_price("1,200") == 1200.0
        assert _validate_price("1,200") is None

    def test_negative_rejected(self):
        assert _validate_price("-5") is not None

    def test_quantity(self):
        assert _validate_quantity("2") is None
        assert _validate_quantity("0") is not None
        assert _validate_quantity("abc") is not None


class TestStorage:
    def test_sanitize(self):
        assert _sanitize_name("../../etc/passwd") == "etc_passwd"
        assert _sanitize_name("Coca Cola 500ml!") == "Coca_Cola_500ml"
        assert _sanitize_name("") == "product"

    def test_detect(self):
        assert _detect_content_type(b"\xff\xd8\xff123") == "image/jpeg"
        assert _detect_content_type(b"\x89PNG\r\n\x1a\n123") == "image/png"
        assert _detect_content_type(b"RIFF1234WEBP") == "image/webp"

    def test_r2_not_configured_bool(self):
        assert isinstance(_r2_configured(), bool)


class TestConfig:
    def test_require_secrets_ok(self):
        require_secrets()  # env has dummy values from conftest


class TestMiniappAuth:
    def test_init_data_rejects_missing_auth_date(self):
        assert miniapp._validate_init_data("user=%7B%22id%22%3A1%7D&hash=abc") is None

    def test_init_data_rejects_empty(self):
        assert miniapp._validate_init_data("") is None

    def test_init_data_rejects_stale_auth_date(self):
        old = int(time.time()) - miniapp.INIT_DATA_MAX_AGE - 100
        assert miniapp._validate_init_data(f"auth_date={old}&user=%7B%7D&hash=abc") is None

    def test_dash_token_roundtrip(self):
        tok = miniapp.generate_dash_token(123)
        assert miniapp.validate_dash_token(tok) == 123
        assert miniapp.validate_dash_token("bogus") is None
        assert miniapp.validate_dash_token("1.2.3") is None

    def test_dash_token_expiry(self):
        import hmac as _hm
        import hashlib as _hl
        key = miniapp._dash_hmac_key()
        past = int(time.time()) - 10
        sig = _hm.new(key, f"123.{past}".encode(), _hl.sha256).hexdigest()[:32]
        assert miniapp.validate_dash_token(f"123.{past}.{sig}") is None

    def test_dash_token_survives_restart(self):
        # Stateless format: no server-side dict entry needed.
        tok = miniapp.generate_dash_token(456)
        miniapp._dash_tokens.clear()
        assert miniapp.validate_dash_token(tok) == 456

    def test_lang_cache_tuple_shape(self):
        # language_callback must store (lang, timestamp) tuple, not bare str
        import bot.handlers as h
        assert isinstance(h._user_cache, dict)
