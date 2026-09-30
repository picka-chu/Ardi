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
