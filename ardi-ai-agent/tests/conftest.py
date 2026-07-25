import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["TELEGRAM_TOKEN"] = "123:fake"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_ardi.db"
os.environ["ADMIN_TELEGRAM_ID"] = "99999"

from sqlalchemy import select
from db.database import async_session, init_db, engine, Base
from db.models import Business, User, Order, OrderItem, _utcnow


@pytest.fixture(scope="session", autouse=True)
async def global_db():
    await init_db()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session() as s:
        for table in (OrderItem, Order, User, Business):
            rows = await s.execute(select(table))
            for r in rows.scalars():
                await s.delete(r)
        await s.commit()
