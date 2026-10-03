"""Tests for secret handling + AI cost cap helpers (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from db.crypto import encrypt_secret, decrypt_secret
from db.database import async_session
import bot.handlers as h


class TestCrypto:
    def test_empty(self):
        assert encrypt_secret("") == ""
        assert decrypt_secret("") == ""
        assert decrypt_secret(None) == ""

    def test_fernet_roundtrip(self, monkeypatch):
        import db.crypto as c
        monkeypatch.setattr("config.ARDI_MASTER_KEY", "test-passphrase-for-unit-tests-only!!")
        enc = encrypt_secret("CHASECK-test-123")
        assert enc.startswith("fernet:")
        assert decrypt_secret(enc) == "CHASECK-test-123"

    def test_plain_fallback_and_legacy(self, monkeypatch):
        import db.crypto as c
        monkeypatch.setattr("config.ARDI_MASTER_KEY", "")
        enc = encrypt_secret("CHASECK-test-123")
        assert enc == "plain:CHASECK-test-123"
        assert decrypt_secret(enc) == "CHASECK-test-123"
        assert decrypt_secret("CHASECK-raw-legacy") == "CHASECK-raw-legacy"

    def test_wrong_key_fails_closed(self, monkeypatch):
        import db.crypto as c
        monkeypatch.setattr("config.ARDI_MASTER_KEY", "key-one-xxxxxxxxxxxxxxxxxxxxxxxx")
        enc = encrypt_secret("secret")
        monkeypatch.setattr("config.ARDI_MASTER_KEY", "key-two-yyyyyyyyyyyyyyyyyyyyyyyy")
        assert decrypt_secret(enc) == ""


class TestAiCap:
    async def test_disabled_cap(self, monkeypatch):
        import config
        from db.settings import ai_over_cap
        monkeypatch.setattr(config, "MAX_AI_CALLS_PER_SHOP_PER_DAY", 0)
        over, first = await ai_over_cap(999999)
        assert (over, first) == (False, False)

    async def test_counts_and_caps(self, monkeypatch):
        import config
        import datetime
        from db.settings import ai_over_cap, bump_ai_usage, set_setting
        monkeypatch.setattr(config, "MAX_AI_CALLS_PER_SHOP_PER_DAY", 2)
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        await set_setting(f"aiuse:888001:{day}", "0")
        await set_setting(f"aiuse-n:888001:{day}", "")
        assert await bump_ai_usage(888001) == 1
        assert await bump_ai_usage(888001) == 2
        over, first = await ai_over_cap(888001)
        assert (over, first) == (True, True)
        over2, first2 = await ai_over_cap(888001)
        assert (over2, first2) == (True, False)


class TestPauseAndHistory:
    async def test_pause_roundtrip(self):
        assert await h._biz_pause_active(777001, 555) is False
        await h._biz_pause_set(777001, 555, hours=1)
        assert await h._biz_pause_active(777001, 555) is True
        assert await h._biz_pause_active(777001, 556) is False

    async def test_history_roundtrip(self):
        await h._store_biz_history(777002, 556, [
            {"role": "user", "text": "selam"},
            {"role": "assistant", "text": "tenayistilign"},
        ])
        loaded = await h._load_biz_history(777002, 556)
        assert [(m["role"], m["text"]) for m in loaded] == [
            ("user", "selam"), ("assistant", "tenayistilign")]

    async def test_history_capped(self):
        many = [{"role": "user", "text": f"m{i}"} for i in range(30)]
        await h._store_biz_history(777003, 557, many)
        loaded = await h._load_biz_history(777003, 557)
        assert len(loaded) == 20
        assert loaded[0]["text"] == "m10"


class TestStockSale:
    async def _shop(self):
        from db.models import Business, Product
        async with async_session() as s:
            b = Business(telegram_chat_id=93001, name="Stock Shop")
            s.add(b)
            await s.flush()
            p = Product(business_id=b.id, name="Ink", price=100, stock_qty=10)
            s.add(p)
            await s.commit()
            return b.id, p.id

    async def test_decrement_and_low_note(self):
        from db.models import Business, Product, Order, OrderItem
        bid, pid = await self._shop()
        async with async_session() as s:
            o = Order(business_id=bid, customer_name="A", customer_phone="1",
                      customer_address="X", total_price=600, status="pending")
            s.add(o)
            await s.flush()
            s.add(OrderItem(order_id=o.id, product_id=pid, product_name="Ink",
                            quantity=6, unit_price=100))
            await s.commit()
            oid = o.id
        notes = await h._apply_stock_sale(oid)
        assert any("low stock" in n for n in notes)
        async with async_session() as s:
            p = await s.get(Product, pid)
            assert p.stock_qty == 4 and p.available is True

    async def test_soldout_hides(self):
        from db.models import Business, Product, Order, OrderItem
        bid, pid = await self._shop()
        async with async_session() as s:
            o = Order(business_id=bid, customer_name="A", customer_phone="1",
                      customer_address="X", total_price=1000, status="pending")
            s.add(o)
            await s.flush()
            s.add(OrderItem(order_id=o.id, product_id=pid, product_name="Ink",
                            quantity=10, unit_price=100))
            await s.commit()
            oid = o.id
        notes = await h._apply_stock_sale(oid)
        assert any("SOLD OUT" in n for n in notes)
        async with async_session() as s:
            p = await s.get(Product, pid)
            assert p.stock_qty == 0 and p.available is False


class TestOrderValidation:
    def test_valid(self):
        from ai.validation import validate_order_data
        m = validate_order_data({"customer_name": "A", "customer_phone": "1",
                                 "customer_address": "X",
                                 "items": [{"product": "Tea", "quantity": "2"}]})
        assert m.items[0].quantity == 2

    def test_qty_clamped(self):
        from ai.validation import validate_order_data
        m = validate_order_data({"customer_name": "A", "customer_phone": "1",
                                 "customer_address": "X",
                                 "items": [{"product": "Tea", "quantity": 999}]})
        assert m.items[0].quantity == 50

    def test_rejects(self):
        import pytest
        from ai.validation import validate_order_data, resolve_catalog_item
        with pytest.raises(ValueError):
            validate_order_data({"items": []})
        assert resolve_catalog_item("nope", []) is None
