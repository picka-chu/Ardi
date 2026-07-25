"""Migrate existing SQLite data to Supabase PostgreSQL."""
import sqlite3, asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_URL
from db.database import engine, async_session, Base
from db.models import Business, Product, BusinessConnectionModel

OLD_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ardi_agent.db")


async def migrate():
    if not os.path.exists(OLD_DB):
        print("No SQLite database found. Nothing to migrate.")
        return

    conn = sqlite3.connect(OLD_DB)
    conn.row_factory = sqlite3.Row
    old_biz = [dict(r) for r in conn.execute("SELECT * FROM businesses").fetchall()]
    old_con = [dict(r) for r in conn.execute("SELECT * FROM business_connections").fetchall()]
    old_pro = [dict(r) for r in conn.execute("SELECT * FROM products").fetchall()]
    conn.close()
    print(f"Found: {len(old_biz)} businesses, {len(old_con)} connections, {len(old_pro)} products")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        for b in old_biz:
            session.add(Business(
                id=b["id"],
                telegram_chat_id=b["telegram_chat_id"],
                name=b["name"],
                description=b.get("description", ""),
                address=b.get("address", ""),
                phone=b.get("phone", ""),
                channel_id=b.get("channel_id"),
                ai_active=b.get("ai_active", False),
                ai_tone=b.get("ai_tone", "friendly"),
                business_hours_enabled=b.get("business_hours_enabled", False),
                business_hours_start=b.get("business_hours_start"),
                business_hours_end=b.get("business_hours_end"),
                ai_offline_message=b.get("ai_offline_message"),
                subscription_status=b.get("subscription_status", "trial"),
                subscription_plan=b.get("subscription_plan"),
                orders_enabled=b.get("orders_enabled", False),
                order_bank_name=b.get("order_bank_name"),
                order_bank_account=b.get("order_bank_account"),
                order_account_holder=b.get("order_account_holder"),
            ))
        for c in old_con:
            session.add(BusinessConnectionModel(
                id=c["id"], business_id=c["business_id"],
                connection_id=c["connection_id"], user_chat_id=c["user_chat_id"],
            ))
        for p in old_pro:
            session.add(Product(
                id=p["id"], business_id=p["business_id"], name=p["name"],
                price=p.get("price"), available=p.get("available", True),
                photo_file_id=p.get("photo_file_id"), photo_url=p.get("photo_url"),
            ))
        await session.commit()

    print("Migration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
