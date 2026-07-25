import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["TELEGRAM_TOKEN"] = "test:token"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

from bot.handlers import (
    _validate_text,
    BUSINESS_HOURS_WARNING,
)


class TestValidateText:
    def test_short_message(self):
        assert _validate_text("hello") is None

    def test_2000_chars(self):
        assert _validate_text("x" * 2000) is None

    def test_too_long(self):
        result = _validate_text("x" * 2001)
        assert result is not None
        assert "long" in result.lower()
