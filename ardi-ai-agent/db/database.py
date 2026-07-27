import logging
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, select

from config import DATABASE_URL

logger = logging.getLogger(__name__)


class _SessionFactory:
    """Creates one engine per event loop — safe for bot + uvicorn (different loops)."""
    def __init__(self):
        self._engines: dict[int, any] = {}
        self._makers: dict[int, any] = {}

    def __call__(self):
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        if loop_id not in self._engines:
            self._engines[loop_id] = create_async_engine(
                DATABASE_URL,
                echo=False,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            self._makers[loop_id] = async_sessionmaker(
                self._engines[loop_id], class_=AsyncSession, expire_on_commit=False
            )
        return self._makers[loop_id]()

    @property
    def engine(self):
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        if loop_id not in self._engines:
            self._engines[loop_id] = create_async_engine(
                DATABASE_URL,
                echo=False,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            self._makers[loop_id] = async_sessionmaker(
                self._engines[loop_id], class_=AsyncSession, expire_on_commit=False
            )
        return self._engines[loop_id]


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
                table_col = sql.split("ADD COLUMN")[1].strip().split(" ")[0]
                logger.info("Ran migration: %s", table_col)
            except Exception as e:
                if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                    logger.debug("Column already exists: %s", sql.split()[2])
                else:
                    logger.warning("Migration warning: %s", e)

    # Seed default payment methods if empty
    from db.models import PaymentMethod
    async with async_session() as seed_session:
        existing = await seed_session.execute(select(PaymentMethod).limit(1))
        if not existing.first():
            from config import CBE_ACCOUNT_NAME, CBE_ACCOUNT_NUMBER, TELEBIRR_ACCOUNT_NAME, TELEBIRR_ACCOUNT_NUMBER
            seed_session.add_all([
                PaymentMethod(name="cbe", bank_name="CBE", account_name=CBE_ACCOUNT_NAME, account_number=str(CBE_ACCOUNT_NUMBER), is_active=True),
                PaymentMethod(name="telebirr", bank_name="Telebirr", account_name=TELEBIRR_ACCOUNT_NAME, account_number=str(TELEBIRR_ACCOUNT_NUMBER), is_active=True),
            ])
            await seed_session.commit()
            logger.info("Seeded default payment methods")
