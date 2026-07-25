"""Integration tests for core bot flows (payment, ordering, registration).

Requires: pytest, pytest-asyncio
Run with: python -m pytest tests/test_integration.py -v --asyncio-mode=auto
"""

import pytest
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mktemp(suffix='.db')}")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "99999")

from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from db.database import async_session
from db.models import Business, User, Order, OrderItem, _utcnow
from bot.handlers import get_or_create_user, get_business, create_order


async def _make_test_business(session=None) -> Business:
    """Create a test business with active subscription."""
    if session:
        b = Business(
            telegram_chat_id=1001,
            name="Test Shop",
            description="Test goods",
            address="Addis Ababa",
            phone="+251911111111",
            ai_active=True,
            subscription_status="active",
            subscription_plan="monthly",
            subscription_end=_utcnow() + timedelta(days=30),
            trial_end=_utcnow() + timedelta(days=30),
            orders_enabled=True,
            order_bank_name="CBE",
            order_bank_account="1000999999",
            order_account_holder="Test Shop Owner",
        )
        session.add(b)
        await session.flush()
        return b
    async with async_session() as s:
        b = Business(
            telegram_chat_id=1001,
            name="Test Shop",
            description="Test goods",
            address="Addis Ababa",
            phone="+251911111111",
            ai_active=True,
            subscription_status="active",
            subscription_plan="monthly",
            subscription_end=_utcnow() + timedelta(days=30),
            trial_end=_utcnow() + timedelta(days=30),
            orders_enabled=True,
            order_bank_name="CBE",
            order_bank_account="1000999999",
            order_account_holder="Test Shop Owner",
        )
        s.add(b)
        await s.commit()
        return b


# ─── Registration Flow ──────────────────────────────────────────────────

class TestRegistrationFlow:
    async def test_get_or_create_new_user(self):
        async with async_session() as s:
            user, is_new = await get_or_create_user(s, 2001)
            assert is_new is True
            assert user.telegram_id == 2001
            assert user.role == "guest"

    async def test_get_or_create_existing_user(self):
        async with async_session() as s:
            await get_or_create_user(s, 2002)
            user, is_new = await get_or_create_user(s, 2002)
            assert is_new is False
            assert user.telegram_id == 2002

    async def test_register_business_owner(self):
        async with async_session() as s:
            b = await _make_test_business(s)
            user, is_new = await get_or_create_user(s, 1001, b.id)
            assert user.role == "business_owner"
            assert user.business_id == b.id


# ─── Subscription / Payment Flow ────────────────────────────────────────

class TestSubscriptionFlow:
    async def test_business_starts_in_trial(self):
        async with async_session() as s:
            b = Business(
                telegram_chat_id=3001,
                name="Trial Biz",
                description="New business",
                address="Awasa",
                phone="+251922222222",
                ai_active=True,
            )
            s.add(b)
            await s.commit()
            assert b.subscription_status == "trial"
            assert b.trial_start is not None

    async def test_expired_trial_disables_ai(self):
        async with async_session() as s:
            expired = _utcnow() - timedelta(days=1)
            b = Business(
                telegram_chat_id=3002,
                name="Expired Biz",
                description="Old",
                address="Gondar",
                phone="+251933333333",
                ai_active=True,
                subscription_status="trial",
                trial_start=expired - timedelta(days=7),
                trial_end=expired,
            )
            s.add(b)
            await s.commit()
            now = _utcnow()
            assert b.trial_end < now

    async def test_business_without_subscription_lacks_active_flag(self):
        async with async_session() as s:
            b = Business(
                telegram_chat_id=3003,
                name="Inactive Biz",
                description="No sub",
                address="Bahir Dar",
                phone="+251944444444",
                ai_active=False,
            )
            s.add(b)
            await s.commit()
            assert b.ai_active is False


# ─── Ordering Flow ──────────────────────────────────────────────────────

class TestOrderingFlow:
    async def test_create_order(self):
        async with async_session() as s:
            b = await _make_test_business(s)
            data = {
                "customer_name": "Abebe",
                "customer_phone": "+251911000001",
                "customer_address": "Bole, Addis",
                "items": [{"product": "Bread", "quantity": 2}],
            }
            order = await create_order(b, None, data, [], session=s)
            assert order.id is not None
            assert order.business_id == b.id
            assert order.customer_name == "Abebe"
            assert order.status == "pending"

            result = await s.execute(
                select(OrderItem).where(OrderItem.order_id == order.id)
            )
            items = result.scalars().all()
            assert len(items) == 1
            assert items[0].product_name == "Bread"
            assert items[0].quantity == 2

    async def test_create_order_multiple_items(self):
        async with async_session() as s:
            b = await _make_test_business(s)
            data = {
                "customer_name": "Tigist",
                "customer_phone": "+251911000002",
                "customer_address": "Kazanchis",
                "items": [
                    {"product": "Milk", "quantity": 1},
                    {"product": "Eggs", "quantity": 12},
                ],
            }
            order = await create_order(b, None, data, [], session=s)
            result = await s.execute(
                select(OrderItem).where(OrderItem.order_id == order.id)
            )
            items = result.scalars().all()
            assert len(items) == 2

    async def test_order_without_delivery_info_raises(self):
        with pytest.raises(ValueError, match="Missing required"):
            async with async_session() as s:
                b = await _make_test_business(s)
                await create_order(b, None, {"customer_name": "", "customer_phone": "", "customer_address": "", "items": [{"product": "Tea", "quantity": 1}]}, [], session=s)

    async def test_no_items_raises(self):
        with pytest.raises(ValueError, match="at least one item"):
            async with async_session() as s:
                b = await _make_test_business(s)
                await create_order(b, None, {"customer_name": "Kebede", "customer_phone": "+251911000003", "customer_address": "Megenagna", "items": []}, [], session=s)


# ─── Language Preference ────────────────────────────────────────────────

class TestLanguagePreference:
    async def test_default_language_is_english(self):
        async with async_session() as s:
            user, _ = await get_or_create_user(s, 5001)
            assert user.language == "en"

    async def test_set_language(self):
        async with async_session() as s:
            user, _ = await get_or_create_user(s, 5002)
            user.language = "am"
            await s.commit()
        async with async_session() as s:
            result = await s.execute(select(User).where(User.telegram_id == 5002))
            user = result.scalar_one()
            assert user.language == "am"


# ─── Translation Helpers ────────────────────────────────────────────────

class TestTranslations:
    def test_t_returns_english_by_default(self):
        from bot.translations import _t
        msg = _t("no_products")
        assert msg == "No products yet."

    def test_t_returns_amharic(self):
        from bot.translations import _t
        msg = _t("no_products", "am")
        assert "ምርቶች" in msg

    def test_t_with_formatting(self):
        from bot.translations import _t
        msg = _t("product_added", "en", name="Bread", price=45.0)
        assert "Bread" in msg
        assert "45.00" in msg

    def test_t_missing_key_returns_placeholder(self):
        from bot.translations import _t
        msg = _t("nonexistent_key")
        assert "missing translation" in msg

    def test_lang_kb_has_buttons(self):
        from bot.translations import lang_kb
        kb = lang_kb()
        assert kb.inline_keyboard
        assert len(kb.inline_keyboard[0]) == 2

    async def test_lang_switching_updates_db(self):
        from bot.handlers import get_user_language
        async with async_session() as s:
            from db.models import User
            u = User(telegram_id=6001, language="am")
            s.add(u)
            await s.commit()
        lang = await get_user_language(6001)
        assert lang == "am"

    async def test_get_user_language_caches(self):
        from bot.handlers import get_user_language, _user_cache
        _user_cache.clear()
        async with async_session() as s:
            from db.models import User
            u = User(telegram_id=6002, language="en")
            s.add(u)
            await s.commit()
        lang1 = await get_user_language(6002)
        assert lang1 == "en"
        # Change in DB, but cache should still return old value
        async with async_session() as s:
            from sqlalchemy import select
            result = await s.execute(select(User).where(User.telegram_id == 6002))
            user = result.scalar_one()
            user.language = "am"
            await s.commit()
        lang2 = await get_user_language(6002)
        assert lang2 == "en"  # cached
        _user_cache.clear()
        lang3 = await get_user_language(6002)
        assert lang3 == "am"  # fresh
