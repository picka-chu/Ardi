import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./ardi_agent.db")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is required")

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
SUBSCRIPTION_MONTHLY = 1200  # ETB
SUBSCRIPTION_YEARLY = 12000  # ETB (2 months free)
TRIAL_DAYS = 7

# Payment accounts (where users send money)
CBE_ACCOUNT_NAME = os.getenv("CBE_ACCOUNT_NAME", "Bereket Tesfalem")
CBE_ACCOUNT_NUMBER = os.getenv("CBE_ACCOUNT_NUMBER", "1000602869893")
TELEBIRR_ACCOUNT_NAME = os.getenv("TELEBIRR_ACCOUNT_NAME", "Bereket Tesfalem")
TELEBIRR_ACCOUNT_NUMBER = os.getenv("TELEBIRR_ACCOUNT_NUMBER", "0930529985")

# Sentry (optional — set SENTRY_DSN in .env to enable)
SENTRY_DSN = os.getenv("SENTRY_DSN", "")

# Mini App
MINI_APP_URL = os.getenv("MINI_APP_URL", "")  # e.g. https://ardi-admin.vercel.app
