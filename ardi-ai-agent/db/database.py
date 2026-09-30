import logging
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, select

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Render/Supabase dashboards hand out plain `postgresql://` URLs, but this app
# only speaks async. Normalize so a missing `+asyncpg` can't crash startup.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+asyncpg://" + DATABASE_URL[len("postgresql://"):]


def _make_engine(url: str):
    # SQLite (aiosqlite) uses a singleton/queue pool that rejects
    # pool_size / max_overflow / pool_pre_ping — only pass those for server DBs.
    if _is_sqlite(url):
        return create_async_engine(url, echo=False)
    return create_async_engine(
        url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )


class _SessionFactory:
    """Creates one engine per event loop — safe for bot + uvicorn (different loops)."""
    def __init__(self):
        self._engines: dict[int, any] = {}
        self._makers: dict[int, any] = {}

    def _ensure(self, loop_id: int):
        if loop_id not in self._engines:
            self._engines[loop_id] = _make_engine(DATABASE_URL)
            self._makers[loop_id] = async_sessionmaker(
                self._engines[loop_id], class_=AsyncSession, expire_on_commit=False
            )

    def __call__(self):
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        self._ensure(loop_id)
        return self._makers[loop_id]()

    @property
    def engine(self):
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        self._ensure(loop_id)
        return self._engines[loop_id]

    async def dispose_all(self):
        for eng in list(self._engines.values()):
            try:
                await eng.dispose()
            except Exception:
                pass
        self._engines.clear()
        self._makers.clear()


async_session = _SessionFactory()


# Backward compat: `engine` resolves to the current loop's engine at access time
class _EngineProxy:
    def __getattr__(self, name):
        return getattr(async_session.engine, name)

engine = _EngineProxy()


class Base(DeclarativeBase):
    pass


def _is_sqlite(url: str) -> bool:
    return "sqlite" in url


async def init_db():
    engine = async_session.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created/verified")

    async with engine.begin() as conn:
        migration_sql = [
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ai_tone VARCHAR(50) DEFAULT 'friendly'",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS available BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'guest'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_id INTEGER",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_hours_enabled BOOLEAN DEFAULT FALSE",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_hours_start VARCHAR(5)",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS business_hours_end VARCHAR(5)",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ai_offline_message TEXT",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(20) DEFAULT 'trial'",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_start TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_end TIMESTAMPTZ",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(10)",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_end TIMESTAMPTZ",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS orders_enabled BOOLEAN DEFAULT FALSE",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS order_bank_name VARCHAR(100)",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS order_bank_account VARCHAR(100)",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS order_account_holder VARCHAR(255)",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_caption TEXT",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_embedding TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'en'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN DEFAULT FALSE",
        ]
        if not _is_sqlite(DATABASE_URL):
            migration_sql.extend([
                "ALTER TABLE products ALTER COLUMN price TYPE NUMERIC(10,2)",
                "ALTER TABLE orders ALTER COLUMN total_price TYPE NUMERIC(10,2)",
                "ALTER TABLE order_items ALTER COLUMN unit_price TYPE NUMERIC(10,2)",
                # Fix timezone-naive columns (TIMESTAMP → TIMESTAMPTZ) for existing databases
                "ALTER TABLE businesses ALTER COLUMN trial_start TYPE TIMESTAMPTZ USING trial_start AT TIME ZONE 'UTC'",
                "ALTER TABLE businesses ALTER COLUMN trial_end TYPE TIMESTAMPTZ USING trial_end AT TIME ZONE 'UTC'",
                "ALTER TABLE businesses ALTER COLUMN subscription_end TYPE TIMESTAMPTZ USING subscription_end AT TIME ZONE 'UTC'",
            ])
        if _is_sqlite(DATABASE_URL):
            migration_sql = [s.replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN") for s in migration_sql]

        for sql in migration_sql:
            try:
                await conn.execute(text(sql))
                if "ADD COLUMN" in sql:
                    table_col = sql.split("ADD COLUMN")[1].strip().split(" ")[0]
                else:
                    table_col = sql.split()[0:4]
                    table_col = " ".join(table_col)
                logger.info("Ran migration: %s", table_col)
            except Exception as e:
                if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                    logger.debug("Column already exists: %s", sql[:80])
                else:
                    logger.warning("Migration warning: %s", e)

    # Seed default payment methods if empty (idempotent — check per name)
    from db.models import PaymentMethod
    async with async_session() as seed_session:
        existing = await seed_session.execute(select(PaymentMethod))
        existing_names = {p.name for p in existing.scalars().all()}
        from config import CBE_ACCOUNT_NAME, CBE_ACCOUNT_NUMBER, TELEBIRR_ACCOUNT_NAME, TELEBIRR_ACCOUNT_NUMBER
        to_add = []
        if "cbe" not in existing_names:
            to_add.append(PaymentMethod(name="cbe", bank_name="CBE", account_name=CBE_ACCOUNT_NAME, account_number=str(CBE_ACCOUNT_NUMBER), is_active=True))
        if "telebirr" not in existing_names:
            to_add.append(PaymentMethod(name="telebirr", bank_name="Telebirr", account_name=TELEBIRR_ACCOUNT_NAME, account_number=str(TELEBIRR_ACCOUNT_NUMBER), is_active=True))
        if to_add:
            seed_session.add_all(to_add)
            try:
                await seed_session.commit()
                logger.info("Seeded default payment methods")
            except Exception as e:
                await seed_session.rollback()
                logger.warning("Payment method seed skipped: %s", e)

    # Seed default subscription prices (admin can change them later; never overwrites)
    from db.models import AppSetting
    async with async_session() as seed_session:
        try:
            from config import SUBSCRIPTION_MONTHLY, SUBSCRIPTION_YEARLY
            for key, val in (("plan.monthly", str(SUBSCRIPTION_MONTHLY)),
                             ("plan.yearly", str(SUBSCRIPTION_YEARLY))):
                if await seed_session.get(AppSetting, key) is None:
                    seed_session.add(AppSetting(key=key, value=val))
            await seed_session.commit()
        except Exception as e:
            await seed_session.rollback()
            logger.warning("Price seed skipped: %s", e)
