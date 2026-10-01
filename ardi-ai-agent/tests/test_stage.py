"""Tests for payment-stage tracking: invoice lines, word detection, customer
memory, and DB-backed pending resolution (restart/cross-chat safe)."""
import os
import sys
from decimal import Decimal
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot.handlers as h
from db.database import async_session
from db.models import Business, Order, OrderItem


def _prod(name, price):
    return SimpleNamespace(name=name, price=price)


class TestInvoiceLines:
    def test_totals(self):
        total, lines = h._invoice_lines(
            {"items": [{"product": "Bread", "quantity": 2}]},
            [_prod("Bread", 45)])
        assert total == Decimal("90")
        assert len(lines) == 1 and "90.00" in lines[0]

    def test_unknown_product_skipped(self):
        total, lines = h._invoice_lines(
            {"items": [{"product": "Ghost", "quantity": 1}]}, [_prod("Bread", 45)])
        assert total == Decimal("0.00") and lines == []


class TestStageWords:
    def test_paid(self):
        assert h._is_paid_word("paid") is True
        assert h._is_paid_word("I've paid!") is True
        assert h._is_paid_word("ከፍያለሁ") is True
        assert h._is_paid_word("hello") is False

    def test_cancel(self):
        assert h._is_cancel_word("cancel") is True
        assert h._is_cancel_word("Cancel.") is True
        assert h._is_cancel_word("ሰርዝ") is True
        assert h._is_cancel_word("cancel my order please") is False


class TestCustomerMemory:
    async def test_save_and_prefill(self):
        async with async_session() as s:
            b = Business(telegram_chat_id=91001, name="Mem Shop")
            s.add(b)
            await s.commit()
            bid = b.id
        await h._save_customer_profile(bid, 91002, {
            "customer_name": "Abebe", "customer_phone": "+251900000001",
            "customer_address": "Bole"})
        prof = await h._customer_profile(bid, 91002)
        assert prof == {"customer_name": "Abebe", "customer_phone": "+251900000001",
                        "customer_address": "Bole"}
        # Partial update keeps old fields.
        await h._save_customer_profile(bid, 91002, {"customer_phone": "+251900000009"})
        prof = await h._customer_profile(bid, 91002)
        assert prof["customer_phone"] == "+251900000009"
        assert prof["customer_name"] == "Abebe"

    async def test_unknown_is_empty(self):
        assert await h._customer_profile(424242, 777) == {}


class TestPendingResolution:
    async def _seed_awaiting(self):
        async with async_session() as s:
            b = Business(telegram_chat_id=92001, name="Stage Shop")
            s.add(b)
            await s.flush()
            o = Order(business_id=b.id, customer_telegram_id=92002,
                      customer_name="Tigist", customer_phone="1",
                      customer_address="A", total_price=Decimal("500"),
                      status="awaiting_payment")
            s.add(o)
            await s.flush()
            s.add(OrderItem(order_id=o.id, product_name="Tea", quantity=2,
                            unit_price=Decimal("250")))
            await s.commit()
            return b.id, o.id

    async def test_db_fallback_rebuilds_pending(self):
        bid, oid = await self._seed_awaiting()
        ctx = SimpleNamespace(user_data={})
        found = await h._resolve_pending(ctx, bid, 92002)
        assert found and found["order_id"] == oid
        assert found["data"]["items"] == [{"product": "Tea", "quantity": 2}]
        assert ctx.user_data["state"] == "awaiting_order_payment"

    async def test_memory_wins(self):
        ctx = SimpleNamespace(user_data={
            "state": "awaiting_order_payment",
            "pending_order": {"business_id": 1, "sentinel": True}})
        found = await h._resolve_pending(ctx, 1, 999)
        assert found.get("sentinel") is True

    async def test_wrong_business_ignored(self):
        bid, _ = await self._seed_awaiting()
        ctx = SimpleNamespace(user_data={})
        assert await h._resolve_pending(ctx, bid + 9999, 92002) is None

    async def test_paid_orders_not_pending(self):
        bid, oid = await self._seed_awaiting()
        async with async_session() as s:
            o = await s.get(Order, oid)
            o.status = "pending"
            await s.commit()
        ctx = SimpleNamespace(user_data={})
        assert await h._resolve_pending(ctx, bid, 92002) is None
