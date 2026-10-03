"""Admin-configurable app settings (e.g. subscription prices).

Values live in the DB (AppSetting table) so the admin dashboard can change
them without a redeploy. Config constants remain as defaults/fallbacks.
"""
import logging

logger = logging.getLogger(__name__)

MONTHLY_KEY = "plan.monthly"
YEARLY_KEY = "plan.yearly"


async def get_setting(key: str, default: str = "") -> str:
    from db.database import async_session
    from db.models import AppSetting
    try:
        async with async_session() as s:
            row = await s.get(AppSetting, key)
            if row and row.value:
                return row.value
    except Exception as e:
        logger.warning("get_setting(%s) failed, using default: %s", key, e)
    return default


async def set_setting(key: str, value: str) -> None:
    from db.database import async_session
    from db.models import AppSetting
    async with async_session() as s:
        row = await s.get(AppSetting, key)
        if row:
            row.value = value
        else:
            s.add(AppSetting(key=key, value=value))
        await s.commit()


def _parse_price(value: str | None, fallback: int) -> int:
    try:
        v = int(float(value))
        if 1 <= v <= 100_000_000:
            return v
    except (ValueError, TypeError):
        pass
    return fallback


async def get_plan_prices() -> dict:
    """Return {monthly, yearly} in ETB — admin values or config defaults."""
    from config import SUBSCRIPTION_MONTHLY, SUBSCRIPTION_YEARLY
    monthly = _parse_price(await get_setting(MONTHLY_KEY, ""), SUBSCRIPTION_MONTHLY)
    yearly = _parse_price(await get_setting(YEARLY_KEY, ""), SUBSCRIPTION_YEARLY)
    return {"monthly": monthly, "yearly": yearly}


def _ai_day_key(business_id: int) -> tuple:
    import datetime
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return f"aiuse:{business_id}:{day}", f"aiuse-n:{business_id}:{day}"


async def bump_ai_usage(business_id: int) -> int:
    """Count one AI call for this shop today. Returns the new total."""
    key, _ = _ai_day_key(business_id)
    try:
        n = int(await get_setting(key, "0") or 0) + 1
    except (ValueError, TypeError):
        n = 1
    await set_setting(key, str(n))
    return n


async def ai_over_cap(business_id: int) -> tuple:
    """(over_cap, first_hit_today). Cap 0 in config means unlimited."""
    from config import MAX_AI_CALLS_PER_SHOP_PER_DAY
    if MAX_AI_CALLS_PER_SHOP_PER_DAY <= 0:
        return False, False
    key, nkey = _ai_day_key(business_id)
    try:
        n = int(await get_setting(key, "0") or 0)
    except (ValueError, TypeError):
        n = 0
    if n < MAX_AI_CALLS_PER_SHOP_PER_DAY:
        return False, False
    if await get_setting(nkey, ""):
        return True, False
    await set_setting(nkey, "1")
    return True, True
