import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _is_sqlite(url: str) -> bool:
    return "sqlite" in url


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created/verified")

    # Run raw migrations outside the create_all transaction
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
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_end TIMESTAMP",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(10)",
            "ALTER TABLE businesses ADD COLUMN IF NOT EXISTS subscription_end TIMESTAMP",
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
            ])
        if _is_sqlite(DATABASE_URL):
            # SQLite doesn't support IF NOT EXISTS for columns
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
