import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./ardi_agent.db")


def require_secrets() -> None:
    """Validate runtime secrets. Called by main.py — not at import time,
    so pytest / tooling / healthchecks can import config without secrets."""
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

# Cloudflare R2 (free tier — 10GB storage)
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
R2_ENDPOINT = os.getenv("R2_ENDPOINT", "")  # e.g. https://abc123.r2.cloudflarestorage.com
R2_BUCKET = os.getenv("R2_BUCKET", "ardi-products")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")  # e.g. https://pub-abc123.r2.dev

# Rate limiting
RATE_LIMIT_CALLS = int(os.getenv("RATE_LIMIT_CALLS", "30"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

# Subscription & billing
try:
    ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
except (ValueError, TypeError):
    ADMIN_TELEGRAM_ID = 0
SUBSCRIPTION_MONTHLY = 1200  # ETB (default — admin can override in dashboard)
SUBSCRIPTION_YEARLY = 12000  # ETB (2 months free; default — admin can override)
TRIAL_DAYS = 7

# Payment accounts (where users send money). No hardcoded defaults —
# configure in the environment; empty means "not configured".
CBE_ACCOUNT_NAME = os.getenv("CBE_ACCOUNT_NAME", "")
CBE_ACCOUNT_NUMBER = os.getenv("CBE_ACCOUNT_NUMBER", "")
TELEBIRR_ACCOUNT_NAME = os.getenv("TELEBIRR_ACCOUNT_NAME", "")
TELEBIRR_ACCOUNT_NUMBER = os.getenv("TELEBIRR_ACCOUNT_NUMBER", "")

# OCR-confirmed receipts auto-confirm only up to this total; above it the
# order goes to owner review (photos can be edited — never trust big ones).
HIGH_VALUE_AUTO_CONFIRM_ETB = int(os.getenv("HIGH_VALUE_AUTO_CONFIRM_ETB", "2000"))

# Per-shop daily AI call cap (0 = unlimited). Over the cap, customers get a
# busy message and the owner is notified.
MAX_AI_CALLS_PER_SHOP_PER_DAY = int(os.getenv("MAX_AI_CALLS_PER_SHOP_PER_DAY", "500"))

# Master key for encrypting shop Chapa secrets at rest (Fernet). If empty,
# keys are stored as-is with a startup warning — set this in production.
ARDI_MASTER_KEY = os.getenv("ARDI_MASTER_KEY", "")

# Sentry (optional — set SENTRY_DSN in .env to enable)
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# Chapa (Ethiopian gateway) — instant subscription checkout.
# Get it at dashboard.chapa.co > API. Test keys start with CHASECK_TEST-.
CHAPA_SECRET_KEY = os.getenv("CHAPA_SECRET_KEY", "")

# Mini App
MINI_APP_URL = os.getenv("MINI_APP_URL", "")  # e.g. https://ardi-admin.vercel.app
