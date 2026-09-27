"""Mini app web server for Ardi AI."""
import os, time, hmac, hashlib, json, secrets
from urllib.parse import unquote_plus
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="Ardi AI")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
bot_last_heartbeat: float = time.monotonic()
HEARTBEAT_TIMEOUT = 120
_START_MONO: float = time.monotonic()

# Short-lived dashboard tokens: {token: {"telegram_id": int, "expires": float}}
_dash_tokens: dict = {}
DASH_TOKEN_TTL = 3600  # 1 hour
MAX_PHOTO_BYTES = 5 * 1024 * 1024
INIT_DATA_MAX_AGE = 86400  # 24h


def _sweep_dash_tokens() -> None:
    now = time.monotonic()
    for tok in [t for t, e in _dash_tokens.items() if now > e.get("expires", 0)]:
        _dash_tokens.pop(tok, None)


def generate_dash_token(telegram_id: int) -> str:
    token = secrets.token_hex(20)
    _dash_tokens[token] = {"telegram_id": telegram_id, "expires": time.monotonic() + DASH_TOKEN_TTL}
    return token


def validate_dash_token(token: str) -> int | None:
    _sweep_dash_tokens()
    entry = _dash_tokens.get(token)
    if not entry:
        return None
    if time.monotonic() > entry["expires"]:
        _dash_tokens.pop(token, None)
        return None
    return entry["telegram_id"]


async def _require_admin(request: Request):
    # Fail closed: if no key is configured, deny all admin access
    # instead of leaving endpoints open.
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {ADMIN_API_KEY}"):
        raise HTTPException(status_code=403, detail="Forbidden")


def _validate_init_data(init_data: str) -> dict | None:
    try:
        if not init_data or not BOT_TOKEN:
            return None
        parsed = {}
        for part in init_data.split("&"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            parsed[unquote_plus(k)] = unquote_plus(v)
        if "hash" not in parsed or "auth_date" not in parsed:
            return None
        try:
            auth_ts = int(parsed["auth_date"])
        except (ValueError, TypeError):
            return None
        if abs(time.time() - auth_ts) > INIT_DATA_MAX_AGE:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()) if k != "hash")
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, parsed.get("hash", "")):
            return None
        user_raw = parsed.get("user", "")
        return json.loads(user_raw) if user_raw else None
    except Exception:
        return None


async def _require_business(request: Request):
    # 1) Try HMAC-validated initData (mobile)
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        init_data = request.query_params.get("tgWebAppData", "")
    user = _validate_init_data(init_data)
    if user:
        tid = user.get("id")
        if tid:
            from db.database import async_session
            from db.models import Business
            from sqlalchemy import select
            async with async_session() as s:
                result = await s.execute(select(Business).where(Business.telegram_chat_id == tid))
                b = result.scalar_one_or_none()
                if b:
                    return {"business": b, "telegram_id": tid, "user": user}

    # 2) Try dashboard token (Desktop fallback — initData is buggy on tdesktop)
    # Token via Authorization-style header only (never query string, to avoid access-log leaks).
    token = request.headers.get("X-Dashboard-Token", "")
    if token:
        tid = validate_dash_token(token)
        if tid:
            from db.database import async_session
            from db.models import Business
            from sqlalchemy import select
            async with async_session() as s:
                result = await s.execute(select(Business).where(Business.telegram_chat_id == tid))
                b = result.scalar_one_or_none()
                if b:
                    return {"business": b, "telegram_id": tid, "user": {"id": tid}}
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    age = time.monotonic() - bot_last_heartbeat
    if age > HEARTBEAT_TIMEOUT:
        return Response(status_code=503, content="Bot heartbeat expired")
    return Response(status_code=200)


# ═══════════════════════════════════════════════════════════════
# ADMIN SPA
# ═══════════════════════════════════════════════════════════════

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Ardi AI Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--b:var(--tg-theme-bg-color,#0c0c1a);--c:var(--tg-theme-secondary-bg-color,#16162a);--t:var(--tg-theme-text-color,#eee);--h:var(--tg-theme-hint-color,#6e6e82);--a:var(--tg-theme-button-color,#6c5ce7);--at:var(--tg-theme-button-text-color,#fff);--r:14px;--s:0 8px 40px rgba(0,0,0,.4);--br:linear-gradient(135deg,var(--a),#a29bfe)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--b);color:var(--t);min-height:100vh;overflow-x:hidden;-webkit-font-smoothing:antialiased;padding-bottom:80px}
input,textarea,select,button{font-family:inherit}
.pg{display:none;padding:16px;max-width:480px;margin:0 auto}.pg.a{display:block}
.hd{display:flex;align-items:center;justify-content:space-between;padding:12px 4px 16px}
.hl{display:flex;align-items:center;gap:12px}
.lo{width:40px;height:40px;background:var(--br);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 16px rgba(108,92,231,.3)}
.ht h1{font-size:18px;font-weight:700;line-height:1.2}.ht p{font-size:12px;color:var(--h);font-weight:500}
.bd{display:flex;align-items:center;gap:5px;padding:5px 10px;border-radius:20px;font-size:11px;font-weight:600;background:rgba(46,213,115,.12);color:#2ed573}.bd.o{background:rgba(255,71,87,.12);color:#ff4757}
.dt{width:6px;height:6px;border-radius:50%;background:#2ed573;animation:pu 2s infinite}.bd.o .dt{background:#ff4757;animation:none}
@keyframes pu{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(.8)}}
.nv{position:fixed;bottom:0;left:0;right:0;background:var(--c);border-top:1px solid rgba(255,255,255,.05);display:flex;justify-content:space-around;padding:8px 0;padding-bottom:calc(8px + env(safe-area-inset-bottom));z-index:100;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.nb{display:flex;flex-direction:column;align-items:center;gap:2px;background:none;border:none;color:var(--h);font-family:inherit;font-size:10px;font-weight:500;cursor:pointer;padding:4px 12px;border-radius:8px;transition:.2s;-webkit-tap-highlight-color:transparent}
.nb .ni{font-size:20px;line-height:1}.nb.a{color:var(--a)}.nb:active{transform:scale(.92)}
.cd{background:var(--c);border-radius:var(--r);padding:16px;margin-bottom:12px;border:1px solid rgba(255,255,255,.04)}
.sg{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}
.sc{background:var(--c);border-radius:var(--r);padding:14px;border:1px solid rgba(255,255,255,.04)}
.sc .ic{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;margin-bottom:8px}
.sc .ic.pu{background:rgba(108,92,231,.15)}.sc .ic.gr{background:rgba(46,213,115,.15)}.sc .ic.or{background:rgba(255,165,2,.15)}.sc .ic.bl{background:rgba(54,164,255,.15)}.sc .ic.re{background:rgba(255,71,87,.15)}
.sl{font-size:11px;color:var(--h);font-weight:500;text-transform:uppercase;letter-spacing:.4px;margin-bottom:2px}
.sv{font-size:24px;font-weight:800;letter-spacing:-.5px;line-height:1.2}
.sv.sk{width:50px;height:28px;background:linear-gradient(90deg,rgba(255,255,255,.04) 25%,rgba(255,255,255,.1) 50%,rgba(255,255,255,.04) 75%);background-size:200% 100%;animation:sh 1.5s infinite;border-radius:4px}
@keyframes sh{0%{background-position:200% 0}100%{background-position:-200% 0}}
.sh{font-size:13px;font-weight:600;color:var(--h);margin-bottom:10px;padding:0 4px;display:flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.4px}
.li{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:.2s;-webkit-tap-highlight-color:transparent}
.li:last-child{border-bottom:none}.li:active{opacity:.6}
.la{width:36px;height:36px;border-radius:10px;background:rgba(108,92,231,.12);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:600;color:var(--a);flex-shrink:0}
.lb{flex:1;min-width:0}.lt{font-size:14px;font-weight:600}.ls{font-size:12px;color:var(--h);margin-top:1px}
.lr{text-align:right;flex-shrink:0}
.st{display:inline-block;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600}
.sa{background:rgba(46,213,115,.12);color:#2ed573}.stb{background:rgba(255,165,2,.12);color:#ffa502}.se{background:rgba(255,71,87,.12);color:#ff4757}.sp{background:rgba(108,92,231,.12);color:var(--a)}.ss{background:rgba(108,92,231,.12);color:#a29bfe}.skk{background:rgba(46,213,115,.12);color:#2ed573}.sx{background:rgba(255,71,87,.12);color:#ff4757}
.btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:14px;border-radius:12px;font-size:14px;font-weight:600;font-family:inherit;cursor:pointer;border:none;transition:.2s;-webkit-tap-highlight-color:transparent;margin-bottom:8px}
.btn:active{transform:scale(.97)}.bp{background:var(--a);color:var(--at)}.bs{background:rgba(255,255,255,.06);color:var(--t);border:1px solid rgba(255,255,255,.08)}.bdg{background:rgba(255,71,87,.12);color:#ff4757}
.sr{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--t);font-family:inherit;font-size:14px;outline:none;margin-bottom:12px;transition:.2s}
.sr:focus{border-color:var(--a);background:rgba(108,92,231,.06)}
.ts{position:fixed;bottom:90px;left:50%;transform:translateX(-50%) translateY(120px);background:var(--c);color:var(--t);padding:12px 18px;border-radius:12px;font-size:13px;font-weight:500;box-shadow:var(--s);border:1px solid rgba(255,255,255,.06);z-index:1000;transition:transform .35s cubic-bezier(.34,1.56,.64,1),opacity .25s;opacity:0;max-width:calc(100vw - 48px);text-align:center;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);pointer-events:none}
.ts.s{transform:translateX(-50%) translateY(0);opacity:1}.ts.er{border-color:rgba(255,71,87,.3)}.ts.ok{border-color:rgba(46,213,115,.3)}
.ld{position:fixed;top:0;left:0;width:100%;height:3px;z-index:999;display:none;background:rgba(255,255,255,.04)}
.ld.a{display:block}.ld::after{content:'';position:absolute;top:0;left:0;height:100%;width:40%;background:var(--br);animation:ld 1s ease-in-out infinite;border-radius:2px}
@keyframes ld{0%{left:-40%}100%{left:100%}}
.bk{display:inline-flex;align-items:center;gap:6px;background:none;border:none;color:var(--a);font-family:inherit;font-size:14px;font-weight:600;cursor:pointer;padding:8px 4px;margin-bottom:12px;-webkit-tap-highlight-color:transparent}
.bk:active{opacity:.6}
.dl{display:flex;flex-direction:column;gap:6px;margin-bottom:16px}.dl .rw{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04)}.dl .rw:last-child{border-bottom:none}
.dl .lb{font-size:13px;color:var(--h)}.dl .vl{font-size:13px;font-weight:600;text-align:right}
.em{padding:40px 20px;text-align:center;color:var(--h);font-size:14px}
.mg{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}.mg .mc{background:var(--c);border-radius:var(--r);padding:14px;border:1px solid rgba(255,255,255,.04)}.mg .ml{font-size:11px;color:var(--h);font-weight:500;text-transform:uppercase;letter-spacing:.4px}.mg .mv{font-size:20px;font-weight:800;letter-spacing:-.3px;margin-top:2px}
.mod{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:200;display:none;align-items:flex-end;justify-content:center;backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)}
.mod.a{display:flex}.mw{background:var(--c);width:100%;max-width:480px;border-radius:20px 20px 0 0;padding:24px 20px;padding-bottom:calc(24px + env(safe-area-inset-bottom));max-height:85vh;overflow-y:auto;animation:ms .3s cubic-bezier(.34,1.56,.64,1)}
@keyframes ms{from{transform:translateY(100%)}to{transform:translateY(0)}}
.mh{font-size:18px;font-weight:700;margin-bottom:16px}.mc{font-size:14px;color:var(--h);margin-bottom:8px}
.tg{display:flex;align-items:center;gap:10px;padding:12px 16px;background:rgba(255,255,255,.04);border-radius:12px;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tk{width:44px;height:24px;border-radius:12px;background:rgba(255,255,255,.12);position:relative;transition:.3s;flex-shrink:0}.tk.on{background:var(--a)}.tk::after{content:'';width:20px;height:20px;border-radius:50%;background:#fff;position:absolute;top:2px;left:2px;transition:.3s}.tk.on::after{left:22px}
.sel{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--t);font-size:14px;outline:none;appearance:none;-webkit-appearance:none;cursor:pointer}
.txt{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--t);font-size:14px;outline:none;transition:.2s;resize:none}
.txt:focus{border-color:var(--a)}.txt::placeholder{color:var(--h)}
</style>
</head>
<body>
<div class="ld" id="ld"></div>
<div class="ts" id="ts"></div>
<div class="mod a" id="lock"><div class="mw" style="text-align:center">
<div class="lo" style="width:56px;height:56px;font-size:26px;margin:0 auto 12px">A</div>
<div class="mh">🔒 Admin Access</div>
<div class="mc">Enter your admin key to unlock the dashboard.</div>
<input class="txt" id="lockKey" type="password" placeholder="Admin key" autocomplete="off" style="margin-bottom:12px" onkeydown="if(event.key==='Enter')unlock()">
<button class="btn bp" onclick="unlock()">Unlock</button>
<div id="lockErr" style="font-size:12px;color:#ff4757;min-height:18px"></div>
</div></div>
<nav class="nv" id="nv">
  <button class="nb a" data-pg="dash"><span class="ni">📊</span>Dashboard</button>
  <button class="nb" data-pg="biz"><span class="ni">🏪</span>Businesses</button>
  <button class="nb" data-pg="sub"><span class="ni">💳</span>Subs</button>
  <button class="nb" data-pg="ord"><span class="ni">📦</span>Orders</button>
  <button class="nb" data-pg="set"><span class="ni">⚙️</span>Settings</button>
</nav>

<div class="pg a" id="pg-dash"><div class="hd"><div class="hl"><div class="lo">A</div><div class="ht"><h1>Ardi AI</h1><p>Admin Dashboard</p></div></div><div class="bd" id="stb"><span class="dt"></span><span id="stt">Online</span></div></div><div class="sg" id="ds"></div><div class="sh">💰 Revenue</div><div class="sg" id="rs"></div><div class="sh">🕐 Recent Orders</div><div class="cd" id="ro"><div class="em">Loading...</div></div></div>

<div class="pg" id="pg-biz"><div class="hd"><div class="ht"><h1>🏪 Businesses</h1><p id="bc">—</p></div></div><input class="sr" id="bs" placeholder="Search..." oninput="fb()"><div class="cd" id="bl"><div class="em">Loading...</div></div></div>

<div class="pg" id="pg-sub"><div class="hd"><div class="ht"><h1>💳 Subscriptions</h1></div><select id="sf" onchange="ls()" style="background:var(--c);color:var(--t);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:6px 10px;font-size:12px;outline:none"><option value="all">All</option><option value="active">Active</option><option value="trial">Trial</option><option value="suspended">Suspended</option><option value="expired">Expired</option><option value="awaiting_payment">Pending</option></select></div><div id="sl"><div class="em">Loading...</div></div></div>

<div class="pg" id="pg-ord"><div class="hd"><div class="ht"><h1>📦 Orders</h1><p id="oc">—</p></div></div><div class="mg" id="os"></div><div class="cd" id="ol"><div class="em">Loading...</div></div></div>

<div class="pg" id="pg-set"><div class="hd"><div class="ht"><h1>⚙️ Settings</h1></div></div><div class="sh">System</div><div class="cd" id="shl"></div><div class="sh">💳 Payment Methods</div><div class="cd" id="pmc"><div class="em">Loading...</div></div><div class="sh">📢 Broadcast</div><div class="cd"><div style="font-size:13px;color:var(--h);margin-bottom:10px">Message all business owners</div><textarea id="bm" class="txt" style="min-height:80px;margin-bottom:10px" placeholder="Type message..."></textarea><button class="btn bp" style="margin:0" onclick="sb()">📨 Send to All</button><div id="bms" style="font-size:12px;color:var(--h);margin-top:8px;text-align:center"></div></div><div class="sh">Actions</div><button class="btn bp" onclick="bdb()">💾 Backup Database</button><button class="btn bdg" onclick="cr()">🔒 Revoke All Trials</button><button class="btn bs" onclick="lockout()">🔓 Sign Out</button></div>

<div class="pg" id="pg-dtl"><button class="bk" onclick="sp('dash')">← Back</button><div id="dc"></div></div>

<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
Telegram.WebApp.ready();Telegram.WebApp.expand();
function dk(){return localStorage.getItem("ardi_admin_key")||""}
function hd(){const h={"Content-Type":"application/json"};const k=dk();if(k)h["Authorization"]="Bearer "+k;return h}
function lockShow(msg){$('lock').classList.add('a');$('lockErr').textContent=msg||'';setTimeout(()=>$('lockKey').focus(),300)}
function lockHide(){$('lock').classList.remove('a');$('lockKey').value='';$('lockErr').textContent=''}
function unlock(){const v=$('lockKey').value.trim();if(!v){$('lockErr').textContent='Enter the admin key.';return}localStorage.setItem("ardi_admin_key",v);lockHide();lda()}
function lockout(){localStorage.removeItem("ardi_admin_key");lockShow()}
function $(i){return document.getElementById(i)}
function tt(m,t){const e=$('ts');e.textContent=m;e.className='ts'+(t?' '+t:'');requestAnimationFrame(()=>{e.classList.add('s');clearTimeout(e._h);e._h=setTimeout(()=>e.classList.remove('s'),3000)})}
function ld(o){$('ld').classList.toggle('a',o)}
function es(t){const d=document.createElement('div');d.appendChild(document.createTextNode(t));return d.innerHTML}
document.querySelectorAll('.nb').forEach(b=>{b.onclick=()=>sp(b.dataset.pg)});
function sp(p){document.querySelectorAll('.pg').forEach(x=>x.classList.remove('a'));const e=$('pg-'+p);if(e)e.classList.add('a');document.querySelectorAll('.nb').forEach(b=>b.classList.toggle('a',b.dataset.pg===p));if(p==='dash')lda();else if(p==='biz')lb();else if(p==='sub')ls();else if(p==='ord')lo();else if(p==='set')lse()}
async function ap(p,o){ld(true);try{const r=await fetch(p,{headers:hd(),...o});if(r.status===401||r.status===403){lockShow(dk()?'Wrong key — try again.':'');return null}if(!r.ok)throw new Error('HTTP '+r.status);return await r.json()}catch(e){tt('Error: '+e.message,'er');return null}finally{ld(false)}}

async function lda(){const d=await ap('/api/admin/dashboard');if(!d)return;const s=$('stb');if(d.bot_online){s.className='bd';$('stt').textContent='Online'}else{s.className='bd o';$('stt').textContent='Offline'}
$('ds').innerHTML=[{ic:'🏪',c:'pu',l:'Businesses',v:d.businesses},{ic:'✅',c:'gr',l:'Active Subs',v:d.active_subscriptions},{ic:'📦',c:'or',l:'Orders (30d)',v:d.orders_30d},{ic:'👥',c:'bl',l:'Users',v:d.users}].map(s=>`<div class="sc"><div class="ic ${s.c}">${s.ic}</div><div class="sl">${s.l}</div><div class="sv">${es(String(s.v??'—'))}</div></div>`).join('')
$('rs').innerHTML=[{ic:'💰',c:'gr',l:'Sub Revenue',v:'ETB '+(d.sub_revenue??0).toLocaleString()},{ic:'📊',c:'pu',l:'Avg Order',v:'ETB '+(d.avg_order_value??0).toLocaleString()},{ic:'📈',c:'bl',l:'Pending Orders',v:d.pending_orders??0},{ic:'⭐',c:'or',l:'Trial Biz',v:d.trial_count??0}].map(s=>`<div class="sc"><div class="ic ${s.c}">${s.ic}</div><div class="sl">${s.l}</div><div class="sv">${es(String(s.v))}</div></div>`).join('')
const ro=d.recent_orders||[];if(!ro.length){$('ro').innerHTML='<div class="em">No orders</div>';return}
$('ro').innerHTML=ro.map(o=>`<div class="li" onclick="so(${o.id})"><div class="la">#${o.id}</div><div class="lb"><div class="lt">${es(o.customer_name||'Customer')}</div><div class="ls">${es(o.business_name||'')} · ${o.item_count||0} items</div></div><div class="lr"><div style="font-weight:700">ETB ${(+o.total_price).toLocaleString()}</div><span class="st ${o.status==='pending'?'sp':o.status==='confirmed'?'sa':o.status==='completed'?'skk':'sx'}">${es(o.status)}</span></div></div>`).join('')}

let ab=[];async function lb(){const d=await ap('/api/admin/businesses');if(!d)return;ab=d.businesses||[];$('bc').textContent=ab.length+' reg';fb()}
function fb(){const q=$('bs').value.toLowerCase();const items=q?ab.filter(b=>(b.name||'').toLowerCase().includes(q)||(b.phone||'').includes(q)):ab;if(!items.length){$('bl').innerHTML='<div class="em">Not found</div>';return}
$('bl').innerHTML=items.map(b=>`<div class="li" onclick="sbz(${b.id})"><div class="la">${(b.name||'?')[0].toUpperCase()}</div><div class="lb"><div class="lt">${es(b.name)}</div><div class="ls">${b.product_count||0} products · ${b.order_count||0} orders</div></div><div class="lr"><span class="st ${b.subscription_status==='active'?'sa':b.subscription_status==='trial'?'stb':b.subscription_status==='suspended'?'ss':'se'}">${es(b.subscription_status||'—')}</span></div></div>`).join('')}

async function ls(){const f=$('sf').value;const d=await ap('/api/admin/subscriptions?filter='+f);if(!d)return;const ss=d.subscriptions||[];if(!ss.length){$('sl').innerHTML='<div class="em">None</div>';return}
$('sl').innerHTML=ss.map(s=>`<div class="cd" style="padding:14px"><div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px"><div><div style="font-weight:700">${es(s.business_name)}</div><div style="font-size:12px;color:var(--h)">Plan: ${es(s.plan||'—')}</div></div><span class="st ${s.status==='active'?'sa':s.status==='trial'?'stb':s.status==='expired'?'se':'sp'}">${es(s.status)}</span></div><div style="font-size:12px;color:var(--h);margin-bottom:${s.status==='awaiting_payment'?'12':'0'}px">${s.end_date?'Ends: '+new Date(s.end_date).toLocaleDateString():''}</div>${s.status==='awaiting_payment'?`<button class="btn bp" style="padding:10px;font-size:13px" onclick="cp(${s.business_id},'${s.plan}')">✅ Confirm Payment</button>`:''}${s.status==='active'||s.status==='trial'?`<button class="btn bdg" style="padding:10px;font-size:13px;margin:0" onclick="rv(${s.business_id})">🔒 Revoke</button>`:''}</div>`).join('')}
async function cp(i,p){const d=await ap('/api/admin/subscriptions/confirm',{method:'POST',body:JSON.stringify({business_id:i,plan:p})});if(d&&d.success){tt('✅ Activated','ok');ls()}}
async function rv(i){Telegram.WebApp.showConfirm('Revoke?',async ok=>{if(!ok)return;const d=await ap('/api/admin/subscriptions/revoke',{method:'POST',body:JSON.stringify({business_id:i})});if(d&&d.success){tt('🔒 Revoked','ok');ls()}})}

async function lo(){const d=await ap('/api/admin/orders');if(!d)return;$('oc').textContent=(d.total_orders||0)+' total'
$('os').innerHTML=[{l:'Total Revenue',v:'ETB '+(d.total_revenue||0).toLocaleString()},{l:'Pending',v:d.pending_count||0},{l:'Completed',v:d.completed_count||0},{l:'Cancelled',v:d.cancelled_count||0}].map(s=>`<div class="mc"><div class="ml">${s.l}</div><div class="mv">${es(String(s.v))}</div></div>`).join('')
const os=d.orders||[];if(!os.length){$('ol').innerHTML='<div class="em">No orders</div>';return}
$('ol').innerHTML=os.map(o=>`<div class="li" onclick="so(${o.id})"><div class="la">#${o.id}</div><div class="lb"><div class="lt">${es(o.customer_name||'Customer')}</div><div class="ls">${es(o.business_name||'')}</div></div><div class="lr"><div style="font-weight:700">ETB ${(+o.total_price).toLocaleString()}</div><span class="st ${o.status==='pending'?'sp':o.status==='confirmed'?'sa':o.status==='completed'?'skk':'sx'}">${es(o.status)}</span></div></div>`).join('')}

async function lse(){const d=await ap('/api/admin/system');if(!d)return
$('shl').innerHTML=`<div class="dl"><div class="rw"><span class="lb">Bot</span><span class="vl"><span class="st ${d.bot_online?'skk':'sx'}">${d.bot_online?'Online':'Offline'}</span></span></div><div class="rw"><span class="lb">Uptime</span><span class="vl">${es(d.uptime||'—')}</span></div><div class="rw"><span class="lb">DB</span><span class="vl">${es(d.database||'—')}</span></div><div class="rw"><span class="lb">Businesses</span><span class="vl">${d.businesses||0}</span></div><div class="rw"><span class="lb">Orders</span><span class="vl">${d.orders||0}</span></div><div class="rw"><span class="lb">Users</span><span class="vl">${d.users||0}</span></div></div>`
// Load payment methods
lpm()}
async function lpm(){const d=await ap('/api/admin/payment-methods');if(!d)return
const ms=d.methods||[];if(!ms.length){$('pmc').innerHTML='<div class="em">No payment methods</div>';return}
$('pmc').innerHTML=ms.map(m=>`<div style="margin-bottom:16px;padding-bottom:16px;border-bottom:1px solid rgba(255,255,255,.04)"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px"><div style="font-weight:600;font-size:14px">${es(m.name==='cbe'?'🏦 CBE Birr':'📱 Telebirr')}</div><label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--h);cursor:pointer"><input type="checkbox" ${m.is_active?'checked':''} onchange="spm(${m.id},'is_active',this.checked)" style="accent-color:var(--a)"> Active</label></div><div style="margin-bottom:4px"><div style="font-size:11px;color:var(--h);margin-bottom:2px">Bank Name</div><input class="txt" id="pmbn${m.id}" value="${es(m.bank_name||'')}" style="padding:8px 10px;font-size:13px" onchange="spm(${m.id},'bank_name',this.value)"></div><div style="margin-bottom:4px"><div style="font-size:11px;color:var(--h);margin-bottom:2px">Account Holder</div><input class="txt" id="pman${m.id}" value="${es(m.account_name)}" style="padding:8px 10px;font-size:13px" onchange="spm(${m.id},'account_name',this.value)"></div><div><div style="font-size:11px;color:var(--h);margin-bottom:2px">Account Number</div><input class="txt" id="pmanum${m.id}" value="${es(m.account_number)}" style="padding:8px 10px;font-size:13px" onchange="spm(${m.id},'account_number',this.value)"></div></div>`).join('')+'<button class="btn bp" style="margin:0" onclick="spmSave()">💾 Save Payment Methods</button>'}
let _pmDirty=[];function spm(id,field,val){_pmDirty=Object.values({...Object.fromEntries(_pmDirty.map(x=>[x.id,x])),[id]:{id,...Object.fromEntries(_pmDirty.filter(x=>x.id===id).flatMap(x=>Object.entries(x)).concat([[field,val]]))}});_pmDirty=_pmDirty.filter((x,i,a)=>a.findIndex(y=>y.id===x.id)===i);_pmDirty=_pmDirty.map(x=>({...x,[field]:val}))}
async function spmSave(){if(!_pmDirty.length){tt('No changes','er');return}const d=await ap('/api/admin/payment-methods',{method:'POST',body:JSON.stringify({methods:_pmDirty})});if(d&&d.success){tt('✅ Saved','ok');_pmDirty=[];lpm()}else{tt('Save failed','er')}}

async function bdb(){const d=await ap('/api/backup',{method:'POST'});if(d&&d.success){tt('✅ Backup done','ok');lse()}}
function cr(){Telegram.WebApp.showConfirm('Revoke ALL trials?',async ok=>{if(!ok)return;const d=await ap('/api/admin/subscriptions/revoke-all',{method:'POST'});if(d&&d.success){tt('🔒 All revoked','ok');lse()}})}
async function sb(){const m=$('bm').value.trim();if(!m){tt('Enter a message','er');return}
Telegram.WebApp.showConfirm('Send to ALL owners?',async ok=>{if(!ok)return;$('bms').textContent='Sending...';const d=await ap('/api/admin/broadcast',{method:'POST',body:JSON.stringify({message:m})});if(d&&d.success){tt('📨 Sent to '+d.sent,'ok');$('bms').textContent='Sent to '+d.sent;$('bm').value=''}else{$('bms').textContent='Failed: '+(d&&d.error||'')}})}

async function sbz(i){const d=await ap('/api/admin/businesses/'+i);if(!d)return;const sp=d.subscription_status==='suspended'
$('dc').innerHTML=`<div class="cd"><div style="display:flex;align-items:center;gap:12px;margin-bottom:16px"><div class="lo" style="width:48px;height:48px;font-size:22px">${(d.name||'?')[0]}</div><div><div style="font-size:18px;font-weight:700">${es(d.name)}</div><div style="font-size:13px;color:var(--h)">ID: ${d.id}</div></div></div><div class="dl"><div class="rw"><span class="lb">Status</span><span class="vl"><span class="st ${sp?'sx':d.subscription_status==='active'?'sa':d.subscription_status==='trial'?'stb':'se'}">${es(d.subscription_status)}</span></span></div><div class="rw"><span class="lb">Plan</span><span class="vl">${es(d.plan||'—')}</span></div><div class="rw"><span class="lb">Owner</span><span class="vl">${es(d.owner_name||'—')}</span></div><div class="rw"><span class="lb">Phone</span><span class="vl">${es(d.phone||'—')}</span></div><div class="rw"><span class="lb">Products</span><span class="vl">${d.product_count||0}</span></div><div class="rw"><span class="lb">Orders</span><span class="vl">${d.order_count||0}</span></div><div class="rw"><span class="lb">AI</span><span class="vl">${d.ai_active?'✅':'❌'}</span></div><div class="rw"><span class="lb">Created</span><span class="vl">${d.created_at?new Date(d.created_at).toLocaleDateString():'—'}</span></div></div><div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">${sp?`<button class="btn bp" style="flex:1;margin:0;padding:10px;font-size:13px" onclick="us(${d.id})">✅ Unsuspend</button>`:`<button class="btn bs" style="flex:1;margin:0;padding:10px;font-size:13px" onclick="sbz2(${d.id})">⏸️ Suspend</button>`}<button class="btn bdg" style="flex:1;margin:0;padding:10px;font-size:13px" onclick="dbz(${d.id})">🗑️ Delete</button></div></div>`;sp('dtl')}
async function sbz2(i){Telegram.WebApp.showConfirm('Suspend? AI will be disabled.',async ok=>{if(!ok)return;const d=await ap('/api/admin/businesses/'+i+'/suspend',{method:'POST'});if(d&&d.success){tt('⏸️ Suspended','ok');sbz(i)}})}
async function us(i){const d=await ap('/api/admin/businesses/'+i+'/unsuspend',{method:'POST'});if(d&&d.success){tt('✅ Unsuspended','ok');sbz(i)}}
async function dbz(i){Telegram.WebApp.showConfirm('PERMANENTLY DELETE? Cannot undo.',async ok=>{if(!ok)return;const d=await ap('/api/admin/businesses/'+i+'/delete',{method:'POST'});if(d&&d.success){tt('🗑️ Deleted','ok');sp('biz');lb()}})}
async function so(i){const d=await ap('/api/admin/orders/'+i);if(!d)return
$('dc').innerHTML=`<div class="cd"><div style="font-size:18px;font-weight:700;margin-bottom:12px">Order #${d.id}</div><div class="dl"><div class="rw"><span class="lb">Customer</span><span class="vl">${es(d.customer_name||'—')}</span></div><div class="rw"><span class="lb">Phone</span><span class="vl">${es(d.customer_phone||'—')}</span></div><div class="rw"><span class="lb">Address</span><span class="vl">${es(d.customer_address||'—')}</span></div><div class="rw"><span class="lb">Business</span><span class="vl">${es(d.business_name||'—')}</span></div><div class="rw"><span class="lb">Total</span><span class="vl" style="font-weight:700">ETB ${(+d.total_price).toLocaleString()}</span></div><div class="rw"><span class="lb">Status</span><span class="vl"><span class="st ${d.status==='pending'?'sp':d.status==='confirmed'?'sa':d.status==='completed'?'skk':'sx'}">${es(d.status)}</span></span></div><div class="rw"><span class="lb">Date</span><span class="vl">${d.created_at?new Date(d.created_at).toLocaleString():'—'}</span></div></div>${d.items&&d.items.length?`<div style="font-size:13px;font-weight:600;color:var(--h);margin:8px 0 4px">Items</div>${d.items.map(i=>`<div style="display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid rgba(255,255,255,.04)"><span>${es(i.product_name||'Item')} ×${i.quantity||1}</span><span style="font-weight:600">ETB ${(+i.unit_price).toLocaleString()}</span></div>`).join('')}`:''}</div>`;sp('dtl')}
if(dk()){lda()}else{lockShow()}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# BUSINESS OWNER SPA
# ═══════════════════════════════════════════════════════════════

BIZ_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Ardi • Business</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+Ethiopic:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
    /* Ardi Business v2 — Telegram-native design system */
    :root{
      --bg:var(--tg-theme-bg-color,#0e0e1a);--card:var(--tg-theme-secondary-bg-color,#171726);
      --text:var(--tg-theme-text-color,#f2f2f7);--hint:var(--tg-theme-hint-color,#8e8e9e);
      --link:var(--tg-theme-link-color,#6c5ce7);--btn:var(--tg-theme-button-color,#6c5ce7);
      --btn-tx:var(--tg-theme-button-text-color,#fff);--sec:var(--tg-theme-section-bg-color,rgba(255,255,255,.03));
      --sep:var(--tg-theme-section-separator-color,rgba(255,255,255,.07));
      --ok:#2ed573;--warn:#ffa502;--bad:#ff4757;--info:#36a4ff;
      --r:16px;--rs:12px;--grad:linear-gradient(135deg,var(--btn),#a29bfe);
      --sh:0 8px 32px rgba(0,0,0,.35);
    }
    *{margin:0;padding:0;box-sizing:border-box}
    html{-webkit-text-size-adjust:100%}
    body{font-family:'Inter','Noto Sans Ethiopic',system-ui,sans-serif;background:var(--bg);color:var(--text);
      min-height:100vh;overflow-x:hidden;-webkit-font-smoothing:antialiased;padding-bottom:calc(76px + env(safe-area-inset-bottom))}
    input,textarea,select,button{font-family:inherit;color:inherit}
    button{-webkit-tap-highlight-color:transparent}
    .wrap{max-width:480px;margin:0 auto;padding:0 16px}
    /* top bar */
    .top{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid var(--sep)}
    .top-in{max-width:480px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:12px}
    .ava{width:42px;height:42px;border-radius:14px;background:var(--grad);display:flex;align-items:center;justify-content:center;
      font-size:20px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 14px rgba(108,92,231,.35)}
    .t-id{flex:1;min-width:0}.t-id h1{font-size:16px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .t-id p{font-size:12px;color:var(--hint);font-weight:500;display:flex;align-items:center;gap:6px}
    .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);display:inline-block}
    .dot.off{background:var(--hint)}
    .icon-btn{width:38px;height:38px;border-radius:12px;border:1px solid var(--sep);background:var(--card);color:var(--text);
      font-size:15px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}
    .icon-btn:active{transform:scale(.93)}
    .planbar{max-width:480px;margin:0 auto;padding:10px 16px 0}
    .planbar-in{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:var(--rs);font-size:13px;font-weight:600;cursor:pointer;border:1px solid}
    .planbar-in.warn{background:rgba(255,165,2,.1);border-color:rgba(255,165,2,.25);color:var(--warn)}
    .planbar-in.bad{background:rgba(255,71,87,.1);border-color:rgba(255,71,87,.25);color:var(--bad)}
    /* pages & nav */
    .pg{display:none;padding:16px 0 8px}.pg.on{display:block;animation:fade .25s ease}
    @keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
    .nav{position:fixed;bottom:0;left:0;right:0;background:color-mix(in srgb,var(--card) 92%,transparent);border-top:1px solid var(--sep);
      display:flex;z-index:100;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);padding-bottom:env(safe-area-inset-bottom)}
    .nav-in{max-width:480px;margin:0 auto;display:flex;width:100%}
    .ni{flex:1;background:none;border:none;color:var(--hint);font-size:10px;font-weight:600;display:flex;flex-direction:column;
      align-items:center;gap:3px;padding:9px 0 8px;cursor:pointer;position:relative}
    .ni .e{font-size:21px;line-height:1}.ni.on{color:var(--btn)}
    .bdg{min-width:17px;height:17px;padding:0 5px;border-radius:9px;background:var(--bad);
      color:#fff;font-size:10px;font-weight:700;display:inline-flex;align-items:center;justify-content:center}
    .ni .bdg{position:absolute;top:5px;right:calc(50% - 22px)}
    .qa button{position:relative}.qa .bdg{position:absolute;top:6px;right:8px}
    /* cards & stats */
    .card{background:var(--card);border:1px solid var(--sep);border-radius:var(--r);padding:16px;margin-bottom:12px}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
    .stat{background:var(--card);border:1px solid var(--sep);border-radius:var(--r);padding:14px}
    .stat .ic{width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:16px;margin-bottom:10px}
    .stat .v{font-size:21px;font-weight:800;letter-spacing:-.5px}
    .stat .l{font-size:11px;color:var(--hint);font-weight:600;text-transform:uppercase;letter-spacing:.4px;margin-top:2px}
    .skl{border-radius:6px;background:linear-gradient(90deg,var(--sep) 25%,rgba(255,255,255,.08) 50%,var(--sep) 75%);background-size:200% 100%;animation:sh 1.4s infinite;color:transparent!important}
    @keyframes sh{to{background-position:-200% 0}}
    .sec-t{font-size:13px;font-weight:700;color:var(--hint);text-transform:uppercase;letter-spacing:.5px;margin:18px 2px 10px;display:flex;align-items:center;gap:6px}
    .qa{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:4px}
    .qa button{background:var(--card);border:1px solid var(--sep);border-radius:var(--r);padding:12px 4px;display:flex;flex-direction:column;
      align-items:center;gap:6px;font-size:11px;font-weight:600;cursor:pointer;color:var(--text)}
    .qa button:active{transform:scale(.94)}.qa .e{font-size:22px}
    .row{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid var(--sep);cursor:pointer}
    .row:last-child{border-bottom:none}.row:active{opacity:.6}
    .row .im{width:44px;height:44px;border-radius:12px;object-fit:cover;background:var(--sec);flex-shrink:0}
    .row .im.ph{display:flex;align-items:center;justify-content:center;font-size:20px}
    .row .tx{flex:1;min-width:0}.row .t1{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .row .t2{font-size:12px;color:var(--hint);margin-top:2px}
    .row .rt{text-align:right;flex-shrink:0}
    .pill{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:700}
    .p-ok{background:rgba(46,213,115,.14);color:var(--ok)}.p-warn{background:rgba(255,165,2,.14);color:var(--warn)}
    .p-bad{background:rgba(255,71,87,.14);color:var(--bad)}.p-info{background:rgba(54,164,255,.14);color:var(--info)}
    .p-brand{background:rgba(108,92,231,.14);color:var(--link)}
    .amt{font-weight:800;font-size:14px}
    .empty{padding:44px 20px;text-align:center;color:var(--hint)}.empty .e{font-size:44px;margin-bottom:10px}
    .empty .t{font-size:15px;font-weight:700;color:var(--text);margin-bottom:4px}.empty .s{font-size:13px;margin-bottom:14px}
    /* toolbar, chips, inputs */
    .toolbar{position:sticky;top:62px;z-index:40;background:color-mix(in srgb,var(--bg) 90%,transparent);backdrop-filter:blur(12px);
      -webkit-backdrop-filter:blur(12px);padding:8px 0 10px}
    .search{width:100%;padding:12px 16px 12px 40px;border-radius:12px;border:1px solid var(--sep);background:var(--card);
      font-size:14px;outline:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%238e8e9e' stroke-width='2'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-3.5-3.5'/%3E%3C/svg%3E");
      background-repeat:no-repeat;background-position:13px center}
    .search:focus{border-color:var(--btn)}
    .chips{display:flex;gap:8px;overflow-x:auto;padding:10px 0 2px;scrollbar-width:none}
    .chips::-webkit-scrollbar{display:none}
    .chip{flex-shrink:0;padding:8px 14px;border-radius:20px;border:1px solid var(--sep);background:var(--card);font-size:13px;
      font-weight:600;color:var(--hint);cursor:pointer}
    .chip.on{background:var(--btn);border-color:var(--btn);color:var(--btn-tx)}
    .inp{width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--sep);background:var(--sec);font-size:14px;outline:none;margin-bottom:10px}
    .inp:focus{border-color:var(--btn)}textarea.inp{resize:vertical;min-height:70px}
    label.fl{display:block;font-size:12px;font-weight:600;color:var(--hint);margin:0 0 6px 2px}
    /* buttons */
    .btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:13px;border-radius:13px;font-size:14px;
      font-weight:700;cursor:pointer;border:none;margin-bottom:8px}
    .btn:active{transform:scale(.97)}.btn:disabled{opacity:.5}
    .b-p{background:var(--btn);color:var(--btn-tx)}.b-s{background:var(--sec);border:1px solid var(--sep);color:var(--text)}
    .b-ok{background:rgba(46,213,115,.14);color:var(--ok)}.b-bad{background:rgba(255,71,87,.13);color:var(--bad)}
    .btn-row{display:flex;gap:8px}.btn-row .btn{margin-bottom:0}
    .fab{position:fixed;bottom:calc(88px + env(safe-area-inset-bottom));right:max(16px,calc(50% - 224px));width:56px;height:56px;border-radius:18px;
      background:var(--grad);color:#fff;font-size:26px;border:none;box-shadow:var(--sh);cursor:pointer;z-index:60}
    .fab:active{transform:scale(.92)}
    /* bottom sheet */
    .ov{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:200;display:none;backdrop-filter:blur(2px)}
    .ov.on{display:block}
    .sheet{position:fixed;left:0;right:0;bottom:0;z-index:201;display:none;justify-content:center}
    .sheet.on{display:flex}
    .sheet-in{background:var(--card);width:100%;max-width:480px;border-radius:22px 22px 0 0;padding:10px 20px calc(20px + env(safe-area-inset-bottom));
      max-height:88vh;overflow-y:auto;animation:up .28s cubic-bezier(.32,1.2,.64,1);border-top:1px solid var(--sep)}
    @keyframes up{from{transform:translateY(60px);opacity:.5}to{transform:none;opacity:1}}
    .grab{width:40px;height:4px;border-radius:2px;background:var(--sep);margin:4px auto 14px}
    .sheet h2{font-size:17px;font-weight:800;margin-bottom:12px}
    .photo-pick{display:flex;gap:12px;align-items:center;margin-bottom:12px}
    .photo-pick img{width:72px;height:72px;border-radius:14px;object-fit:cover;background:var(--sec)}
    .photo-pick .ph{width:72px;height:72px;border-radius:14px;background:var(--sec);display:flex;align-items:center;justify-content:center;font-size:28px;flex-shrink:0}
    .tgl{width:46px;height:27px;border-radius:14px;background:var(--sep);position:relative;transition:.25s;flex-shrink:0;cursor:pointer}
    .tgl.on{background:var(--ok)}.tgl::after{content:'';width:21px;height:21px;border-radius:50%;background:#fff;position:absolute;top:3px;left:3px;transition:.25s;box-shadow:0 1px 4px rgba(0,0,0,.3)}
    .tgl.on::after{left:22px}
    .set-row{display:flex;align-items:center;gap:12px;padding:13px 0;border-bottom:1px solid var(--sep);cursor:pointer}
    .set-row:last-child{border-bottom:none}.set-row .tx{flex:1}.set-row .t1{font-size:14px;font-weight:600}.set-row .t2{font-size:12px;color:var(--hint);margin-top:1px}
    .tone-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:4px}
    .tone{padding:12px;border-radius:12px;border:1.5px solid var(--sep);background:var(--sec);cursor:pointer;text-align:left}
    .tone.on{border-color:var(--btn);background:rgba(108,92,231,.08)}.tone .e{font-size:20px}.tone .n{font-size:13px;font-weight:700;margin-top:4px}.tone .d{font-size:11px;color:var(--hint);margin-top:2px}
    /* plan */
    .hero{text-align:center;padding:26px 16px;border-radius:var(--r);margin-bottom:12px;border:1px solid}
    .hero.ok{background:rgba(46,213,115,.08);border-color:rgba(46,213,115,.2)}
    .hero.warn{background:rgba(255,165,2,.08);border-color:rgba(255,165,2,.25)}
    .hero.bad{background:rgba(255,71,87,.08);border-color:rgba(255,71,87,.25)}
    .hero .e{font-size:38px}.hero .t{font-size:17px;font-weight:800;margin-top:6px}.hero .s{font-size:13px;color:var(--hint);margin-top:4px}
    .plans{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
    .plan{cursor:pointer;position:relative;text-align:center;padding:18px 10px;border-radius:var(--r);border:2px solid var(--sep);background:var(--card)}
    .plan.sel{border-color:var(--btn);background:rgba(108,92,231,.07)}
    .plan .e{font-size:26px}.plan .n{font-size:14px;font-weight:700;margin-top:4px}.plan .p{font-size:20px;font-weight:800;margin:6px 0 2px}
    .plan .p small{font-size:11px;color:var(--hint);font-weight:500}.plan .d{font-size:11px;color:var(--hint);line-height:1.4}
    .plan .bv{position:absolute;top:-9px;left:50%;transform:translateX(-50%);background:var(--btn);color:var(--btn-tx);font-size:10px;
      font-weight:800;padding:2px 10px;border-radius:12px;white-space:nowrap}
    .pay-acct{padding:12px 0;border-bottom:1px solid var(--sep)}.pay-acct:last-child{border:none}
    .pay-acct .n{font-size:14px;font-weight:700}.kv{display:flex;justify-content:space-between;padding:5px 0;font-size:13px}
    .kv .k{color:var(--hint)}.kv .v{font-weight:700}
    .up-zone{display:flex;flex-direction:column;align-items:center;gap:6px;padding:26px 16px;border:2px dashed var(--sep);
      border-radius:14px;cursor:pointer;text-align:center;margin-bottom:10px}
    .up-zone.has{border-style:solid;border-color:var(--ok)}
    .up-zone img{max-width:100%;max-height:180px;border-radius:10px}
    /* toast, loader, misc */
    .toast{position:fixed;bottom:calc(92px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%) translateY(20px);background:var(--card);
      border:1px solid var(--sep);padding:12px 18px;border-radius:14px;font-size:13px;font-weight:600;box-shadow:var(--sh);z-index:300;
      opacity:0;pointer-events:none;transition:.3s;max-width:calc(100vw - 40px);text-align:center}
    .toast.show{transform:translateX(-50%);opacity:1}.toast.err{border-color:rgba(255,71,87,.4)}.toast.ok{border-color:rgba(46,213,115,.4)}
    .loader{position:fixed;top:0;left:0;right:0;height:3px;z-index:400;display:none;background:transparent}
    .loader.on{display:block}.loader::after{content:'';position:absolute;height:100%;width:35%;background:var(--grad);border-radius:2px;animation:ld 1s ease-in-out infinite}
    @keyframes ld{0%{left:-35%}100%{left:100%}}
    .sess{padding:60px 24px;text-align:center}.sess .e{font-size:52px}.sess h2{font-size:18px;margin:12px 0 6px}.sess p{font-size:14px;color:var(--hint);margin-bottom:16px}
    .tl{margin:6px 0 4px}.tl .stp{display:flex;gap:10px}.tl .bar{display:flex;flex-direction:column;align-items:center}
    .tl .dt{width:12px;height:12px;border-radius:50%;background:var(--sep);flex-shrink:0;margin-top:2px}
    .tl .stp.done .dt{background:var(--ok)}.tl .stp.now .dt{background:var(--btn);box-shadow:0 0 0 4px rgba(108,92,231,.2)}
    .tl .ln{width:2px;flex:1;background:var(--sep);min-height:14px;margin:2px 0}.tl .stp.done .ln{background:var(--ok)}
    .tl .tt{font-size:13px;font-weight:600;padding-bottom:14px}.tl .tt small{display:block;font-weight:400;color:var(--hint);font-size:11px}
    .foot{text-align:center;font-size:11px;color:var(--hint);padding:18px 0 6px}
  </style>
</head>
<body>
<!-- Ardi Business v2 -->
<div class="loader" id="loader"></div>
<div class="toast" id="toast"></div>

<header class="top"><div class="top-in">
  <div class="ava" id="ava">A</div>
  <div class="t-id"><h1 id="bizName">Ardi Business</h1><p><span class="dot" id="aiDot"></span><span id="bizSub">Connecting…</span></p></div>
  <button class="icon-btn" id="langBtn" onclick="toggleLang()" title="Language">EN</button>
  <button class="icon-btn" id="shareBtn" onclick="openShare()" title="Share">↗</button>
</div></header>
<div class="planbar" id="planBar" hidden><div class="planbar-in warn" id="planBarIn" onclick="go('plan')"><span>⏳</span><span id="planBarTx"></span></div></div>

<div class="wrap">
<div id="sess" class="sess" hidden><div class="e">🔐</div><h2 data-i="sess_t">Session expired</h2>
<p data-i="sess_s">Reopen this app from the Ardi bot to continue.</p>
<button class="btn b-p" style="max-width:240px;margin:0 auto" onclick="location.reload()" data-i="retry">Retry</button></div>

<div id="app">
<!-- HOME -->
<section class="pg on" id="pg-home">
  <div class="grid2" id="stats"></div>
  <div class="sec-t" data-i="quick">Quick actions</div>
  <div class="qa">
    <button onclick="openProductSheet()"><span class="e">➕</span><span data-i="qa_add">Add</span></button>
    <button onclick="go('orders')"><span class="e">🧾</span><span data-i="qa_orders">Orders</span><span class="bdg" id="pendBdg" hidden></span></button>
    <button onclick="quickAi()"><span class="e">🤖</span><span data-i="qa_ai">AI</span></button>
    <button onclick="openShare()"><span class="e">🔗</span><span data-i="qa_share">Share</span></button>
  </div>
  <div class="sec-t"><span data-i="recent">Recent orders</span><span style="flex:1"></span>
    <a style="font-size:12px;color:var(--link);cursor:pointer;text-transform:none;letter-spacing:0" onclick="go('orders')" data-i="view_all">View all →</a></div>
  <div class="card" style="padding:6px 16px" id="recent"></div>
</section>

<!-- CATALOG -->
<section class="pg" id="pg-catalog">
  <div class="toolbar"><input class="search" id="q" data-i-ph="search_ph" placeholder="Search products…" oninput="renderProducts()">
    <div class="chips" id="stockChips">
      <button class="chip on" data-f="all" onclick="setStock('all',this)" data-i="f_all">All</button>
      <button class="chip" data-f="in" onclick="setStock('in',this)" data-i="f_in">In stock</button>
      <button class="chip" data-f="out" onclick="setStock('out',this)" data-i="f_out">Out of stock</button>
    </div></div>
  <div style="font-size:12px;color:var(--hint);margin:2px 2px 10px" id="prodCount"></div>
  <div id="plist"></div>
</section>

<!-- ORDERS -->
<section class="pg" id="pg-orders">
  <div class="toolbar"><div class="chips" id="ordChips" style="padding-top:2px">
    <button class="chip on" data-f="all" onclick="setOrd('all',this)" data-i="f_all">All</button>
    <button class="chip" data-f="pending" onclick="setOrd('pending',this)" data-i="f_pending">Pending</button>
    <button class="chip" data-f="confirmed" onclick="setOrd('confirmed',this)" data-i="f_conf">Confirmed</button>
    <button class="chip" data-f="completed" onclick="setOrd('completed',this)" data-i="f_done">Completed</button>
    <button class="chip" data-f="cancelled" onclick="setOrd('cancelled',this)" data-i="f_canc">Cancelled</button>
  </div></div>
  <div style="font-size:12px;color:var(--hint);margin:2px 2px 10px" id="ordCount"></div>
  <div id="olist"></div>
</section>

<!-- PLAN -->
<section class="pg" id="pg-plan">
  <div id="planHero"></div>
  <div id="planPick"></div>
  <div id="planPay" hidden>
    <div class="sec-t" data-i="pay_to">Send payment to</div>
    <div class="card" style="padding:6px 16px" id="payAccts"></div>
    <div class="sec-t" data-i="receipt">Payment receipt</div>
    <div class="card"><div class="up-zone" id="upZone" onclick="document.getElementById('recFile').click()">
        <div id="upInner"><div style="font-size:34px">🧾</div><div style="font-size:14px;font-weight:600" data-i="up_t">Tap to upload screenshot</div>
        <div style="font-size:11px;color:var(--hint)">PNG · JPG · WEBP</div></div></div>
      <input type="file" id="recFile" accept="image/png,image/jpeg,image/webp" hidden onchange="recChosen(this)">
      <button class="btn b-p" id="recBtn" onclick="recSubmit()" disabled data-i="submit_receipt">Submit payment proof</button>
    </div>
  </div>
</section>

<!-- MORE -->
<section class="pg" id="pg-more">
  <div class="sec-t" data-i="m_biz">Business</div>
  <div class="card"><div class="set-row" onclick="toggleBox('profBox')"><div class="tx"><div class="t1" data-i="profile">Store profile</div><div class="t2" id="profSum">—</div></div><span style="color:var(--hint)">›</span></div>
    <div id="profBox" hidden style="padding-top:12px">
      <label class="fl" data-i="p_name">Store name</label><input class="inp" id="pfName" maxlength="120">
      <label class="fl" data-i="p_phone">Phone</label><input class="inp" id="pfPhone" maxlength="30" inputmode="tel">
      <label class="fl" data-i="p_addr">Address</label><input class="inp" id="pfAddr" maxlength="300">
      <label class="fl" data-i="p_desc">Description</label><textarea class="inp" id="pfDesc" maxlength="1000"></textarea>
      <button class="btn b-p" onclick="saveProfile()" data-i="save">Save</button>
    </div></div>
  <div class="card"><div class="set-row" onclick="openShare()"><div class="tx"><div class="t1" data-i="share_store">Share my store</div><div class="t2" data-i="share_s">Link + QR for customers</div></div><span style="color:var(--hint)">›</span></div>
    <div class="set-row" onclick="toggleBox('chanBox')"><div class="tx"><div class="t1" data-i="channel">Sales channel</div><div class="t2" data-i="channel_s">Auto-import from Telegram channel</div></div><span style="color:var(--hint)">›</span></div>
    <div id="chanBox" hidden style="padding-top:12px;font-size:13px;color:var(--hint);line-height:1.6" data-i="channel_h">1. Add the Ardi bot as admin to your channel<br>2. Forward any channel message to the bot<br>3. New photo posts with prices are saved as products automatically.</div></div>
  <div class="sec-t">Ardi AI</div>
  <div class="card" style="padding:4px 16px">
    <div class="set-row"><div class="tx"><div class="t1" data-i="ai_reply">AI auto-reply</div><div class="t2" id="aiState">—</div></div><div class="tgl" id="aiTgl" onclick="toggleAi()"></div></div>
    <div class="set-row" onclick="toggleBox('toneBox')"><div class="tx"><div class="t1" data-i="tone">Conversation tone</div><div class="t2" id="toneName">—</div></div><span style="color:var(--hint)">›</span></div>
    <div id="toneBox" hidden style="padding:12px 0"><div class="tone-grid" id="toneGrid"></div></div>
    <div class="set-row" onclick="toggleBox('hrsBox')"><div class="tx"><div class="t1" data-i="hours">Business hours</div><div class="t2" id="hrsSum">—</div></div><span style="color:var(--hint)">›</span></div>
    <div id="hrsBox" hidden style="padding:12px 0">
      <div class="set-row"><div class="tx"><div class="t1" data-i="hours_on">Enable business hours</div></div><div class="tgl" id="hrsTgl" onclick="toggleHrs()"></div></div>
      <div style="display:flex;gap:8px;margin:10px 0"><input class="inp" id="hrsS" placeholder="09:00" style="margin:0"><input class="inp" id="hrsE" placeholder="18:00" style="margin:0"></div>
      <button class="btn b-s" onclick="saveHrs()" data-i="save">Save</button>
      <label class="fl" data-i="offline">Offline message</label><textarea class="inp" id="offMsg"></textarea>
      <button class="btn b-s" onclick="saveOff()" data-i="save">Save</button>
    </div>
  </div>
  <div class="sec-t" data-i="m_pay">Payments</div>
  <div class="card"><label class="fl" data-i="bank">Bank name</label><input class="inp" id="bkN" maxlength="100">
    <label class="fl" data-i="acc_no">Account number</label><input class="inp" id="bkA" maxlength="100" inputmode="numeric">
    <label class="fl" data-i="acc_name">Account holder</label><input class="inp" id="bkH" maxlength="255">
    <button class="btn b-p" onclick="saveBank()" data-i="save">Save</button></div>
  <div class="sec-t" data-i="m_app">App</div>
  <div class="card" style="padding:4px 16px">
    <div class="set-row" onclick="toggleLang()"><div class="tx"><div class="t1" data-i="lang">Language / ቋንቋ</div><div class="t2" id="langName">English</div></div><span style="color:var(--hint)">›</span></div>
    <div class="set-row" onclick="go('plan')"><div class="tx"><div class="t1" data-i="plan">Subscription plan</div><div class="t2" id="planSum">—</div></div><span style="color:var(--hint)">›</span></div>
  </div>
  <div class="foot">Ardi Business v2 · Made for Telegram</div>
</section>
</div><!-- /app -->
</div><!-- /wrap -->

<nav class="nav"><div class="nav-in" id="navIn">
  <button class="ni on" data-t="home" onclick="go('home')"><span class="e">🏠</span><span data-i="tab_home">Home</span></button>
  <button class="ni" data-t="catalog" onclick="go('catalog')"><span class="e">🛍️</span><span data-i="tab_cat">Catalog</span></button>
  <button class="ni" data-t="orders" onclick="go('orders')"><span class="e">🧾</span><span data-i="tab_ord">Orders</span><span class="bdg" id="navBdg" hidden></span></button>
  <button class="ni" data-t="plan" onclick="go('plan')"><span class="e">💳</span><span data-i="tab_plan">Plan</span></button>
  <button class="ni" data-t="more" onclick="go('more')"><span class="e">⋯</span><span data-i="tab_more">More</span></button>
</div></nav>

<button class="fab" id="fab" onclick="openProductSheet()" hidden>＋</button>

<div class="ov" id="ov" onclick="closeSheet()"></div>
<div class="sheet" id="sheet"><div class="sheet-in" id="sheetIn"></div></div>

<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
/* ═══ Ardi Business v2 — core ═══ */
const tg = window.Telegram?.WebApp || null;
if (tg) { tg.ready(); tg.expand(); }
const qp = new URLSearchParams(location.search);
const INIT_DATA = tg?.initData || '';
const DASH_TOKEN = qp.get('token') || '';
const $ = id => document.getElementById(id);
function esc(s){ const d=document.createElement('div'); d.appendChild(document.createTextNode(s==null?'':String(s))); return d.innerHTML; }
function hap(k){ try{ tg?.HapticFeedback?.notificationOccurred(k||'success'); }catch(e){} }
function toast(m,k){ const e=$('toast'); e.textContent=m; e.className='toast show '+(k||''); clearTimeout(e._t); e._t=setTimeout(()=>e.classList.remove('show'),2800); }
function loading(on){ $('loader').classList.toggle('on',!!on); }
function authH(){ const h={'Content-Type':'application/json'}; if(INIT_DATA)h['X-Telegram-Init-Data']=INIT_DATA; if(DASH_TOKEN)h['X-Dashboard-Token']=DASH_TOKEN; return h; }
async function api(p,o){ loading(true); try{
  const c=new AbortController(); const to=setTimeout(()=>c.abort(),20000);
  const r=await fetch(p,{headers:authH(),signal:c.signal,...(o||{})}); clearTimeout(to);
  if(r.status===401||r.status===403){ showSess(); return null; }
  let j=null; try{ j=await r.json(); }catch(e){}
  if(!r.ok){ toast((j&&j.detail)||('Error '+r.status),'err'); return null; }
  if(j&&j.error){ toast(j.error,'err'); return null; }
  return j;
}catch(e){ toast(t('net_err'),'err'); return null; }finally{ loading(false); } }
function showSess(){ $('sess').hidden=false; $('app').hidden=true; document.querySelector('.nav').style.display='none'; $('fab').hidden=true; }

/* ── i18n (English / Amharic) ── */
const T={
en:{sess_t:'Session expired',sess_s:'Reopen this app from the Ardi bot to continue.',retry:'Retry',net_err:'Network error. Try again.',
quick:'Quick actions',qa_add:'Add',qa_orders:'Orders',qa_ai:'AI',qa_share:'Share',recent:'Recent orders',view_all:'View all →',
search_ph:'Search products…',f_all:'All',f_in:'In stock',f_out:'Out of stock',f_pending:'Pending',f_conf:'Confirmed',f_done:'Completed',f_canc:'Cancelled',
pay_to:'Send payment to',receipt:'Payment receipt',up_t:'Tap to upload screenshot',submit_receipt:'Submit payment proof',
m_biz:'Business',profile:'Store profile',share_store:'Share my store',share_s:'Link for customers',channel:'Sales channel',channel_s:'Auto-import from Telegram channel',
channel_h:'1. Add the Ardi bot as admin to your channel<br>2. Forward any channel message to the bot<br>3. New photo posts with prices are saved as products automatically.',
ai_reply:'AI auto-reply',tone:'Conversation tone',hours:'Business hours',hours_on:'Enable business hours',offline:'Offline message',
bank:'Bank name',acc_no:'Account number',acc_name:'Account holder',save:'Save',m_pay:'Payments',m_app:'App',lang:'Language / ቋንቋ',plan:'Subscription plan',
tab_home:'Home',tab_cat:'Catalog',tab_ord:'Orders',tab_plan:'Plan',tab_more:'More',p_name:'Store name',p_phone:'Phone',p_addr:'Address',p_desc:'Description',
products:'Products',orders:'Orders',revenue:'Revenue',pending:'Pending',in_stock:'In stock',out:'Out of stock',edit:'Edit product',add_p:'Add product',
photo:'Photo',change:'Change',pname_ph:'e.g. Fresh Avocado',price_ph:'Price in ETB',save_p:'Save product',delete:'Delete product',cancel:'Cancel',
del_q:'Delete this product? This cannot be undone.',no_prod:'No products yet',no_prod_s:'Add your first product to start selling.',add_first:'Add product',
no_ord:'No orders',no_ord_s:'New customer orders will appear here.',customer:'Customer',phone:'Phone',address:'Address',total:'Total',status:'Status',date:'Date',items:'Items',
confirm:'Confirm',complete:'Complete',cancel_o:'Cancel order',st_pending:'Pending',st_confirmed:'Confirmed',st_completed:'Completed',st_cancelled:'Cancelled',
ai_on:'AI replies to customers automatically',ai_off:'AI is off — you reply manually',trial:'Trial',active:'Active',awaiting:'Awaiting payment',expired:'Expired',suspended:'Suspended',
days_left:'days left',choose_plan:'Choose a plan',monthly:'Monthly',yearly:'Yearly',per_mo:'/mo',best:'Best value',mo_desc:'Billed monthly · cancel anytime',yr_desc:'2 months free · best for growing stores',
cur_monthly:'Monthly plan active',cur_yearly:'Yearly plan active',await_t:'Payment sent — waiting for admin confirmation.',exp_t:'Subscribe to keep selling with Ardi AI.',
sub_now:'Subscribe now',proceed:'Proceed with this plan?',plan_ok:'Plan selected — send payment below',copy:'Copy link',open:'Open in Telegram',copied:'Link copied ✓',
shr_t:'Your store link',shr_s:'Share it anywhere — customers chat & order automatically.',prof_ok:'Profile saved ✓',set_ok:'Saved ✓',ai_on_t:'AI is ON 🤖',ai_off_t:'AI is OFF ⏸️',
tone_ok:'Tone saved ✓',hrs_ok:'Hours saved ✓',off_ok:'Message saved ✓',bank_ok:'Payment info saved ✓',ord_ok:'Order updated ✓',prod_ok:'Product saved ✓',prod_del:'Product deleted 🗑️',
rec_ok:'Receipt submitted! Admin will verify. 📩',sel_img:'Choose an image first',enter_name:'Enter a name (2+ letters)',enter_price:'Enter a valid price',
enter_hrs:'Enter start & end time (HH:MM)',bad_hrs:'Use 24h format like 09:00',call:'Call',chat:'Chat'},
am:{sess_t:'ክፍለ-ጊዜው አልቋል',sess_s:'ለመቀጠል መተግበሪያውን ከArdi bot እንደገና ይክፈቱ።',retry:'እንደገና ሞክር',net_err:'የኔትወርክ ስህተት። እንደገና ይሞክሩ።',
quick:'ፈጣን እርምጃዎች',qa_add:'ጨምር',qa_orders:'ትዕዛዞች',qa_ai:'AI',qa_share:'አጋራ',recent:'የቅርብ ጊዜ ትዕዛዞች',view_all:'ሁሉንም →',
search_ph:'ምርቶችን ፈልግ…',f_all:'ሁሉም',f_in:'በስቶክ ያለ',f_out:'ያለቀ',f_pending:'በመጠባበቅ ላይ',f_conf:'ተቀባይነት ያገኘ',f_done:'ተጠናቋል',f_canc:'ተሰርዟል',
pay_to:'ክፍያ ይላኩ ወደ',receipt:'የክፍያ ደረሰኝ',up_t:'ስክሪንሾት ለመስቀል ይንኩ',submit_receipt:'የክፍያ ማረጋገጫ ላክ',
m_biz:'ንግድ',profile:'የሱቅ መገለጫ',share_store:'ሱቄን አጋራ',share_s:'ለደንበኞች ሊንክ',channel:'የሽያጭ ቻናል',channel_s:'ከቴሌግራም ቻናል በራስ-ሰር',
channel_h:'1. Ardi botን በቻናልዎ አድሚን ያድርጉ<br>2. ማንኛውንም የቻናል መልእክት ለbot ያስተላልፉ<br>3. አዳዲስ የምስል ልጥፎች በራስ-ሰር እንደ ምርት ይቀመጣሉ።',
ai_reply:'AI በራስ-ሰር መልስ',tone:'የውይይት ዘይቤ',hours:'የስራ ሰዓት',hours_on:'የስራ ሰዓት አንቃ',offline:'ከስራ ሰዓት ውጪ መልእክት',
bank:'የባንክ ስም',acc_no:'የሂሳብ ቁጥር',acc_name:'የሂሳብ ባለቤት',save:'አስቀምጥ',m_pay:'ክፍያዎች',m_app:'መተግበሪያ',lang:'Language / ቋንቋ',plan:'የክፍያ እቅድ',
tab_home:'መነሻ',tab_cat:'ምርቶች',tab_ord:'ትዕዛዞች',tab_plan:'ፕላን',tab_more:'ተጨማሪ',p_name:'የሱቅ ስም',p_phone:'ስልክ',p_addr:'አድራሻ',p_desc:'መግለጫ',
products:'ምርቶች',orders:'ትዕዛዞች',revenue:'ገቢ',pending:'በመጠባበቅ ላይ',in_stock:'በስቶክ ያለ',out:'ያለቀ',edit:'ምርት አርም',add_p:'ምርት ጨምር',
photo:'ፎቶ',change:'ቀይር',pname_ph:'ለምሳሌ ትኩስ አቮካዶ',price_ph:'ዋጋ በኢቲቢ',save_p:'ምርቱን አስቀምጥ',delete:'ምርቱን ሰርዝ',cancel:'ሰርዝ',
del_q:'ይህን ምርት ይሰርዙ? ይህ ሊቀለበስ አይችልም።',no_prod:'ምንም ምርቶች የሉም',no_prod_s:'ለመሸጥ የመጀመሪያ ምርትዎን ይጨምሩ።',add_first:'ምርት ጨምር',
no_ord:'ምንም ትዕዛዞች የሉም',no_ord_s:'አዳዲስ የደንበኛ ትዕዛዞች እዚህ ይታያሉ።',customer:'ደንበኛ',phone:'ስልክ',address:'አድራሻ',total:'ጠቅላላ',status:'ሁኔታ',date:'ቀን',items:'ዕቃዎች',
confirm:'አጽድቅ',complete:'አጠናቅቅ',cancel_o:'ትዕዛዙን ሰርዝ',st_pending:'በመጠባበቅ ላይ',st_confirmed:'ተቀባይነት ያገኘ',st_completed:'ተጠናቋል',st_cancelled:'ተሰርዟል',
ai_on:'AI ለደንበኞች በራስ-ሰር ይመልሳል',ai_off:'AI ጠፍቷል — እርስዎ በእጅ ይመልሳሉ',trial:'ሙከራ',active:'ንቁ',awaiting:'ክፍያ በመጠባበቅ ላይ',expired:'ጊዜው አልፎበታል',suspended:'ታግዷል',
days_left:'ቀናት ቀርተዋል',choose_plan:'እቅድ ይምረጡ',monthly:'ወርሃዊ',yearly:'ዓመታዊ',per_mo:'/ወር',best:'ምርጥ ምርጫ',mo_desc:'በየወሩ ክፍያ · በማንኛውም ጊዜ ይሰርዙ',yr_desc:'2 ወር ነፃ · ለሚያድጉ ሱቆች',
cur_monthly:'ወርሃዊ እቅድ ንቁ ነው',cur_yearly:'ዓመታዊ እቅድ ንቁ ነው',await_t:'ክፍያ ተልኳል — የአድሚን ማረጋገጫ በመጠባበቅ ላይ።',exp_t:'ከArdi AI ጋር ለመሸጥ ይመዝገቡ።',
sub_now:'አሁን ይመዝገቡ',proceed:'በዚህ እቅድ ይቀጥሉ?',plan_ok:'እቅድ ተመርጧል — ክፍያ ከዚህ በታች ይላኩ',copy:'ሊንኩን ቅዳ',open:'በቴሌግራም ክፈት',copied:'ሊንኩ ተቀድቷል ✓',
shr_t:'የሱቅዎ ሊንክ',shr_s:'የትም ቦታ ያጋሩ — ደንበኞች በራስ-ሰር ያዣሉ።',prof_ok:'መገለጫ ተቀምጧል ✓',set_ok:'ተቀምጧል ✓',ai_on_t:'AI በርቷል 🤖',ai_off_t:'AI ጠፍቷል ⏸️',
tone_ok:'ዘይቤ ተቀምጧል ✓',hrs_ok:'ሰዓት ተቀምጧል ✓',off_ok:'መልእክት ተቀምጧል ✓',bank_ok:'የክፍያ መረጃ ተቀምጧል ✓',ord_ok:'ትዕዛዝ ታድሷል ✓',prod_ok:'ምርት ተቀምጧል ✓',prod_del:'ምርት ተሰርዟል 🗑️',
rec_ok:'ደረሰኝ ተልኳል! አድሚን ያረጋግጣል። 📩',sel_img:'መጀመሪያ ምስል ይምረጡ',enter_name:'ስም ያስገቡ (2+ ፊደላት)',enter_price:'ትክክለኛ ዋጋ ያስገቡ',
enter_hrs:'መጀመሪያ እና መጨረሻ ሰዓት ያስገቡ (HH:MM)',bad_hrs:'የ24-ሰዓት ቅርጸት ይጠቀሙ ለምሳሌ 09:00',call:'ደውል',chat:'ውይይት'}};
let LANG=localStorage.getItem('ardi_lang')||(((tg?.initDataUnsafe?.user?.language_code)||'en').startsWith('am')?'am':'en');
const t=k=>(T[LANG]&&T[LANG][k])??T.en[k]??k;
function applyI18n(){ document.querySelectorAll('[data-i]').forEach(el=>{el.textContent=t(el.dataset.i)}); document.querySelectorAll('[data-i-ph]').forEach(el=>{el.placeholder=t(el.dataset.iPh)}); $('langBtn').textContent=LANG==='am'?'EN':'አማ'; const ln=$('langName'); if(ln)ln.textContent=LANG==='am'?'አማርኛ':'English'; }
function toggleLang(){ LANG=LANG==='am'?'en':'am'; localStorage.setItem('ardi_lang',LANG); applyI18n(); renderAll(); hap('light'); }

/* ── router + sheets ── */
let TAB='home';
function go(tab){ TAB=tab; closeSheet(true);
  document.querySelectorAll('.pg').forEach(x=>x.classList.remove('on')); $('pg-'+tab).classList.add('on');
  document.querySelectorAll('#navIn .ni').forEach(b=>b.classList.toggle('on',b.dataset.t===tab));
  $('fab').hidden=tab!=='catalog'; if(tg?.BackButton)tg.BackButton.hide();
  ({home:loadHome,catalog:loadCatalog,orders:loadOrders,plan:loadPlan,more:loadMore}[tab]||(()=>{}))();
  try{scrollTo({top:0})}catch(e){} }
function openSheet(h){ $('sheetIn').innerHTML=h; $('ov').classList.add('on'); $('sheet').classList.add('on'); if(tg){tg.BackButton.show();try{tg.enableClosingConfirmation()}catch(e){}} hap('light'); }
function closeSheet(s){ if(!$('sheet').classList.contains('on'))return; $('ov').classList.remove('on'); $('sheet').classList.remove('on'); if(tg){tg.BackButton.hide();try{tg.disableClosingConfirmation()}catch(e){}} }
if(tg){ try{tg.BackButton.onClick(()=>closeSheet())}catch(e){} }
function toggleBox(id){ const e=$(id); e.hidden=!e.hidden; }

/* ── shared state + format ── */
const S={dash:null,products:[],orders:[],sub:null,settings:null,profile:null,stockF:'all',ordF:'all',photoB64:null,recB64:null,planSel:null,pend:0};
const fmtN=n=>'ETB '+(+n||0).toLocaleString();
function ago(iso){ if(!iso)return''; const s=(Date.now()-new Date(iso))/1e3; if(s<60)return'· now'; if(s<3600)return'· '+Math.floor(s/60)+'m'; if(s<86400)return'· '+Math.floor(s/3600)+'h'; return'· '+Math.floor(s/86400)+'d'; }
function pill(st){ const m={pending:['p-brand',t('st_pending')],confirmed:['p-ok',t('st_confirmed')],completed:['p-ok',t('st_completed')],cancelled:['p-bad',t('st_cancelled')],active:['p-ok',t('active')],trial:['p-warn',t('trial')],awaiting_payment:['p-warn',t('awaiting')],expired:['p-bad',t('expired')],suspended:['p-bad',t('suspended')]}; const v=m[st]||['p-brand',st]; return `<span class="pill ${v[0]}">${esc(v[1])}</span>`; }
function skel(n){ return Array(n).fill('<div class="card"><div class="skl" style="height:16px;width:60%;margin-bottom:8px">x</div><div class="skl" style="height:12px;width:90%">x</div></div>').join(''); }
function empty(e,ti,s,btn,fn){ return `<div class="empty"><div class="e">${e}</div><div class="t">${ti}</div><div class="s">${s}</div>${btn?`<button class="btn b-p" style="max-width:220px;margin:0 auto" onclick="${fn}">${btn}</button>`:''}</div>`; }
function renderAll(){ applyI18n(); go(TAB); }
function confirmDlg(msg,fn){ if(tg?.showConfirm){tg.showConfirm(msg,ok=>{if(ok)fn()})} else if(confirm(msg))fn(); }

/* ── HOME ── */
async function loadHome(){
  $('stats').innerHTML='<div class="stat"><div class="skl" style="height:20px">x</div></div>'.repeat(4);
  $('recent').innerHTML=skel(2);
  const d=await api('/api/business/dashboard'); if(!d)return; S.dash=d;
  const ai=S.settings?.ai_active;
  $('ava').textContent=(d.name||'A')[0].toUpperCase();
  $('bizName').textContent=d.name||'Ardi Business';
  $('bizSub').textContent=(d.subscription_status||'trial')+(d.recent_orders?.length?'':' · '+t('no_ord'));
  $('aiDot').className='dot'+(ai===false?' off':'');
  $('stats').innerHTML=[
    {e:'🛍️',v:d.product_count??0,l:t('products')},{e:'🧾',v:d.order_count??0,l:t('orders')},
    {e:'💰',v:fmtN(d.revenue),l:t('revenue')},{e:'⏳',v:d.subscription_status||'—',l:t('plan')}
  ].map(s=>`<div class="stat"><div class="ic" style="background:var(--sec)">${s.e}</div><div class="v" style="font-size:${String(s.v).length>8?'16px':'21px'}">${esc(String(s.v))}</div><div class="l">${esc(s.l)}</div></div>`).join('');
  const ro=d.recent_orders||[];
  $('recent').innerHTML=ro.length?ro.map(o=>`<div class="row" onclick="openOrder(${o.id})"><div class="im ph">🧾</div><div class="tx"><div class="t1">${esc(o.customer_name||t('customer'))}</div><div class="t2">${ago(o.created_at)}</div></div><div class="rt"><div class="amt">${fmtN(o.total_price)}</div><div style="margin-top:4px">${pill(o.status)}</div></div></div>`).join('')
    :empty('📭',t('no_ord'),t('no_ord_s'));
  refreshPlanBar(d.subscription_status); refreshBadge();
}
async function refreshBadge(){
  const d=await api('/api/business/orders?filter=pending'); if(!d)return;
  S.pend=(d.orders||[]).length;
  const b=$('pendBdg'),n=$('navBdg');
  [b,n].forEach(x=>{x.hidden=!S.pend; x.textContent=S.pend>99?'99+':S.pend});
  const q=document.querySelector('#qa_orders'); if(q)q.textContent=S.pend;
}
function refreshPlanBar(st){
  const bar=$('planBar'),inn=$('planBarIn'),tx=$('planBarTx');
  if(!st||st==='active'){bar.hidden=true;return}
  bar.hidden=false;
  inn.className='planbar-in '+((st==='expired'||st==='suspended')?'bad':'warn');
  tx.textContent=st==='trial'?t('exp_t'):st==='awaiting_payment'?t('await_t'):t(st);
}
async function quickAi(){
  const s=S.settings||await api('/api/business/settings'); if(!s)return; S.settings=s;
  const d=await api('/api/business/ai/toggle',{method:'POST'}); if(d&&d.success){S.settings.ai_active=d.active;toast(d.active?t('ai_on_t'):t('ai_off_t'),'ok');hap();loadHome()}
}

/* ── CATALOG ── */
async function loadCatalog(){
  $('plist').innerHTML=skel(3);
  const d=await api('/api/business/products'); if(!d)return;
  S.products=d.products||[]; renderProducts();
}
function setStock(f,el){ S.stockF=f; document.querySelectorAll('#stockChips .chip').forEach(c=>c.classList.toggle('on',c===el)); renderProducts(); }
function renderProducts(){
  const q=($('q').value||'').toLowerCase();
  const items=S.products.filter(p=>(!q||(p.name||'').toLowerCase().includes(q))&&(S.stockF==='all'||(S.stockF==='in'?p.available:!p.available)));
  $('prodCount').textContent=`${items.length} / ${S.products.length} · ${t('products')}`;
  $('plist').innerHTML=items.length?items.map(p=>`
    <div class="card" style="padding:8px 14px"><div class="row" style="border:none;padding:8px 0" onclick="openProductSheet(${p.id})">
    ${p.photo_url?`<img class="im" src="${esc(p.photo_url)}" loading="lazy" onerror="this.outerHTML='<div class=&quot;im ph&quot;>📦</div>'">`:'<div class="im ph">📦</div>'}
    <div class="tx"><div class="t1">${esc(p.name)}</div><div class="t2">${p.available?t('in_stock'):t('out')}</div></div>
    <div class="rt"><div class="amt">${fmtN(p.price)}</div></div></div></div>`).join('')
    :empty('📦',t('no_prod'),t('no_prod_s'),t('add_first'),'openProductSheet()');
}
function openProductSheet(id){
  const p=id?S.products.find(x=>x.id===id):null; S.editId=id||null; S.photoB64=null;
  openSheet(`<div class="grab"></div><h2>${p?esc(p.name):t('add_p')}</h2>
  <div class="photo-pick"><div id="ppPrev">${p?.photo_url?`<img src="${esc(p.photo_url)}" style="width:72px;height:72px;border-radius:14px;object-fit:cover">`:'<div class="ph">📷</div>'}</div>
  <div><div style="font-size:13px;font-weight:700">${t('photo')}</div>
  <button class="btn b-s" style="width:auto;padding:8px 14px;font-size:13px;margin:6px 0 0" onclick="document.getElementById('ppFile').click()">${t('change')}</button>
  <input type="file" id="ppFile" accept="image/png,image/jpeg,image/webp" hidden onchange="photoPick(this)"></div></div>
  <label class="fl">${t('p_name')}</label><input class="inp" id="ppName" maxlength="120" placeholder="${t('pname_ph')}" value="${esc(p?.name||'')}">
  <label class="fl">ETB</label><input class="inp" id="ppPrice" type="number" min="0" max="9999999" inputmode="decimal" placeholder="${t('price_ph')}" value="${esc(p?.price??'')}">
  <div class="set-row"><div class="tx"><div class="t1">${t('in_stock')}</div></div><div class="tgl ${!p||p.available?'on':''}" id="ppAvail" onclick="this.classList.toggle('on')"></div></div>
  <div style="height:12px"></div>
  <button class="btn b-p" onclick="saveProduct()">${p?t('save_p'):t('add_p')}</button>
  ${p?`<button class="btn b-bad" onclick="delProduct(${p.id})">${t('delete')}</button>`:''}
  <button class="btn b-s" onclick="closeSheet()">${t('cancel')}</button>`);
  setTimeout(()=>$('ppName')?.focus(),350);
}
function photoPick(inp){
  const f=inp.files[0]; if(!f)return;
  compressImage(f).then(b64=>{ S.photoB64=b64; $('ppPrev').innerHTML=`<img src="data:image/jpeg;base64,${b64}" style="width:72px;height:72px;border-radius:14px;object-fit:cover">`; });
}
function compressImage(file){
  return new Promise(res=>{ const img=new Image(); const url=URL.createObjectURL(file);
    img.onload=()=>{ const M=1280; let w=img.width,h=img.height; const r=Math.min(1,M/Math.max(w,h)); w=Math.round(w*r); h=Math.round(h*r);
      const c=document.createElement('canvas'); c.width=w; c.height=h; c.getContext('2d').drawImage(img,0,0,w,h);
      URL.revokeObjectURL(url); res(c.toDataURL('image/jpeg',.82).split(',')[1]); };
    img.onerror=()=>{URL.revokeObjectURL(url); const fr=new FileReader(); fr.onload=()=>res(fr.result.split(',')[1]); fr.readAsDataURL(file);};
    img.src=url; });
}
async function saveProduct(){
  const name=($('ppName').value||'').trim(), price=parseFloat($('ppPrice').value);
  if(name.length<2){toast(t('enter_name'),'err');return}
  if(!(price>=0)||price>9999999){toast(t('enter_price'),'err');return}
  const avail=$('ppAvail').classList.contains('on');
  const body={name,price,photo_data:S.photoB64};
  let d;
  if(S.editId){ d=await api('/api/business/products/'+S.editId,{method:'PATCH',body:JSON.stringify(body)}); }
  else{ d=await api('/api/business/products',{method:'POST',body:JSON.stringify(body)}); }
  if(d&&(d.success)){ toast(t('prod_ok'),'ok'); hap(); closeSheet(true); loadCatalog(); }
}
function delProduct(id){ confirmDlg(t('del_q'),async()=>{ const d=await api('/api/business/products/'+id,{method:'DELETE'}); if(d&&d.success){toast(t('prod_del'),'ok');hap();closeSheet(true);loadCatalog()} }); }

/* ── ORDERS ── */
async function loadOrders(){ await fetchOrders(); }
async function fetchOrders(){
  $('olist').innerHTML=skel(3);
  const d=await api('/api/business/orders?filter='+S.ordF); if(!d)return;
  S.orders=d.orders||[]; $('ordCount').textContent=`${S.orders.length} · ${t('orders')}`;
  $('olist').innerHTML=S.orders.length?S.orders.map(o=>`
    <div class="card" style="padding:6px 16px"><div class="row" style="border:none" onclick="openOrder(${o.id})">
    <div class="im ph">🧾</div><div class="tx"><div class="t1">#${o.id} · ${esc(o.customer_name||t('customer'))}</div>
    <div class="t2">${o.item_count||0} ${t('items')} ${ago(o.created_at)}</div></div>
    <div class="rt"><div class="amt">${fmtN(o.total_price)}</div><div style="margin-top:4px">${pill(o.status)}</div></div></div></div>`).join('')
    :empty('🧾',t('no_ord'),t('no_ord_s'));
}
function setOrd(f,el){ S.ordF=f; document.querySelectorAll('#ordChips .chip').forEach(c=>c.classList.toggle('on',c===el)); fetchOrders(); }
async function openOrder(id){
  openSheet(`<div class="grab"></div><div id="ordSheet">${skel(2)}</div>`);
  const d=await api('/api/business/orders/'+id); if(!d){closeSheet(true);return}
  const steps=['pending','confirmed','completed'];
  const cur=d.status==='cancelled'?'cancelled':d.status;
  const idx=steps.indexOf(cur);
  const tl=d.status==='cancelled'
    ?`<div class="tl"><div class="stp"><div class="bar"><div class="dt" style="background:var(--bad)"></div></div><div class="tt">${t('st_cancelled')}</div></div></div>`
    :`<div class="tl">${steps.map((s,i)=>`<div class="stp ${i<idx?'done':i===idx?'now':''}"><div class="bar"><div class="dt"></div>${i<2?'<div class="ln"></div>':''}</div><div class="tt">${t('st_'+s)}${i===idx?`<small>${t('status')}</small>`:''}</div></div>`).join('')}</div>`;
  const acts=d.status==='pending'
    ?`<div class="btn-row"><button class="btn b-ok" onclick="setOrdStatus(${d.id},'confirmed')">${t('confirm')}</button><button class="btn b-bad" onclick="setOrdStatus(${d.id},'cancelled')">${t('cancel_o')}</button></div>`
    :d.status==='confirmed'
    ?`<div class="btn-row"><button class="btn b-ok" onclick="setOrdStatus(${d.id},'completed')">${t('complete')}</button><button class="btn b-bad" onclick="setOrdStatus(${d.id},'cancelled')">${t('cancel_o')}</button></div>`:'';
  $('ordSheet').innerHTML=`<h2>#${d.id} ${pill(d.status)}</h2>
    <div class="card">
      <div class="kv"><span class="k">${t('customer')}</span><span class="v">${esc(d.customer_name||'—')}</span></div>
      <div class="kv"><span class="k">${t('phone')}</span><span class="v">${d.customer_phone?`<a href="tel:${esc(d.customer_phone)}" style="color:var(--link)">${esc(d.customer_phone)}</a>`:'—'}</span></div>
      <div class="kv"><span class="k">${t('address')}</span><span class="v">${esc(d.customer_address||'—')}</span></div>
      <div class="kv"><span class="k">${t('date')}</span><span class="v">${d.created_at?esc(new Date(d.created_at).toLocaleString()):'—'}</span></div>
    </div>
    <div class="sec-t">${t('items')}</div>
    <div class="card" style="padding:6px 16px">${(d.items||[]).map(i=>`<div class="row"><div class="tx"><div class="t1">${esc(i.product_name||'Item')} ×${i.quantity||1}</div></div><div class="rt amt">${fmtN(i.unit_price)}</div></div>`).join('')||'<div class="empty">—</div>'}
      <div class="kv" style="padding:12px 0 6px"><span class="k">${t('total')}</span><span class="v" style="font-size:16px">${fmtN(d.total_price)}</span></div></div>
    <div class="sec-t">${t('status')}</div><div class="card">${tl}</div>${acts}`;
}
async function setOrdStatus(id,st){
  confirmDlg(st==='cancelled'?t('cancel_o')+'?':t(st==='completed'?'complete':'confirm')+'?',async()=>{
    const d=await api('/api/business/orders/'+id+'/status',{method:'POST',body:JSON.stringify({status:st})});
    if(d&&d.success){toast(t('ord_ok'),'ok');hap();closeSheet(true);fetchOrders();refreshBadge()}
  });
}

/* ── PLAN ── */
const PLANS=[{id:'monthly',e:'📆',p:1200},{id:'yearly',e:'📅',p:12000}];
async function loadPlan(){
  $('planHero').innerHTML=skel(1); $('planPick').innerHTML=''; $('planPay').hidden=true;
  const d=await api('/api/business/subscription'); if(!d)return; S.sub=d;
  const st=d.status||'trial', days=d.days_left||0;
  const cls=st==='active'?'ok':(st==='expired'||st==='suspended')?'bad':'warn';
  const icon=st==='active'?'✅':st==='trial'?'⏳':st==='awaiting_payment'?'📩':'🔒';
  const title=st==='active'?(d.plan==='yearly'?t('cur_yearly'):t('cur_monthly')):t(st==='awaiting_payment'?'awaiting':st);
  const sub=st==='active'&&days>0?`${days} ${t('days_left')}`:st==='trial'&&days>0?`${days} ${t('days_left')}`:t(st==='awaiting_payment'?'await_t':'exp_t');
  $('planHero').innerHTML=`<div class="hero ${cls}"><div class="e">${icon}</div><div class="t">${esc(title)}</div><div class="s">${esc(sub)}</div></div>`;
  $('planSum').textContent=title;
  if(st==='active'){ $('planPick').innerHTML=''; return; }
  if(st==='awaiting_payment'){
    $('planPick').innerHTML=`<div class="card" style="text-align:center">${t('await_t')}</div>`;
    S.planSel=d.plan||d.selected||'monthly'; renderPay(d.payment_methods||[]); return;
  }
  S.planSel=d.selected||null;
  $('planPick').innerHTML=`<div class="sec-t">${t('choose_plan')}</div><div class="plans">${PLANS.map(p=>`
    <div class="plan ${S.planSel===p.id?'sel':''}" onclick="selPlan('${p.id}')">${p.id==='yearly'?`<div class="bv">${t('best')}</div>`:''}
    <div class="e">${p.e}</div><div class="n">${t(p.id)}</div><div class="p">${p.p.toLocaleString()}<small> ETB${t('per_mo')}</small></div>
    <div class="d">${t(p.id==='yearly'?'yr_desc':'mo_desc')}</div></div>`).join('')}</div>
    <div id="planCta"></div>`;
  if(S.planSel){ $('planCta').innerHTML=`<button class="btn b-p" onclick="selPlan('${S.planSel}')">${t('sub_now')}</button>`; renderPay(d.payment_methods||[]); }
}
async function selPlan(plan){
  confirmDlg((plan==='yearly'?'📅 Yearly · 12,000 ETB':'📆 Monthly · 1,200 ETB')+' — '+t('proceed'),async()=>{
    const d=await api('/api/business/subscription/select-plan',{method:'POST',body:JSON.stringify({plan})});
    if(d&&d.success){toast(t('plan_ok'),'ok');hap();loadPlan()}
  });
}
function renderPay(methods){
  if(!methods.length){$('planPay').hidden=true;return}
  $('planPay').hidden=false;
  $('payAccts').innerHTML=methods.map(m=>`<div class="pay-acct"><div class="n">${m.name==='cbe'?'🏦 CBE Birr':'📱 Telebirr'}</div>
    <div class="kv"><span class="k">${t('acc_name')}</span><span class="v">${esc(m.account_name||'')}</span></div>
    <div class="kv"><span class="k">${t('acc_no')}</span><span class="v">${esc(m.account_number||'')}</span></div></div>`).join('');
}
function recChosen(inp){
  const f=inp.files[0]; if(!f){S.recB64=null;$('recBtn').disabled=true;$('upInner').innerHTML='';return}
  compressImage(f).then(b=>{ S.recB64=b; $('recBtn').disabled=false;
    $('upZone').classList.add('has'); $('upInner').innerHTML=`<img src="data:image/jpeg;base64,${b}"><div style="font-size:12px;color:var(--ok);font-weight:700">✓ ${esc(f.name)}</div>`; });
}
async function recSubmit(){
  if(!S.recB64){toast(t('sel_img'),'err');return}
  const d=await api('/api/business/subscription/upload-receipt',{method:'POST',body:JSON.stringify({photo_data:S.recB64})});
  if(d&&d.success){toast(t('rec_ok'),'ok');hap();S.recB64=null;loadPlan()}
}

/* ── MORE: profile, AI, hours, bank ── */
const TONES=[{id:'friendly',e:'😊'},{id:'professional',e:'🤵'},{id:'casual',e:'😎'},{id:'formal',e:'🎩'},{id:'witty',e:'😜'}];
const TONE_NM={friendly:['Friendly','ተግባቢ'],professional:['Professional','ሙያዊ'],casual:['Casual','ቀላል'],formal:['Formal','ኦፊሴላዊ'],witty:['Witty','አስቂኝ']};
async function loadMore(){
  const [p,s]=await Promise.all([api('/api/business/profile'),api('/api/business/settings')]);
  if(p){ S.profile=p; $('pfName').value=p.name||''; $('pfPhone').value=p.phone||''; $('pfAddr').value=p.address||''; $('pfDesc').value=p.description||'';
    $('profSum').textContent=p.name||'—'; $('ava').textContent=(p.name||'A')[0].toUpperCase(); }
  if(s){ S.settings=s;
    $('aiTgl').classList.toggle('on',!!s.ai_active); $('aiState').textContent=s.ai_active?t('ai_on'):t('ai_off');
    $('aiDot').className='dot'+(s.ai_active?'':' off');
    renderTones(s.ai_tone||'friendly');
    $('hrsTgl').classList.toggle('on',!!s.business_hours_enabled);
    $('hrsS').value=s.business_hours_start||''; $('hrsE').value=s.business_hours_end||'';
    $('hrsSum').textContent=s.business_hours_enabled?((s.business_hours_start||'?')+'–'+(s.business_hours_end||'?')):t('hours');
    $('offMsg').value=s.ai_offline_message||'';
    $('bkN').value=s.order_bank_name||''; $('bkA').value=s.order_bank_account||''; $('bkH').value=s.order_account_holder||'';
    $('planSum').textContent=s.subscription_status||'—';
    $('bizSub').textContent=s.subscription_status||'';
  }
}
function renderTones(cur){
  $('toneGrid').innerHTML=TONES.map(x=>{const nm=TONE_NM[x.id];return `<button class="tone ${cur===x.id?'on':''}" onclick="pickTone('${x.id}')"><div class="e">${x.e}</div><div class="n">${LANG==='am'?nm[1]:nm[0]}</div></button>`}).join('');
  const nm=TONE_NM[cur]||TONE_NM.friendly; $('toneName').textContent=LANG==='am'?nm[1]:nm[0];
}
async function saveProfile(){
  const name=($('pfName').value||'').trim();
  if(name.length<2){toast(t('enter_name'),'err');return}
  const d=await api('/api/business/profile',{method:'PATCH',body:JSON.stringify({name,phone:$('pfPhone').value.trim(),address:$('pfAddr').value.trim(),description:$('pfDesc').value.trim()})});
  if(d&&d.success){toast(t('prof_ok'),'ok');hap();loadMore()}
}
async function toggleAi(){ const d=await api('/api/business/ai/toggle',{method:'POST'}); if(d&&d.success){S.settings.ai_active=d.active;loadMore();toast(d.active?t('ai_on_t'):t('ai_off_t'),'ok');hap()} }
async function pickTone(id){ const d=await api('/api/business/settings',{method:'PATCH',body:JSON.stringify({ai_tone:id})}); if(d&&d.success){toast(t('tone_ok'),'ok');hap();loadMore()} }
function toggleHrs(){ $('hrsTgl').classList.toggle('on'); }
function validTime(v){ return /^([01]\d|2[0-3]):[0-5]\d$/.test(v.trim()); }
async function saveHrs(){
  const on=$('hrsTgl').classList.contains('on'), s=$('hrsS').value.trim(), e=$('hrsE').value.trim();
  if(on&&(!validTime(s)||!validTime(e))){toast(t('bad_hrs'),'err');return}
  const d=await api('/api/business/settings',{method:'PATCH',body:JSON.stringify({business_hours_enabled:on,business_hours_start:s||null,business_hours_end:e||null})});
  if(d&&d.success){toast(t('hrs_ok'),'ok');hap();loadMore()}
}
async function saveOff(){ const d=await api('/api/business/settings',{method:'PATCH',body:JSON.stringify({ai_offline_message:$('offMsg').value})}); if(d&&d.success){toast(t('off_ok'),'ok');hap()} }
async function saveBank(){ const d=await api('/api/business/settings',{method:'PATCH',body:JSON.stringify({order_bank_name:$('bkN').value,order_bank_account:$('bkA').value,order_account_holder:$('bkH').value})}); if(d&&d.success){toast(t('bank_ok'),'ok');hap()} }

/* ── SHARE ── */
async function openShare(){
  openSheet(`<div class="grab"></div><h2>${t('shr_t')}</h2><div id="shrBody">${skel(1)}</div>`);
  const d=await api('/api/business/share'); if(!d){closeSheet(true);return}
  $('shrBody').innerHTML=`<p style="font-size:13px;color:var(--hint);margin-bottom:12px">${t('shr_s')}</p>
    <div class="card" style="word-break:break-all;font-size:13px;font-weight:600" id="shrLink">${esc(d.deep_link)}</div>
    <div class="btn-row"><button class="btn b-p" onclick="copyLink()">${t('copy')}</button>
    <button class="btn b-s" onclick="try{tg?.openTelegramLink('${esc(d.deep_link)}')}catch(e){}">${t('open')}</button></div>`;
  S.shareLink=d.deep_link;
}
function copyLink(){ const l=S.shareLink||''; (navigator.clipboard?navigator.clipboard.writeText(l):Promise.reject()).then(()=>toast(t('copied'),'ok')).catch(()=>{try{tg?.openTelegramLink('https://t.me/share/url?url='+encodeURIComponent(l))}catch(e){toast(l)}}); }

/* ── boot ── */
applyI18n(); loadHome();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    # Do NOT embed ADMIN_API_KEY — the browser bundle is public.
    # Admin key is read from localStorage (set via /business or manual entry).
    return ADMIN_HTML.replace("{{ADMIN_API_KEY}}", "")


@app.get("/business", response_class=HTMLResponse)
async def business_miniapp():
    return BIZ_HTML


# ═══════════════════════════════════════════════════════════════
# ADMIN API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/admin/dashboard")
async def api_dashboard(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, User, Order, OrderItem
        from sqlalchemy import select, func
        from datetime import datetime, timedelta, timezone

        async with async_session() as s:
            biz = (await s.execute(select(func.count(Business.id)))).scalar() or 0
            act = (await s.execute(select(func.count(Business.id)).where(Business.subscription_status == "active"))).scalar() or 0
            tr = (await s.execute(select(func.count(Business.id)).where(Business.subscription_status == "trial"))).scalar() or 0
            usr = (await s.execute(select(func.count(User.id)))).scalar() or 0
            cut = datetime.now(timezone.utc) - timedelta(days=30)
            o30 = (await s.execute(select(func.count(Order.id)).where(Order.created_at >= cut))).scalar() or 0
            pen = (await s.execute(select(func.count(Order.id)).where(Order.status == "pending"))).scalar() or 0
            rev = (await s.execute(select(func.coalesce(func.sum(Order.total_price), 0)).where(Order.status.in_(["confirmed", "completed"]), Order.created_at >= cut))).scalar() or 0.0
            avg = (await s.execute(select(func.coalesce(func.avg(Order.total_price), 0)).where(Order.status.in_(["confirmed", "completed"])))).scalar() or 0.0
            recent = (await s.execute(select(Order).order_by(Order.created_at.desc()).limit(5))).scalars().all()
            ro = []
            for o in recent:
                bb = await s.get(Business, o.business_id)
                ic_ = (await s.execute(select(func.count(OrderItem.id)).where(OrderItem.order_id == o.id))).scalar() or 0
                ro.append({"id": o.id, "customer_name": o.customer_name, "business_name": bb.name if bb else "", "total_price": str(o.total_price), "status": o.status, "item_count": ic_, "created_at": o.created_at.isoformat() if o.created_at else ""})
        from config import SUBSCRIPTION_MONTHLY
        return {"bot_online": (time.monotonic() - bot_last_heartbeat) < HEARTBEAT_TIMEOUT, "businesses": biz, "active_subscriptions": act, "trial_count": tr, "users": usr, "orders_30d": o30, "pending_orders": pen, "sub_revenue": round(act * SUBSCRIPTION_MONTHLY, 2), "avg_order_value": round(float(avg), 2), "order_revenue_30d": round(float(rev), 2), "recent_orders": ro}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/businesses")
async def api_businesses(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, Product, Order
        from sqlalchemy import select, func

        async with async_session() as s:
            rows = (await s.execute(select(Business).order_by(Business.created_at.desc()))).scalars().all()
            r = []
            for b in rows:
                pc = (await s.execute(select(func.count(Product.id)).where(Product.business_id == b.id))).scalar() or 0
                oc = (await s.execute(select(func.count(Order.id)).where(Order.business_id == b.id))).scalar() or 0
                r.append({"id": b.id, "name": b.name, "owner_name": b.name, "phone": b.phone, "subscription_status": b.subscription_status, "ai_active": b.ai_active, "product_count": pc, "order_count": oc, "created_at": b.created_at.isoformat() if b.created_at else ""})
            return {"businesses": r}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/businesses/{biz_id}")
async def api_business_detail(request: Request, biz_id: int):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, Product, Order
        from sqlalchemy import select, func

        async with async_session() as s:
            b = await s.get(Business, biz_id)
            if not b:
                raise HTTPException(status_code=404)
            pc = (await s.execute(select(func.count(Product.id)).where(Product.business_id == b.id))).scalar() or 0
            oc = (await s.execute(select(func.count(Order.id)).where(Order.business_id == b.id))).scalar() or 0
            return {"id": b.id, "name": b.name, "description": b.description, "address": b.address, "phone": b.phone, "owner_name": b.name, "subscription_status": b.subscription_status, "plan": b.subscription_plan, "subscription_end": b.subscription_end.isoformat() if b.subscription_end else None, "ai_active": b.ai_active, "orders_enabled": b.orders_enabled, "product_count": pc, "order_count": oc, "created_at": b.created_at.isoformat() if b.created_at else ""}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/businesses/{biz_id}/suspend")
async def api_suspend_business(request: Request, biz_id: int):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business
        from telegram import Bot
        from config import TELEGRAM_TOKEN, ADMIN_TELEGRAM_ID

        async with async_session() as s:
            b = await s.get(Business, biz_id)
            if not b:
                return {"error": "Not found"}
            b.subscription_status = "suspended"
            b.ai_active = False
            await s.commit()
        if ADMIN_TELEGRAM_ID:
            try:
                bot = Bot(TELEGRAM_TOKEN)
                await bot.send_message(int(ADMIN_TELEGRAM_ID), f"⏸️ Business *{b.name}* (ID: {biz_id}) suspended.", parse_mode="Markdown")
            except Exception:
                pass
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/businesses/{biz_id}/unsuspend")
async def api_unsuspend_business(request: Request, biz_id: int):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business

        async with async_session() as s:
            b = await s.get(Business, biz_id)
            if not b:
                return {"error": "Not found"}
            b.subscription_status = "active" if b.subscription_plan else "trial"
            await s.commit()
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/businesses/{biz_id}/delete")
async def api_delete_business(request: Request, biz_id: int):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business
        from telegram import Bot
        from config import TELEGRAM_TOKEN, ADMIN_TELEGRAM_ID

        async with async_session() as s:
            b = await s.get(Business, biz_id)
            if not b:
                return {"error": "Not found"}
            name = b.name
            await s.delete(b)
            await s.commit()
        if ADMIN_TELEGRAM_ID:
            try:
                bot = Bot(TELEGRAM_TOKEN)
                await bot.send_message(int(ADMIN_TELEGRAM_ID), f"🗑️ Business *{name}* (ID: {biz_id}) deleted.", parse_mode="Markdown")
            except Exception:
                pass
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/broadcast")
async def api_broadcast(request: Request):
    await _require_admin(request)
    try:
        body = await request.json()
        msg = body.get("message", "").strip()
        if not msg:
            raise HTTPException(status_code=400, detail="Message required")
        if len(msg) > 1000:
            raise HTTPException(status_code=400, detail="Message too long (max 1000 chars)")
        from db.database import async_session
        from db.models import Business
        from sqlalchemy import select
        from telegram import Bot
        from config import TELEGRAM_TOKEN
        import asyncio

        bot = Bot(TELEGRAM_TOKEN)
        async with async_session() as s:
            rows = (await s.execute(select(Business).where(Business.telegram_chat_id.isnot(None)))).scalars().all()
        sent, fail = 0, 0
        for b in rows:
            try:
                # No Markdown parsing — admin text is sent as-is to avoid injection/breakage.
                await bot.send_message(chat_id=b.telegram_chat_id, text=f"📢 Admin Announcement\n\n{msg}")
                sent += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        return {"success": True, "sent": sent, "failed": fail, "total": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/subscriptions")
async def api_subscriptions(request: Request, filter: str = "all"):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business

        async with async_session() as s:
            q = select(Business).order_by(Business.created_at.desc())
            if filter != "all":
                q = q.where(Business.subscription_status == filter)
            rows = (await s.execute(q)).scalars().all()
            subs = [{"business_id": b.id, "business_name": b.name, "status": b.subscription_status, "plan": b.subscription_plan, "end_date": b.subscription_end.isoformat() if b.subscription_end else None, "created_at": b.created_at.isoformat() if b.created_at else ""} for b in rows]
            return {"subscriptions": subs}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/subscriptions/confirm")
async def api_confirm_sub(request: Request):
    await _require_admin(request)
    try:
        body = await request.json()
        biz_id = body["business_id"]
        plan = body.get("plan", "monthly")
        from db.database import async_session
        from db.models import Business
        from telegram import Bot
        from config import TELEGRAM_TOKEN
        import datetime

        async with async_session() as s:
            b = await s.get(Business, biz_id)
            if not b:
                return {"error": "Not found"}
            now = datetime.datetime.now(datetime.timezone.utc)
            end = now + datetime.timedelta(days=365 if plan == "yearly" else 30)
            b.subscription_status = "active"
            b.subscription_plan = plan
            b.subscription_end = end
            await s.commit()
            try:
                bot = Bot(TELEGRAM_TOKEN)
                await bot.send_message(b.telegram_chat_id, f"🎉 *Subscription Activated!*\n\nYour *{plan.capitalize()}* plan is now active.\nExpires: {end.strftime('%Y-%m-%d')}\n\nThank you for choosing Ardi AI!", parse_mode="Markdown")
            except Exception:
                pass
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/subscriptions/revoke")
async def api_revoke_sub(request: Request):
    await _require_admin(request)
    try:
        body = await request.json()
        from db.database import async_session
        from db.models import Business
        async with async_session() as s:
            b = await s.get(Business, body["business_id"])
            if b:
                b.subscription_status = "expired"
                b.subscription_end = None
                b.subscription_plan = None
                await s.commit()
                return {"success": True}
        return {"success": False}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/subscriptions/revoke-all")
async def api_revoke_all(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business
        from sqlalchemy import select
        async with async_session() as s:
            rows = (await s.execute(select(Business).where(Business.subscription_status == "trial"))).scalars().all()
            for b in rows:
                b.subscription_status = "expired"
            await s.commit()
            return {"success": True, "revoked": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/orders")
async def api_orders(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, Order, OrderItem
        from sqlalchemy import select, func

        async with async_session() as s:
            tot = (await s.execute(select(func.count(Order.id)))).scalar() or 0
            pen = (await s.execute(select(func.count(Order.id)).where(Order.status == "pending"))).scalar() or 0
            com = (await s.execute(select(func.count(Order.id)).where(Order.status == "completed"))).scalar() or 0
            can = (await s.execute(select(func.count(Order.id)).where(Order.status == "cancelled"))).scalar() or 0
            rev = (await s.execute(select(func.coalesce(func.sum(Order.total_price), 0)).where(Order.status.in_(["confirmed", "completed"])))).scalar() or 0.0
            rows = (await s.execute(select(Order).order_by(Order.created_at.desc()).limit(50))).scalars().all()
            ords = []
            for o in rows:
                bz = await s.get(Business, o.business_id)
                ords.append({"id": o.id, "customer_name": o.customer_name, "business_name": bz.name if bz else "", "total_price": str(o.total_price), "status": o.status, "created_at": o.created_at.isoformat() if o.created_at else ""})
            return {"total_orders": tot, "pending_count": pen, "completed_count": com, "cancelled_count": can, "total_revenue": round(float(rev), 2), "orders": ords}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/orders/{order_id}")
async def api_order_detail(request: Request, order_id: int):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, Order, OrderItem

        async with async_session() as s:
            o = await s.get(Order, order_id)
            if not o:
                raise HTTPException(status_code=404)
            bz = await s.get(Business, o.business_id)
            items = (await s.execute(select(OrderItem).where(OrderItem.order_id == o.id))).scalars().all()
            return {"id": o.id, "customer_name": o.customer_name, "customer_phone": o.customer_phone, "customer_address": o.customer_address, "business_name": bz.name if bz else "", "total_price": str(o.total_price), "status": o.status, "created_at": o.created_at.isoformat() if o.created_at else "", "items": [{"product_name": i.product_name, "quantity": i.quantity, "unit_price": str(i.unit_price)} for i in items]}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/system")
async def api_system(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, User, Order
        from sqlalchemy import select, func

        async with async_session() as s:
            bz = (await s.execute(select(func.count(Business.id)))).scalar() or 0
            us = (await s.execute(select(func.count(User.id)))).scalar() or 0
            od = (await s.execute(select(func.count(Order.id)))).scalar() or 0
        sec = time.monotonic() - _START_MONO
        d, h, m = int(sec // 86400), int((sec % 86400) // 3600), int((sec % 3600) // 60)
        return {"bot_online": (time.monotonic() - bot_last_heartbeat) < HEARTBEAT_TIMEOUT, "uptime": f"{d}d {h}h {m}m", "businesses": bz, "users": us, "orders": od, "database": "PostgreSQL", "last_backup": "Use /backup in bot"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/payment-methods")
async def api_get_payment_methods(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import PaymentMethod
        from sqlalchemy import select

        async with async_session() as s:
            rows = (await s.execute(select(PaymentMethod).order_by(PaymentMethod.id))).scalars().all()
            return {"methods": [{"id": m.id, "name": m.name, "bank_name": m.bank_name or "", "account_name": m.account_name, "account_number": m.account_number, "is_active": m.is_active} for m in rows]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/payment-methods")
async def api_update_payment_method(request: Request):
    await _require_admin(request)
    try:
        body = await request.json()
        from db.database import async_session
        from db.models import PaymentMethod
        from sqlalchemy import select

        async with async_session() as s:
            for item in body.get("methods", []):
                mid = item.get("id")
                if mid:
                    m = await s.get(PaymentMethod, mid)
                    if m:
                        m.bank_name = item.get("bank_name", m.bank_name)
                        m.account_name = item.get("account_name", m.account_name)
                        m.account_number = item.get("account_number", m.account_number)
                        m.is_active = item.get("is_active", m.is_active)
            await s.commit()
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# BUSINESS OWNER API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/business/dashboard")
async def biz_dashboard(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session
        from db.models import Product, Order, OrderItem
        from sqlalchemy import select, func

        async with async_session() as s:
            pc = (await s.execute(select(func.count(Product.id)).where(Product.business_id == b.id))).scalar() or 0
            oc = (await s.execute(select(func.count(Order.id)).where(Order.business_id == b.id))).scalar() or 0
            rev = (await s.execute(select(func.coalesce(func.sum(Order.total_price), 0)).where(Order.business_id == b.id, Order.status.in_(["confirmed", "completed"])))).scalar() or 0.0
            recent = (await s.execute(select(Order).where(Order.business_id == b.id).order_by(Order.created_at.desc()).limit(5))).scalars().all()
            ro = [{"id": o.id, "customer_name": o.customer_name, "total_price": str(o.total_price), "status": o.status, "created_at": o.created_at.isoformat() if o.created_at else ""} for o in recent]
            return {"name": b.name, "product_count": pc, "order_count": oc, "revenue": round(float(rev), 2), "subscription_status": b.subscription_status, "recent_orders": ro}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/business/products")
async def biz_products(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session
        from db.models import Product
        from sqlalchemy import select

        async with async_session() as s:
            rows = (await s.execute(select(Product).where(Product.business_id == b.id).order_by(Product.created_at.desc()))).scalars().all()
            return {"products": [{"id": p.id, "name": p.name, "price": str(p.price), "available": p.available, "photo_url": p.photo_url, "created_at": p.created_at.isoformat() if p.created_at else ""} for p in rows]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/business/products")
async def biz_add_product(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name or len(name) > 200:
            raise HTTPException(status_code=400, detail="Product name required (max 200 chars)")
        try:
            price = float(body.get("price", 0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid price")
        if price < 0 or price > 9999999:
            raise HTTPException(status_code=400, detail="Price out of range")
        from db.database import async_session
        from db.models import Product
        from storage import upload_product_photo

        async with async_session() as s:
            p = Product(business_id=b.id, name=name, price=price)
            s.add(p)
            await s.commit()
            # Upload photo if provided (after commit so product has an id)
            photo_data = body.get("photo_data")
            if photo_data:
                import base64
                try:
                    if len(photo_data) > 7 * 1024 * 1024:
                        raise ValueError("Image too large")
                    photo_bytes = base64.b64decode(photo_data, validate=True)
                    if len(photo_bytes) > MAX_PHOTO_BYTES:
                        raise ValueError("Image too large")
                    url = await upload_product_photo(photo_bytes, b.id, name)
                    if url:
                        p.photo_url = url
                        await s.commit()
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid image data")
            return {"success": True, "id": p.id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/business/products/{prod_id}/toggle")
async def biz_toggle_product(request: Request, prod_id: int):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session
        from db.models import Product

        async with async_session() as s:
            p = await s.get(Product, prod_id)
            if not p or p.business_id != b.id:
                return {"error": "Not found"}
            p.available = not p.available
            await s.commit()
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.delete("/api/business/products/{prod_id}")
async def biz_delete_product(request: Request, prod_id: int):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session
        from db.models import Product

        async with async_session() as s:
            p = await s.get(Product, prod_id)
            if not p or p.business_id != b.id:
                return {"error": "Not found"}
            await s.delete(p)
            await s.commit()
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.patch("/api/business/products/{prod_id}")
async def biz_update_product(request: Request, prod_id: int):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        from db.database import async_session
        from db.models import Product
        from storage import upload_product_photo

        async with async_session() as s:
            p = await s.get(Product, prod_id)
            if not p or p.business_id != b.id:
                raise HTTPException(status_code=404)
            if "name" in body:
                name = (body["name"] or "").strip()
                if not name or len(name) > 200:
                    raise HTTPException(status_code=400, detail="Invalid product name")
                p.name = name
            if "price" in body:
                try:
                    price = float(body["price"])
                except (ValueError, TypeError):
                    raise HTTPException(status_code=400, detail="Invalid price")
                if price < 0 or price > 9999999:
                    raise HTTPException(status_code=400, detail="Price out of range")
                p.price = price
            photo_data = body.get("photo_data")
            if photo_data:
                import base64
                try:
                    if len(photo_data) > 7 * 1024 * 1024:
                        raise ValueError("Image too large")
                    photo_bytes = base64.b64decode(photo_data, validate=True)
                    if len(photo_bytes) > MAX_PHOTO_BYTES:
                        raise ValueError("Image too large")
                    url = await upload_product_photo(photo_bytes, b.id, p.name)
                    if url:
                        p.photo_url = url
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid image data")
            await s.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/business/orders")
async def biz_orders(request: Request, filter: str = "all"):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session
        from db.models import Order, OrderItem
        from sqlalchemy import select, func

        async with async_session() as s:
            q = select(Order).where(Order.business_id == b.id)
            if filter != "all":
                q = q.where(Order.status == filter)
            q = q.order_by(Order.created_at.desc()).limit(50)
            rows = (await s.execute(q)).scalars().all()
            tot = (await s.execute(select(func.count(Order.id)).where(Order.business_id == b.id))).scalar() or 0
            ords = []
            for o in rows:
                ic = (await s.execute(select(func.count(OrderItem.id)).where(OrderItem.order_id == o.id))).scalar() or 0
                ords.append({"id": o.id, "customer_name": o.customer_name, "total_price": str(o.total_price), "status": o.status, "item_count": ic, "created_at": o.created_at.isoformat() if o.created_at else ""})
            return {"orders": ords, "total": tot}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/business/orders/{order_id}/status")
async def biz_update_order_status(request: Request, order_id: int):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        new_status = body.get("status")
        if new_status not in ("confirmed", "completed", "cancelled"):
            raise HTTPException(status_code=400, detail="Invalid status")
        from db.database import async_session
        from db.models import Order

        # Allowed transitions: pending -> confirmed/cancelled, confirmed -> completed/cancelled.
        _ALLOWED = {"pending": ("confirmed", "cancelled"), "confirmed": ("completed", "cancelled")}
        async with async_session() as s:
            o = await s.get(Order, order_id)
            if not o or o.business_id != b.id:
                raise HTTPException(status_code=404)
            if new_status not in _ALLOWED.get(o.status, ()):
                raise HTTPException(status_code=400, detail=f"Cannot move order from {o.status} to {new_status}")
            o.status = new_status
            await s.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/business/orders/{order_id}")
async def biz_order_detail(request: Request, order_id: int):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session
        from db.models import Order, OrderItem

        async with async_session() as s:
            o = await s.get(Order, order_id)
            if not o or o.business_id != b.id:
                raise HTTPException(status_code=404)
            items = (await s.execute(select(OrderItem).where(OrderItem.order_id == o.id))).scalars().all()
            return {"id": o.id, "customer_name": o.customer_name, "customer_phone": o.customer_phone, "customer_address": o.customer_address, "total_price": str(o.total_price), "status": o.status, "created_at": o.created_at.isoformat() if o.created_at else "", "items": [{"product_name": i.product_name, "quantity": i.quantity, "unit_price": str(i.unit_price)} for i in items]}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# BUSINESS OWNER API ENDPOINTS (continued)
# ═══════════════════════════════════════════════════════════════

@app.get("/api/business/subscription")
async def biz_subscription(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    from bot.handlers import _get_subscription_status, _get_payment_methods
    sub = _get_subscription_status(b)
    methods = await _get_payment_methods()
    return {
        "status": b.subscription_status or "trial",
        "plan": b.subscription_plan or None,
        "active": sub["active"],
        "days_left": sub.get("days_left", 0),
        "label": sub.get("label", ""),
        "selected": b.subscription_plan,
        "payment_methods": [{"name": m.name, "bank_name": m.bank_name, "account_name": m.account_name, "account_number": m.account_number} for m in methods if m.is_active],
    }


@app.post("/api/business/subscription/select-plan")
async def biz_select_plan(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        plan = body.get("plan", "monthly")
        if plan not in ("monthly", "yearly"):
            return {"error": "Invalid plan"}
        from db.database import async_session
        async with async_session() as s:
            bb = await s.get(type(b), b.id)
            bb.subscription_plan = plan
            bb.subscription_status = "awaiting_payment"
            await s.commit()
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/business/subscription/upload-receipt")
async def biz_upload_receipt(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        photo_data = body.get("photo_data")
        if not photo_data:
            raise HTTPException(status_code=400, detail="No image data")
        if len(photo_data) > 7 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large")
        import base64, logging
        logger = logging.getLogger(__name__)
        try:
            photo_bytes = base64.b64decode(photo_data, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid image data")
        if len(photo_bytes) > MAX_PHOTO_BYTES:
            raise HTTPException(status_code=400, detail="Image too large")
        from storage import upload_product_photo
        url = await upload_product_photo(photo_bytes, b.id, f"sub_receipt_{b.id}")
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        from config import ADMIN_TELEGRAM_ID, SUBSCRIPTION_MONTHLY, SUBSCRIPTION_YEARLY, TELEGRAM_TOKEN
        plan = b.subscription_plan or "monthly"
        amount = SUBSCRIPTION_MONTHLY if plan == "monthly" else SUBSCRIPTION_YEARLY
        bot = Bot(TELEGRAM_TOKEN)
        msg = (
            f"💳 *Payment Receipt Uploaded*\n\n"
            f"Business: *{b.name}* (ID: {b.id})\n"
            f"Plan: *{plan.capitalize()}*\n"
            f"Amount: *{amount:,} ETB*\n"
        )
        if url:
            msg += f"[View Receipt]({url})\n"
        msg += "\nVerify and confirm:"
        try:
            await bot.send_message(
                ADMIN_TELEGRAM_ID,
                msg,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Confirm Payment", callback_data=f"sub_confirm_{b.id}_{plan}")],
                ]),
            )
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning("Failed to notify admin: %s", e)
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/business/settings")
async def biz_settings(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    return {
        "ai_active": b.ai_active,
        "ai_tone": b.ai_tone,
        "business_hours_enabled": b.business_hours_enabled,
        "business_hours_start": b.business_hours_start,
        "business_hours_end": b.business_hours_end,
        "ai_offline_message": b.ai_offline_message,
        "order_bank_name": b.order_bank_name,
        "order_bank_account": b.order_bank_account,
        "order_account_holder": b.order_account_holder,
        "subscription_status": b.subscription_status,
        "subscription_plan": b.subscription_plan,
        "subscription_end": b.subscription_end.isoformat() if b.subscription_end else None,
    }


@app.post("/api/business/ai/toggle")
async def biz_toggle_ai(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        from db.database import async_session

        async with async_session() as s:
            bb = await s.get(type(b), b.id)
            bb.ai_active = not bb.ai_active
            await s.commit()
            return {"success": True, "active": bb.ai_active}
    except Exception as e:
        return {"error": str(e)}


_bot_username_cache: dict = {}


async def _get_bot_username() -> str:
    """Cached bot username for share links (1h TTL). Empty string if unavailable."""
    if _bot_username_cache.get("exp", 0) > time.monotonic():
        return _bot_username_cache.get("u", "")
    try:
        from telegram import Bot
        from config import TELEGRAM_TOKEN
        if TELEGRAM_TOKEN:
            bot = Bot(TELEGRAM_TOKEN)
            me = await bot.get_me()
            _bot_username_cache["u"] = me.username or ""
            _bot_username_cache["exp"] = time.monotonic() + 3600
    except Exception:
        pass
    return _bot_username_cache.get("u", "")


@app.get("/api/business/profile")
async def biz_profile(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    return {
        "id": b.id,
        "name": b.name or "",
        "description": b.description or "",
        "address": b.address or "",
        "phone": b.phone or "",
        "channel_id": b.channel_id,
        "ai_tone": b.ai_tone or "friendly",
    }


@app.patch("/api/business/profile")
async def biz_update_profile(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        from db.database import async_session

        async with async_session() as s:
            bb = await s.get(type(b), b.id)
            if "name" in body:
                name = (body["name"] or "").strip()
                if len(name) < 2 or len(name) > 120:
                    raise HTTPException(status_code=400, detail="Name must be 2–120 characters")
                bb.name = name
            if "description" in body:
                desc = (body["description"] or "").strip()
                if len(desc) > 1000:
                    raise HTTPException(status_code=400, detail="Description too long (max 1000)")
                bb.description = desc or None
            if "address" in body:
                addr = (body["address"] or "").strip()
                if len(addr) > 300:
                    raise HTTPException(status_code=400, detail="Address too long (max 300)")
                bb.address = addr or None
            if "phone" in body:
                phone = (body["phone"] or "").strip()
                if len(phone) > 30:
                    raise HTTPException(status_code=400, detail="Phone too long (max 30)")
                bb.phone = phone or None
            await s.commit()
            return {"success": True, "name": bb.name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/business/share")
async def biz_share(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    username = await _get_bot_username()
    path = f"?start=bus_{b.id}"
    link = f"https://t.me/{username}{path}" if username else path
    return {
        "business_id": b.id,
        "business_name": b.name,
        "bot_username": username,
        "deep_link": link,
        "share_text": f"🛍️ {b.name} — browse & order here: {link}",
    }


@app.patch("/api/business/settings")
async def biz_update_settings(request: Request):
    biz_data = await _require_business(request)
    b = biz_data["business"]
    try:
        body = await request.json()
        from db.database import async_session

        async with async_session() as s:
            bb = await s.get(type(b), b.id)
            for field in ("ai_tone", "business_hours_enabled", "business_hours_start", "business_hours_end", "ai_offline_message", "order_bank_name", "order_bank_account", "order_account_holder"):
                if field in body:
                    setattr(bb, field, body[field])
            await s.commit()
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# LEGACY (backward compat)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/backup")
async def api_backup(request: Request):
    await _require_admin(request)
    try:
        from db.backup import backup_database
        path = await backup_database()
        if path:
            return {"success": True, "message": f"Backup saved to {path}"}
        raise HTTPException(status_code=500, detail="Backup failed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def stats(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, User, Order
        from sqlalchemy import select, func
        from datetime import datetime, timedelta, timezone

        async with async_session() as s:
            bz = (await s.execute(select(func.count(Business.id)))).scalar() or 0
            act = (await s.execute(select(func.count(Business.id)).where(Business.subscription_status == "active"))).scalar() or 0
            us = (await s.execute(select(func.count(User.id)))).scalar() or 0
            cut = datetime.now(timezone.utc) - timedelta(days=30)
            o30 = (await s.execute(select(func.count(Order.id)).where(Order.created_at >= cut))).scalar() or 0
            return {"businesses": bz, "active_subscriptions": act, "users": us, "orders_30d": o30}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("MINI_APP_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
