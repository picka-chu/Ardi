"""Mini app web server for Ardi AI super admin dashboard.

Run standalone: python miniapp.py
Or: uvicorn miniapp:app --host 0.0.0.0 --port 8080

Requires: pip install fastapi uvicorn
"""

import os
import time

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="Ardi AI Admin Panel")

# Admin API key for protecting sensitive endpoints
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")


async def _require_admin(request: Request):
    """Reject requests without a valid admin API key."""
    auth = request.headers.get("Authorization", "")
    if ADMIN_API_KEY and auth != f"Bearer {ADMIN_API_KEY}":
        raise HTTPException(status_code=403, detail="Forbidden")

# Bot heartbeat — updated by a periodic task in the bot
bot_last_heartbeat: float = time.monotonic()

HEARTBEAT_TIMEOUT = 120  # seconds — 2x the polling interval


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    age = time.monotonic() - bot_last_heartbeat
    if age > HEARTBEAT_TIMEOUT:
        return Response(status_code=503, content="Bot heartbeat expired")
    return Response(status_code=200)

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Ardi AI</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: var(--tg-theme-bg-color, #0f0f1a);
      --card: var(--tg-theme-secondary-bg-color, #1a1a2e);
      --text: var(--tg-theme-text-color, #e8e8f0);
      --hint: var(--tg-theme-hint-color, #7a7a8a);
      --accent: var(--tg-theme-button-color, #6c5ce7);
      --accent-text: var(--tg-theme-button-text-color, #ffffff);
      --danger: #ff4757;
      --success: #2ed573;
      --warning: #ffa502;
      --radius: 16px;
      --shadow: 0 8px 32px rgba(0,0,0,0.3);
      --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 0;
      min-height: 100vh;
      overflow-x: hidden;
      -webkit-font-smoothing: antialiased;
    }
    .container { padding: 20px 16px 32px; max-width: 480px; margin: 0 auto; }

    /* Header */
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 4px 20px;
    }
    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .logo {
      width: 44px; height: 44px;
      background: linear-gradient(135deg, var(--accent), #a29bfe);
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      font-weight: 800;
      color: #fff;
      box-shadow: 0 4px 16px rgba(108,92,231,0.3);
      flex-shrink: 0;
    }
    .header-text h1 {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.3px;
      line-height: 1.2;
    }
    .header-text p {
      font-size: 13px;
      color: var(--hint);
      font-weight: 500;
    }
    .status-badge {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      background: rgba(46,213,115,0.12);
      color: var(--success);
    }
    .status-badge.offline {
      background: rgba(255,71,87,0.12);
      color: var(--danger);
    }
    .status-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--success);
      animation: pulse 2s infinite;
    }
    .status-badge.offline .status-dot { background: var(--danger); animation: none; }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(0.8); }
    }

    /* Stats Grid */
    .stats-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-bottom: 20px;
    }
    .stat-card {
      background: var(--card);
      border-radius: var(--radius);
      padding: 18px 16px;
      position: relative;
      overflow: hidden;
      transition: var(--transition);
      border: 1px solid rgba(255,255,255,0.04);
    }
    .stat-card:active { transform: scale(0.97); }
    .stat-card .stat-icon {
      width: 36px; height: 36px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      margin-bottom: 12px;
    }
    .stat-card .stat-icon.purple { background: rgba(108,92,231,0.15); }
    .stat-card .stat-icon.green { background: rgba(46,213,115,0.15); }
    .stat-card .stat-icon.orange { background: rgba(255,165,2,0.15); }
    .stat-card .stat-icon.blue { background: rgba(54,164,255,0.15); }
    .stat-label {
      font-size: 12px;
      color: var(--hint);
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }
    .stat-value {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -1px;
      line-height: 1.1;
    }
    .stat-value.skeleton {
      width: 60px; height: 32px;
      background: linear-gradient(90deg, rgba(255,255,255,0.06) 25%, rgba(255,255,255,0.12) 50%, rgba(255,255,255,0.06) 75%);
      background-size: 200% 100%;
      animation: shimmer 1.5s infinite;
      border-radius: 6px;
    }
    @keyframes shimmer {
      0% { background-position: 200% 0; }
      100% { background-position: -200% 0; }
    }

    /* Sections */
    .section-title {
      font-size: 15px;
      font-weight: 600;
      color: var(--hint);
      margin-bottom: 12px;
      padding: 0 4px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* Action Cards */
    .actions {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-bottom: 24px;
    }
    .action-btn {
      display: flex;
      align-items: center;
      gap: 14px;
      background: var(--card);
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: var(--radius);
      padding: 16px;
      cursor: pointer;
      transition: var(--transition);
      width: 100%;
      text-align: left;
      color: var(--text);
      font-family: inherit;
      font-size: 15px;
      font-weight: 500;
      position: relative;
      overflow: hidden;
    }
    .action-btn:active { transform: scale(0.98); }
    .action-btn::after {
      content: '';
      position: absolute;
      inset: 0;
      background: rgba(255,255,255,0.03);
      opacity: 0;
      transition: var(--transition);
    }
    .action-btn:hover::after { opacity: 1; }
    .action-btn .btn-icon {
      width: 40px; height: 40px;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      flex-shrink: 0;
    }
    .action-btn .btn-icon.purple { background: rgba(108,92,231,0.15); }
    .action-btn .btn-icon.red { background: rgba(255,71,87,0.15); }
    .action-btn .btn-icon.green { background: rgba(46,213,115,0.15); }
    .action-btn .btn-icon.blue { background: rgba(54,164,255,0.15); }
    .action-btn .btn-content {
      flex: 1;
      min-width: 0;
    }
    .action-btn .btn-title {
      font-weight: 600;
      font-size: 15px;
    }
    .action-btn .btn-sub {
      font-size: 12px;
      color: var(--hint);
      font-weight: 400;
      margin-top: 2px;
    }
    .action-btn .btn-arrow {
      color: var(--hint);
      font-size: 18px;
      flex-shrink: 0;
      opacity: 0.5;
    }

    /* Toast */
    .toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: var(--card);
      color: var(--text);
      padding: 14px 20px;
      border-radius: 14px;
      font-size: 14px;
      font-weight: 500;
      box-shadow: var(--shadow);
      border: 1px solid rgba(255,255,255,0.06);
      z-index: 1000;
      transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s;
      opacity: 0;
      max-width: calc(100vw - 48px);
      text-align: center;
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }
    .toast.show {
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }
    .toast.success { border-color: rgba(46,213,115,0.3); }
    .toast.error { border-color: rgba(255,71,87,0.3); }

    /* Loading overlay */
    .loading-bar {
      position: fixed;
      top: 0; left: 0;
      width: 100%; height: 3px;
      background: rgba(255,255,255,0.06);
      z-index: 999;
      display: none;
    }
    .loading-bar.active { display: block; }
    .loading-bar::after {
      content: '';
      position: absolute;
      top: 0; left: 0;
      height: 100%;
      width: 40%;
      background: linear-gradient(90deg, var(--accent), #a29bfe);
      animation: loading 1s ease-in-out infinite;
      border-radius: 2px;
    }
    @keyframes loading {
      0% { left: -40%; }
      100% { left: 100%; }
    }

    /* Footer */
    .footer {
      text-align: center;
      padding: 24px 0 8px;
      color: var(--hint);
      font-size: 12px;
      font-weight: 500;
      opacity: 0.5;
    }
    .footer span { opacity: 0.7; }

    /* Spacer */
    .spacer { height: 12px; }
  </style>
</head>
<body>
  <div class="loading-bar" id="loadingBar"></div>
  <div class="container">
    <!-- Header -->
    <div class="header">
      <div class="header-left">
        <div class="logo">A</div>
        <div class="header-text">
          <h1>Ardi AI</h1>
          <p>Admin Dashboard</p>
        </div>
      </div>
      <div class="status-badge" id="statusBadge">
        <span class="status-dot"></span>
        <span id="statusText">Online</span>
      </div>
    </div>

    <!-- Stats Grid -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon purple">🏪</div>
        <div class="stat-label">Businesses</div>
        <div class="stat-value skeleton" id="biz_count"></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon green">✅</div>
        <div class="stat-label">Active Subs</div>
        <div class="stat-value skeleton" id="active_subs"></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon orange">📦</div>
        <div class="stat-label">Orders (30d)</div>
        <div class="stat-value skeleton" id="orders_30d"></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon blue">👥</div>
        <div class="stat-label">Total Users</div>
        <div class="stat-value skeleton" id="user_count"></div>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="section-title">⚡ Quick Actions</div>
    <div class="actions">
      <button class="action-btn" onclick="backupDB()">
        <div class="btn-icon purple">💾</div>
        <div class="btn-content">
          <div class="btn-title">Backup Database</div>
          <div class="btn-sub">Export full database snapshot</div>
        </div>
        <span class="btn-arrow">›</span>
      </button>
      <button class="action-btn" onclick="confirmRevoke()">
        <div class="btn-icon red">🔒</div>
        <div class="btn-content">
          <div class="btn-title">Revoke All Trials</div>
          <div class="btn-sub">End all active trial subscriptions</div>
        </div>
        <span class="btn-arrow">›</span>
      </button>
    </div>

    <!-- Resources -->
    <div class="section-title">📚 Resources</div>
    <div class="actions">
      <button class="action-btn" onclick="Telegram.WebApp.openLink('https://t.me/ardisupport')">
        <div class="btn-icon blue">💬</div>
        <div class="btn-content">
          <div class="btn-title">Support Chat</div>
          <div class="btn-sub">Get help from the team</div>
        </div>
        <span class="btn-arrow">›</span>
      </button>
      <button class="action-btn" onclick="Telegram.WebApp.openLink('https://ardi.ai/docs')">
        <div class="btn-icon green">📖</div>
        <div class="btn-content">
          <div class="btn-title">Documentation</div>
          <div class="btn-sub">Guides & API reference</div>
        </div>
        <span class="btn-arrow">›</span>
      </button>
    </div>

    <div class="footer">
      Ardi AI <span>·</span> v1.0
    </div>
  </div>

  <!-- Toast -->
  <div class="toast" id="toast"></div>

  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <script>
    const ADMIN_API_KEY = "{{ADMIN_API_KEY}}";

    Telegram.WebApp.ready();
    Telegram.WebApp.expand();

    function _headers() {
      let h = {"Content-Type": "application/json"};
      if (ADMIN_API_KEY) h["Authorization"] = "Bearer " + ADMIN_API_KEY;
      return h;
    }

    function showToast(msg, type) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = 'toast' + (type ? ' ' + type : '');
      requestAnimationFrame(() => {
        t.classList.add('show');
        clearTimeout(t._hide);
        t._hide = setTimeout(() => t.classList.remove('show'), 3000);
      });
    }

    function loading(on) {
      document.getElementById('loadingBar').classList.toggle('active', on);
    }

    async function loadStats() {
      try {
        const r = await fetch('/api/stats', {headers: _headers()});
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        const fields = [
          ['biz_count', d.businesses],
          ['active_subs', d.active_subscriptions],
          ['orders_30d', d.orders_30d],
          ['user_count', d.users],
        ];
        fields.forEach(([id, val]) => {
          const el = document.getElementById(id);
          el.textContent = val ?? '—';
          el.classList.remove('skeleton');
        });
      } catch(e) {
        console.error('Stats fetch failed:', e);
        document.querySelectorAll('.stat-value.skeleton').forEach(el => {
          el.textContent = '—';
          el.classList.remove('skeleton');
        });
      }
    }

    async function backupDB() {
      loading(true);
      try {
        const r = await fetch('/api/backup', {headers: _headers(), method: 'POST'});
        const d = await r.json();
        if (d.success) {
          showToast('✅ Backup saved successfully', 'success');
        } else {
          showToast('❌ ' + (d.error || 'Backup failed'), 'error');
        }
      } catch(e) {
        showToast('❌ Backup failed: ' + e.message, 'error');
      } finally {
        loading(false);
      }
    }

    function confirmRevoke() {
      Telegram.WebApp.showConfirm('Revoke all trial subscriptions? This cannot be undone.', ok => {
        if (ok) showToast('🔒 Use /admin in the bot to confirm', 'warning');
      });
    }

    loadStats();
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/api/stats")
async def stats(request: Request):
    await _require_admin(request)
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


@app.post("/api/backup")
@app.get("/api/backup")
async def api_backup(request: Request):
    await _require_admin(request)
    try:
        from db.backup import backup_database
        path = await backup_database()
        if path:
            return {"success": True, "message": f"Backup saved to {path}"}
        return {"error": "Backup failed — check logs"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("MINI_APP_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
