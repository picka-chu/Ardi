import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text as sql_text

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


def _column_exists(sync_conn, table: str, column: str) -> bool:
    try:
        if _is_sqlite(DATABASE_URL):
            rows = sync_conn.execute(sql_text(f"PRAGMA table_info({table})")).fetchall()
            return any(row[1] == column for row in rows)
        rows = sync_conn.execute(sql_text(
            "SELECT column_name FROM information_schema.columns WHERE table_name=:t AND column_name=:c"
        ), {"t": table, "c": column}).fetchall()
        return len(rows) > 0
    except Exception:
        return False


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _migrate(sync_conn):
            migrations = [
                ("businesses", "ai_tone", "VARCHAR(50) DEFAULT 'friendly'"),
                ("products", "available", "BOOLEAN NOT NULL DEFAULT TRUE"),
                ("users", "role", "VARCHAR(50) DEFAULT 'guest'"),
                ("users", "business_id", "INTEGER"),
                ("businesses", "business_hours_enabled", "BOOLEAN DEFAULT FALSE"),
                ("businesses", "business_hours_start", "VARCHAR(5)"),
                ("businesses", "business_hours_end", "VARCHAR(5)"),
                ("businesses", "ai_offline_message", "TEXT"),
                ("businesses", "subscription_status", "VARCHAR(20) DEFAULT 'trial'"),
                ("businesses", "trial_start", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
                ("businesses", "trial_end", "TIMESTAMP"),
                ("businesses", "subscription_plan", "VARCHAR(10)"),
                ("businesses", "subscription_end", "TIMESTAMP"),
                ("businesses", "orders_enabled", "BOOLEAN DEFAULT FALSE"),
                ("businesses", "order_bank_name", "VARCHAR(100)"),
                ("businesses", "order_bank_account", "VARCHAR(100)"),
                ("businesses", "order_account_holder", "VARCHAR(255)"),
            ]
            for table, column, col_type in migrations:
                if _column_exists(sync_conn, table, column):
                    continue
                try:
                    sync_conn.execute(sql_text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                    ))
                    logger.info("Added column %s.%s", table, column)
                except Exception as e:
                    logger.warning("Could not add column %s.%s: %s", table, column, e)

            product_migrations = [
                ("products", "photo_caption", "TEXT"),
                ("products", "photo_embedding", "TEXT"),
            ]

            user_migrations = [
                ("users", "language", "VARCHAR(10) DEFAULT 'en'"),
            ]

            all_product_migrations = product_migrations + user_migrations + [
                ("users", "is_super_admin", "BOOLEAN DEFAULT FALSE"),
            ]
            for table, column, col_type in all_product_migrations:
                if _column_exists(sync_conn, table, column):
                    continue
                try:
                    sync_conn.execute(sql_text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    logger.info("Added column %s.%s", table, column)
                except Exception as e:
                    logger.warning("Could not add column %s.%s: %s", table, column, e)

            if not _is_sqlite(DATABASE_URL):
                tables = [
                    ("businesses", "businesses_id_seq"),
                    ("products", "products_id_seq"),
                    ("orders", "orders_id_seq"),
                    ("order_items", "order_items_id_seq"),
                    ("users", "users_id_seq"),
                    ("business_connections", "business_connections_id_seq"),
                    ("escalated_chats", "escalated_chats_id_seq"),
                ]
                for table, seq in tables:
                    try:
                        sync_conn.execute(sql_text(
                            f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                        ))
                    except Exception as e:
                        logger.warning("Could not sync sequence %s: %s", seq, e)

            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_telegram_id)",
                "CREATE INDEX IF NOT EXISTS idx_products_business ON products(business_id)",
                "CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)",
                "CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id)",
                "CREATE INDEX IF NOT EXISTS idx_business_connections_business ON business_connections(business_id)",
                "CREATE INDEX IF NOT EXISTS idx_escalated_chats_business ON escalated_chats(business_id)",
                "CREATE INDEX IF NOT EXISTS idx_users_business ON users(business_id)",
            ]
            for stmt in indexes:
                try:
                    sync_conn.execute(sql_text(stmt))
                except Exception as e:
                    logger.warning("Could not create index: %s", e)

        await conn.run_sync(_migrate)
