"""Mini app web server for Ardi AI super admin dashboard.

Run standalone: python miniapp.py
Or: uvicorn miniapp:app --host 0.0.0.0 --port 8080

Requires: pip install fastapi uvicorn
"""

import os
import sys
import subprocess

# Ensure dependencies are installed
for mod in ("fastapi", "uvicorn"):
    try:
        __import__(mod)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", mod])

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="Ardi AI Admin Panel")


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return Response(status_code=200)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ardi AI Admin</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f23; color: #e0e0e0; padding: 16px; }}
    .card {{ background: #1a1a3e; border-radius: 12px; padding: 20px; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; margin-bottom: 8px; color: #fff; }}
    h2 {{ font-size: 16px; color: #aaa; margin-bottom: 12px; }}
    .stat {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #2a2a4e; }}
    .stat:last-child {{ border-bottom: none; }}
    .label {{ color: #888; }}
    .value {{ color: #4fc3f7; font-weight: bold; }}
    .btn {{ display: block; width: 100%; padding: 14px; margin: 8px 0; background: #4fc3f7; color: #0f0f23; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; text-align: center; }}
    .btn-danger {{ background: #ef5350; color: #fff; }}
    .footer {{ text-align: center; color: #555; font-size: 12px; margin-top: 24px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    .badge-green {{ background: #2e7d32; color: #a5d6a7; }}
    .badge-red {{ background: #c62828; color: #ef9a9a; }}
    .badge-yellow {{ background: #f57f17; color: #fff9c4; }}
  </style>
</head>
<body>
  <h1>🛠️ Ardi AI Admin</h1>
  <p style="color:#888;margin-bottom:16px;">Super Admin Dashboard</p>

  <div class="card">
    <h2>📊 Platform Stats</h2>
    <div class="stat"><span class="label">Businesses</span><span class="value" id="biz_count">—</span></div>
    <div class="stat"><span class="label">Active Subscriptions</span><span class="value" id="active_subs">—</span></div>
    <div class="stat"><span class="label">Orders (30d)</span><span class="value" id="orders_30d">—</span></div>
    <div class="stat"><span class="label">Total Users</span><span class="value" id="user_count">—</span></div>
  </div>

  <div class="card">
    <h2>⚡ Quick Actions</h2>
    <button class="btn" onclick="window.Telegram.WebApp.openLink('/api/backup')">💾 Backup DB</button>
    <button class="btn btn-danger" onclick="alert('Confirm in Telegram bot')">🔒 Revoke All Trials</button>
  </div>

  <div class="footer">Ardi AI v1.0 • Powered by Ardi Technologies</div>

  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script>
    Telegram.WebApp.ready();
    Telegram.WebApp.expand();

    async function loadStats() {{
      try {{
        let r = await fetch('/api/stats');
        let d = await r.json();
        document.getElementById('biz_count').textContent = d.businesses ?? '—';
        document.getElementById('active_subs').textContent = d.active_subscriptions ?? '—';
        document.getElementById('orders_30d').textContent = d.orders_30d ?? '—';
        document.getElementById('user_count').textContent = d.users ?? '—';
      }} catch(e) {{
        console.error('Stats fetch failed:', e);
      }}
    }}
    loadStats();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/api/stats")
async def stats():
    try:
        from db.database import async_session
        from db.models import Business, User, Order
        from sqlalchemy import select, func
        from datetime import datetime, timedelta, timezone

        async with async_session() as s:
            biz_count = (await s.execute(select(func.count(Business.id)))).scalar() or 0
            active_subs = (await s.execute(
                select(func.count(Business.id)).where(Business.subscription_status == "active")
            )).scalar() or 0
            user_count = (await s.execute(select(func.count(User.id)))).scalar() or 0
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            orders_30d = (await s.execute(
                select(func.count(Order.id)).where(Order.created_at >= cutoff)
            )).scalar() or 0

        return {
            "businesses": biz_count,
            "active_subscriptions": active_subs,
            "users": user_count,
            "orders_30d": orders_30d,
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("MINI_APP_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
