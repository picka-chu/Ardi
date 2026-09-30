"""Tests for channel connect/scrape helpers + mini app URL normalization (no network)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import bot.handlers as h


class TestMiniappBase:
    def test_strips_business_suffix(self, monkeypatch):
        monkeypatch.setattr(config, "MINI_APP_URL", "https://ardi.onrender.com/business")
        assert h._miniapp_base() == "https://ardi.onrender.com"
        assert h._miniapp_url("/business") == "https://ardi.onrender.com/business"

    def test_bare_url(self, monkeypatch):
        monkeypatch.setattr(config, "MINI_APP_URL", "https://ardi.onrender.com/")
        assert h._miniapp_base() == "https://ardi.onrender.com"
        assert h._miniapp_url("/") == "https://ardi.onrender.com/"

    def test_empty(self, monkeypatch):
        monkeypatch.setattr(config, "MINI_APP_URL", "")
        assert h._miniapp_base() == ""
        assert h._miniapp_url("/business") == ""

    def test_http_upgraded(self, monkeypatch):
        monkeypatch.setattr(config, "MINI_APP_URL", "http://ardi.onrender.com")
        assert h._miniapp_base() == "https://ardi.onrender.com"


class TestImportChannelPhoto:
    async def test_no_price_no_import(self, monkeypatch):
        called = []
        monkeypatch.setattr(h, "identify_product", lambda *a, **k: called.append(1) or {"name": "x"})
        photo = SimpleNamespace(get_file=lambda: None, file_id="f1")
        assert await h._import_channel_photo(1, photo, "nice shoes, DM me") is None
        assert called == []

    async def test_empty_caption_no_import(self):
        photo = SimpleNamespace(get_file=lambda: None, file_id="f1")
        assert await h._import_channel_photo(1, photo, "") is None
