"""Mini app web server for Ardi AI."""
import os, time, hmac, hashlib, json, logging
from urllib.parse import parse_qs
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response

logger = logging.getLogger("miniapp")

app = FastAPI(title="Ardi AI")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
bot_last_heartbeat: float = time.monotonic()
HEARTBEAT_TIMEOUT = 120


async def _require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    if ADMIN_API_KEY and auth != f"Bearer {ADMIN_API_KEY}":
        raise HTTPException(status_code=403, detail="Forbidden")


def _validate_init_data(init_data: str) -> dict | None:
    try:
        parsed = parse_qs(init_data)
        items = sorted((k, v[0]) for k, v in parsed.items() if k != "hash")
        data_check = "\n".join(f"{k}={v}" for k, v in items)
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        expected = parsed.get("hash", [None])[0]
        if computed != expected:
            logger.warning("init_data=%.300s", init_data)
            logger.warning("data_check=%s", data_check)
            logger.warning("items=%s", items)
            logger.warning("computed=%s expected=%s", computed, expected)
            return None
        user_raw = parsed.get("user", [None])[0]
        return json.loads(user_raw) if user_raw else None
    except Exception as exc:
        logger.warning("_validate_init_data error: %s", exc, exc_info=True)
        return None


async def _require_business(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user = _validate_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    tid = user.get("id")
    if not tid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    from db.database import async_session
    from db.models import Business
    from sqlalchemy import select
    async with async_session() as s:
        result = await s.execute(select(Business).where(Business.telegram_chat_id == tid))
        b = result.scalar_one_or_none()
        if not b:
            raise HTTPException(status_code=403, detail="No business registered")
        return {"business": b, "telegram_id": tid, "user": user}


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

<div class="pg" id="pg-set"><div class="hd"><div class="ht"><h1>⚙️ Settings</h1></div></div><div class="sh">System</div><div class="cd" id="shl"></div><div class="sh">📢 Broadcast</div><div class="cd"><div style="font-size:13px;color:var(--h);margin-bottom:10px">Message all business owners</div><textarea id="bm" class="txt" style="min-height:80px;margin-bottom:10px" placeholder="Type message..."></textarea><button class="btn bp" style="margin:0" onclick="sb()">📨 Send to All</button><div id="bms" style="font-size:12px;color:var(--h);margin-top:8px;text-align:center"></div></div><div class="sh">Actions</div><button class="btn bp" onclick="bdb()">💾 Backup Database</button><button class="btn bdg" onclick="cr()">🔒 Revoke All Trials</button></div>

<div class="pg" id="pg-dtl"><button class="bk" onclick="sp('dash')">← Back</button><div id="dc"></div></div>

<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
const K="{{ADMIN_API_KEY}}";Telegram.WebApp.ready();Telegram.WebApp.expand();
function hd(){const h={"Content-Type":"application/json"};if(K)h["Authorization"]="Bearer "+K;return h}
function $(i){return document.getElementById(i)}
function tt(m,t){const e=$('ts');e.textContent=m;e.className='ts'+(t?' '+t:'');requestAnimationFrame(()=>{e.classList.add('s');clearTimeout(e._h);e._h=setTimeout(()=>e.classList.remove('s'),3000)})}
function ld(o){$('ld').classList.toggle('a',o)}
function es(t){const d=document.createElement('div');d.appendChild(document.createTextNode(t));return d.innerHTML}
document.querySelectorAll('.nb').forEach(b=>{b.onclick=()=>sp(b.dataset.pg)});
function sp(p){document.querySelectorAll('.pg').forEach(x=>x.classList.remove('a'));const e=$('pg-'+p);if(e)e.classList.add('a');document.querySelectorAll('.nb').forEach(b=>b.classList.toggle('a',b.dataset.pg===p));if(p==='dash')lda();else if(p==='biz')lb();else if(p==='sub')ls();else if(p==='ord')lo();else if(p==='set')lse()}
async function ap(p,o){ld(true);try{const r=await fetch(p,{headers:hd(),...o});if(r.status===403){tt('Unauthorized','er');return null}if(!r.ok)throw new Error('HTTP '+r.status);return await r.json()}catch(e){tt('Error: '+e.message,'er');return null}finally{ld(false)}}

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
$('shl').innerHTML=`<div class="dl"><div class="rw"><span class="lb">Bot</span><span class="vl"><span class="st ${d.bot_online?'skk':'sx'}">${d.bot_online?'Online':'Offline'}</span></span></div><div class="rw"><span class="lb">Uptime</span><span class="vl">${es(d.uptime||'—')}</span></div><div class="rw"><span class="lb">DB</span><span class="vl">${es(d.database||'—')}</span></div><div class="rw"><span class="lb">Businesses</span><span class="vl">${d.businesses||0}</span></div><div class="rw"><span class="lb">Orders</span><span class="vl">${d.orders||0}</span></div><div class="rw"><span class="lb">Users</span><span class="vl">${d.users||0}</span></div></div>`}

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
lda();
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
<title>My Business - Ardi AI</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--b:var(--tg-theme-bg-color,#0c0c1a);--c:var(--tg-theme-secondary-bg-color,#16162a);--t:var(--tg-theme-text-color,#eee);--h:var(--tg-theme-hint-color,#6e6e82);--a:var(--tg-theme-button-color,#6c5ce7);--at:var(--tg-theme-button-text-color,#fff);--r:14px;--br:linear-gradient(135deg,var(--a),#a29bfe)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--b);color:var(--t);min-height:100vh;overflow-x:hidden;-webkit-font-smoothing:antialiased;padding-bottom:80px}
input,textarea,select,button{font-family:inherit}
.pg{display:none;padding:16px;max-width:480px;margin:0 auto}.pg.a{display:block}
.hd{display:flex;align-items:center;justify-content:space-between;padding:12px 4px 16px}
.hl{display:flex;align-items:center;gap:12px}
.lo{width:40px;height:40px;background:var(--br);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 16px rgba(108,92,231,.3)}
.ht h1{font-size:18px;font-weight:700;line-height:1.2}.ht p{font-size:12px;color:var(--h);font-weight:500}
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
.sa{background:rgba(46,213,115,.12);color:#2ed573}.stb{background:rgba(255,165,2,.12);color:#ffa502}.se{background:rgba(255,71,87,.12);color:#ff4757}.sp{background:rgba(108,92,231,.12);color:var(--a)}.skk{background:rgba(46,213,115,.12);color:#2ed573}.sx{background:rgba(255,71,87,.12);color:#ff4757}.sy{background:rgba(255,165,2,.12);color:#ffa502}
.btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:14px;border-radius:12px;font-size:14px;font-weight:600;font-family:inherit;cursor:pointer;border:none;transition:.2s;-webkit-tap-highlight-color:transparent;margin-bottom:8px}
.btn:active{transform:scale(.97)}.bp{background:var(--a);color:var(--at)}.bs{background:rgba(255,255,255,.06);color:var(--t);border:1px solid rgba(255,255,255,.08)}.br{background:rgba(255,71,87,.12);color:#ff4757}.bg{background:rgba(46,213,115,.12);color:#2ed573}
.sr{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--t);font-family:inherit;font-size:14px;outline:none;margin-bottom:12px;transition:.2s}
.sr:focus{border-color:var(--a);background:rgba(108,92,231,.06)}
.ts{position:fixed;bottom:90px;left:50%;transform:translateX(-50%) translateY(120px);background:var(--c);color:var(--t);padding:12px 18px;border-radius:12px;font-size:13px;font-weight:500;box-shadow:0 8px 40px rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.06);z-index:1000;transition:transform .35s cubic-bezier(.34,1.56,.64,1),opacity .25s;opacity:0;max-width:calc(100vw - 48px);text-align:center;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);pointer-events:none}
.ts.s{transform:translateX(-50%) translateY(0);opacity:1}.ts.er{border-color:rgba(255,71,87,.3)}.ts.ok{border-color:rgba(46,213,115,.3)}
.ld{position:fixed;top:0;left:0;width:100%;height:3px;z-index:999;display:none;background:rgba(255,255,255,.04)}
.ld.a{display:block}.ld::after{content:'';position:absolute;top:0;left:0;height:100%;width:40%;background:var(--br);animation:ld 1s ease-in-out infinite;border-radius:2px}
@keyframes ld{0%{left:-40%}100%{left:100%}}
.dl{display:flex;flex-direction:column;gap:6px;margin-bottom:16px}.dl .rw{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04)}.dl .rw:last-child{border-bottom:none}
.dl .lb{font-size:13px;color:var(--h)}.dl .vl{font-size:13px;font-weight:600;text-align:right}
.em{padding:40px 20px;text-align:center;color:var(--h);font-size:14px}
.mod{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:200;display:none;align-items:flex-end;justify-content:center;backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)}
.mod.a{display:flex}.mw{background:var(--c);width:100%;max-width:480px;border-radius:20px 20px 0 0;padding:24px 20px;padding-bottom:calc(24px + env(safe-area-inset-bottom));max-height:85vh;overflow-y:auto;animation:ms .3s cubic-bezier(.34,1.56,.64,1)}
@keyframes ms{from{transform:translateY(100%)}to{transform:translateY(0)}}
.mh{font-size:18px;font-weight:700;margin-bottom:16px}
.tg{display:flex;align-items:center;gap:10px;padding:12px 16px;background:rgba(255,255,255,.04);border-radius:12px;cursor:pointer;-webkit-tap-highlight-color:transparent}
.tk{width:44px;height:24px;border-radius:12px;background:rgba(255,255,255,.12);position:relative;transition:.3s;flex-shrink:0}.tk.on{background:var(--a)}.tk::after{content:'';width:20px;height:20px;border-radius:50%;background:#fff;position:absolute;top:2px;left:2px;transition:.3s}.tk.on::after{left:22px}
.sel{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--t);font-size:14px;outline:none;appearance:none;-webkit-appearance:none;cursor:pointer}
.txt{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--t);font-size:14px;outline:none;transition:.2s;resize:none}
.txt:focus{border-color:var(--a)}.txt::placeholder{color:var(--h)}
.gr{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px}
</style>
</head>
<body>
<div class="ld" id="ld"></div>
<div class="ts" id="ts"></div>
<nav class="nv" id="nv">
  <button class="nb a" data-pg="dash"><span class="ni">📊</span>Dashboard</button>
  <button class="nb" data-pg="prod"><span class="ni">📦</span>Products</button>
  <button class="nb" data-pg="ord"><span class="ni">🛒</span>Orders</button>
  <button class="nb" data-pg="set"><span class="ni">⚙️</span>Settings</button>
</nav>

<div class="pg a" id="pg-dash">
  <div class="hd"><div class="hl"><div class="lo">A</div><div class="ht"><h1>My Business</h1><p id="bizName">Loading...</p></div></div></div>
  <div class="sg" id="bd"></div>
  <div class="sh">🕐 Recent Orders</div>
  <div class="cd" id="bro"><div class="em">Loading...</div></div>
</div>

<div class="pg" id="pg-prod">
  <div class="hd"><div class="ht"><h1>📦 Products</h1><p id="pc">—</p></div><button class="btn bp" style="width:auto;padding:10px 16px;font-size:13px;margin:0" onclick="apm()">+ Add</button></div>
  <input class="sr" id="ps" placeholder="Search products..." oninput="fp()">
  <div id="pl"><div class="em">Loading...</div></div>
</div>

<div class="pg" id="pg-ord">
  <div class="hd"><div class="ht"><h1>🛒 Orders</h1><p id="boc">—</p></div>
  <select id="of" onchange="blo()" style="background:var(--c);color:var(--t);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:6px 10px;font-size:12px;outline:none"><option value="all">All</option><option value="pending">Pending</option><option value="confirmed">Confirmed</option><option value="completed">Completed</option></select></div>
  <div id="bol"><div class="em">Loading...</div></div>
</div>

<div class="pg" id="pg-set">
  <div class="hd"><div class="ht"><h1>⚙️ Settings</h1></div></div>
  <div class="sh">AI Assistant</div>
  <div class="cd"><div class="tg" onclick="ta()"><div class="tk" id="aiTg"></div><div><div style="font-weight:600;font-size:14px">AI Auto-Reply</div><div style="font-size:12px;color:var(--h)" id="aiSt">Loading...</div></div></div></div>
  <div class="sh">Conversation Tone</div>
  <div class="cd"><select class="sel" id="toneSel" onchange="stt()"><option value="friendly">Friendly</option><option value="professional">Professional</option><option value="casual">Casual</option><option value="formal">Formal</option><option value="witty">Witty</option></select></div>
  <div class="sh">Business Hours</div>
  <div class="cd">
    <div class="tg" style="margin-bottom:12px" onclick="tbh()"><div class="tk" id="bhTg"></div><div style="font-size:14px;font-weight:500">Enable Business Hours</div></div>
    <div id="bhFields" style="display:none">
      <div class="gr">
        <div><div style="font-size:12px;color:var(--h);margin-bottom:4px">Start</div><input class="txt" id="bhS" placeholder="09:00" style="padding:10px 12px;font-size:14px"></div>
        <div><div style="font-size:12px;color:var(--h);margin-bottom:4px">End</div><input class="txt" id="bhE" placeholder="18:00" style="padding:10px 12px;font-size:14px"></div>
      </div>
      <button class="btn bp" style="padding:10px;font-size:13px;margin:0" onclick="sbh()">Save Hours</button>
    </div>
  </div>
  <div class="sh">Offline Message</div>
  <div class="cd"><textarea class="txt" id="offMsg" placeholder="Message customers see outside business hours..." style="min-height:60px;margin-bottom:8px"></textarea><button class="btn bp" style="padding:10px;font-size:13px;margin:0" onclick="som()">Save</button></div>
  <div class="sh">Payment Info</div>
  <div class="cd">
    <div style="font-size:12px;color:var(--h);margin-bottom:10px">Bank details shown to customers when ordering</div>
    <div class="txt" style="margin-bottom:4px;padding:10px 12px;cursor:default;opacity:.7">Bank Name</div>
    <input class="txt" id="bn" placeholder="e.g. CBE" style="padding:10px 12px;margin-bottom:8px">
    <div class="txt" style="margin-bottom:4px;padding:10px 12px;cursor:default;opacity:.7">Account Number</div>
    <input class="txt" id="ba" placeholder="e.g. 1000134567890" style="padding:10px 12px;margin-bottom:8px">
    <div class="txt" style="margin-bottom:4px;padding:10px 12px;cursor:default;opacity:.7">Account Holder</div>
    <input class="txt" id="bah" placeholder="Full name on account" style="padding:10px 12px;margin-bottom:8px">
    <button class="btn bp" style="padding:10px;font-size:13px;margin:0" onclick="spi()">Save Payment Info</button>
  </div>
  <div class="sh">Subscription</div>
  <div class="cd" id="subInfo"></div>
</div>

<div class="pg" id="pg-ordd"><button class="bk" onclick="sp('ord')">← Orders</button><div id="odc"></div></div>

<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
const ID=Telegram.WebApp.initData;Telegram.WebApp.ready();Telegram.WebApp.expand();
function hd(){return{"Content-Type":"application/json","X-Telegram-Init-Data":ID}}
function $(i){return document.getElementById(i)}
function tt(m,t){const e=$('ts');e.textContent=m;e.className='ts'+(t?' '+t:'');requestAnimationFrame(()=>{e.classList.add('s');clearTimeout(e._h);e._h=setTimeout(()=>e.classList.remove('s'),3000)})}
function ld(o){$('ld').classList.toggle('a',o)}
function es(t){const d=document.createElement('div');d.appendChild(document.createTextNode(t));return d.innerHTML}
document.querySelectorAll('.nb').forEach(b=>{b.onclick=()=>sp(b.dataset.pg)});
function sp(p){document.querySelectorAll('.pg').forEach(x=>x.classList.remove('a'));const e=$('pg-'+p);if(e)e.classList.add('a');document.querySelectorAll('.nb').forEach(b=>b.classList.toggle('a',b.dataset.pg===p));if(p==='dash')ldd();else if(p==='prod')lp();else if(p==='ord')blo();else if(p==='set')lset()}
async function ap(p,o){ld(true);try{const r=await fetch(p,{headers:hd(),...o});if(r.status===401||r.status===403){tt('Session expired. Reopen from bot.','er');return null}if(!r.ok)throw new Error('HTTP '+r.status);return await r.json()}catch(e){tt('Error: '+e.message,'er');return null}finally{ld(false)}}

// ─── DASHBOARD ─────────────────────────────────────────────
async function ldd(){
  const d=await ap('/api/business/dashboard');if(!d)return;
  $('bizName').textContent=d.name;
  $('bd').innerHTML=[
    {ic:'📦',c:'pu',l:'Products',v:d.product_count},{ic:'🛒',c:'bl',l:'Orders',v:d.order_count},
    {ic:'💰',c:'gr',l:'Revenue',v:'ETB '+d.revenue.toLocaleString()},{ic:'⭐',c:'or',l:'Sub',v:d.subscription_status},
  ].map(s=>`<div class="sc"><div class="ic ${s.c}">${s.ic}</div><div class="sl">${s.l}</div><div class="sv">${es(String(s.v))}</div></div>`).join('');
  const ro=d.recent_orders||[];if(!ro.length){$('bro').innerHTML='<div class="em">No orders yet</div>';return}
  $('bro').innerHTML=ro.map(o=>`<div class="li" onclick="bvo(${o.id})"><div class="la">#${o.id}</div><div class="lb"><div class="lt">${es(o.customer_name||'Customer')}</div><div class="ls">${es(o.created_at?new Date(o.created_at).toLocaleDateString():'')}</div></div><div class="lr"><div style="font-weight:700">ETB ${(+o.total_price).toLocaleString()}</div><span class="st ${o.status==='pending'?'sp':o.status==='confirmed'?'sa':o.status==='completed'?'skk':'sx'}">${es(o.status)}</span></div></div>`).join('')}

// ─── PRODUCTS ──────────────────────────────────────────────
let allP=[];async function lp(){const d=await ap('/api/business/products');if(!d)return;allP=d.products||[];$('pc').textContent=allP.length;fp()}
function fp(){const q=$('ps').value.toLowerCase();const items=q?allP.filter(p=>(p.name||'').toLowerCase().includes(q)):allP;if(!items.length){$('pl').innerHTML='<div class="em">No products</div>';return}
$('pl').innerHTML=items.map(p=>`<div class="cd" style="padding:14px"><div style="display:flex;justify-content:space-between;align-items:center"><div style="flex:1;min-width:0"><div style="font-weight:600;font-size:14px">${es(p.name)}</div><div style="font-size:13px;color:var(--h)">ETB ${(+p.price).toLocaleString()}</div></div><div style="display:flex;align-items:center;gap:8px"><span class="st ${p.available?'sa':'sx'}" style="cursor:pointer" onclick="tgl(${p.id})">${p.available?'In Stock':'Out'}</span><span style="font-size:18px;color:var(--h);cursor:pointer;padding:4px" onclick="ep(${p.id})">✏️</span><span style="font-size:18px;color:#ff4757;cursor:pointer;padding:4px" onclick="dp(${p.id})">🗑️</span></div></div></div>`).join('')}

async function tgl(i){const d=await ap('/api/business/products/'+i+'/toggle',{method:'POST'});if(d&&d.success){tt('✅ Updated','ok');lp()}}
async function dp(i){Telegram.WebApp.showConfirm('Delete this product?',async ok=>{if(!ok)return;const d=await ap('/api/business/products/'+i,{method:'DELETE'});if(d&&d.success){tt('🗑️ Deleted','ok');lp()}})}
async function ep(i){const p=allP.find(x=>x.id===i);if(!p)return;const n=prompt('Product name:',p.name);if(!n||!n.trim())return;const r=prompt('Price (ETB):',p.price);if(!r||!r.trim())return;const d=await ap('/api/business/products/'+i,{method:'PATCH',body:JSON.stringify({name:n.trim(),price:parseFloat(r.replace(/[^0-9.]/g,''))})});if(d&&d.success){tt('✅ Updated','ok');lp()}}

// Add product modal
const am=document.createElement('div');am.className='mod';am.id='am';am.innerHTML=`<div class="mw"><div class="mh">+ Add Product</div><input class="txt" id="apn" placeholder="Product name" style="margin-bottom:8px"><input class="txt" id="app" placeholder="Price in ETB" style="margin-bottom:16px" type="number"><div style="display:flex;gap:8px"><button class="btn bs" style="flex:1;margin:0" onclick="cam()">Cancel</button><button class="btn bp" style="flex:1;margin:0" onclick="sap()">Save</button></div></div>`
document.body.appendChild(am);
function apm(){am.classList.add('a');$('apn').value='';$('app').value='';setTimeout(()=>$('apn').focus(),300)}
function cam(){am.classList.remove('a')}
async function sap(){const n=$('apn').value.trim();const p=parseFloat($('app').value);if(!n||!p||p<=0){tt('Enter name & valid price','er');return}
const d=await ap('/api/business/products',{method:'POST',body:JSON.stringify({name:n,price:p})});if(d&&d.success){tt('✅ Added','ok');cam();lp()}}

// ─── ORDERS ────────────────────────────────────────────────
async function blo(){const f=$('of').value;const d=await ap('/api/business/orders?filter='+f);if(!d)return
$('boc').textContent=d.total+' total'
$('bol').innerHTML=(d.orders||[]).length?d.orders.map(o=>`<div class="cd" style="padding:14px"><div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:6px"><div><div style="font-weight:600">#${o.id} · ${es(o.customer_name||'Customer')}</div><div style="font-size:12px;color:var(--h)">${o.item_count||0} items · ${o.created_at?new Date(o.created_at).toLocaleDateString():''}</div></div><div style="text-align:right"><div style="font-weight:700;font-size:15px">ETB ${(+o.total_price).toLocaleString()}</div><span class="st ${o.status==='pending'?'sp':o.status==='confirmed'?'sa':o.status==='completed'?'skk':'sx'}">${es(o.status)}</span></div></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${o.status==='pending'?`<button class="btn bg" style="flex:1;padding:8px;font-size:12px;margin:0" onclick="bos(${o.id},'confirmed')">✅ Confirm</button>`:''}${o.status==='confirmed'?`<button class="btn bg" style="flex:1;padding:8px;font-size:12px;margin:0" onclick="bos(${o.id},'completed')">✅ Complete</button>`:''}${o.status==='pending'||o.status==='confirmed'?`<button class="btn br" style="flex:1;padding:8px;font-size:12px;margin:0" onclick="bos(${o.id},'cancelled')">❌ Cancel</button>`:''}</div></div>`).join(''):'<div class="em">No orders</div>'}
async function bos(i,s){Telegram.WebApp.showConfirm(s==='cancelled'?'Cancel order?':s==='completed'?'Mark completed?':'Confirm order?',async ok=>{if(!ok)return;const d=await ap('/api/business/orders/'+i+'/status',{method:'POST',body:JSON.stringify({status:s})});if(d&&d.success){tt('✅ '+s,'ok');blo()}})}
async function bvo(i){const d=await ap('/api/business/orders/'+i);if(!d)return
$('odc').innerHTML=`<div class="cd"><div style="font-size:18px;font-weight:700;margin-bottom:12px">Order #${d.id}</div><div class="dl"><div class="rw"><span class="lb">Customer</span><span class="vl">${es(d.customer_name||'—')}</span></div><div class="rw"><span class="lb">Phone</span><span class="vl">${es(d.customer_phone||'—')}</span></div><div class="rw"><span class="lb">Address</span><span class="vl">${es(d.customer_address||'—')}</span></div><div class="rw"><span class="lb">Total</span><span class="vl" style="font-weight:700">ETB ${(+d.total_price).toLocaleString()}</span></div><div class="rw"><span class="lb">Status</span><span class="vl"><span class="st ${d.status==='pending'?'sp':d.status==='confirmed'?'sa':d.status==='completed'?'skk':'sx'}">${es(d.status)}</span></span></div><div class="rw"><span class="lb">Date</span><span class="vl">${d.created_at?new Date(d.created_at).toLocaleString():'—'}</span></div></div>${d.items&&d.items.length?`<div style="font-size:13px;font-weight:600;color:var(--h);margin:8px 0 4px">Items</div>${d.items.map(i=>`<div style="display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid rgba(255,255,255,.04)"><span>${es(i.product_name||'Item')} ×${i.quantity||1}</span><span style="font-weight:600">ETB ${(+i.unit_price).toLocaleString()}</span></div>`).join('')}`:''}</div>`;sp('ordd')}

// ─── SETTINGS ──────────────────────────────────────────────
async function lset(){
  const d=await ap('/api/business/settings');if(!d)return;
  // AI toggle
  const ai=$('aiTg');ai.classList.toggle('on',d.ai_active);$('aiSt').textContent=d.ai_active?'AI replies are ON':'AI replies are OFF'
  // Tone
  $('toneSel').value=d.ai_tone||'friendly'
  // Business hours
  const bh=$('bhTg');bh.classList.toggle('on',d.business_hours_enabled);$('bhFields').style.display=d.business_hours_enabled?'block':'none'
  $('bhS').value=d.business_hours_start||'';$('bhE').value=d.business_hours_end||''
  // Offline msg
  $('offMsg').value=d.ai_offline_message||''
  // Bank info
  $('bn').value=d.order_bank_name||'';$('ba').value=d.order_bank_account||'';$('bah').value=d.order_account_holder||''
  // Subscription
  const sub=d.subscription_status||'trial';const plan=d.subscription_plan||'—';const end=d.subscription_end?new Date(d.subscription_end).toLocaleDateString():'—'
  $('subInfo').innerHTML=`<div class="dl"><div class="rw"><span class="lb">Status</span><span class="vl"><span class="st ${sub==='active'?'sa':sub==='trial'?'stb':'sx'}">${es(sub)}</span></span></div><div class="rw"><span class="lb">Plan</span><span class="vl">${es(plan)}</span></div><div class="rw"><span class="lb">Expires</span><span class="vl">${es(end)}</span></div></div>`
}
async function ta(){const d=await ap('/api/business/ai/toggle',{method:'POST'});if(d&&d.success){lset();tt(d.active?'✅ AI ON':'⏸️ AI OFF','ok')}}
async function stt(){const d=await ap('/api/business/settings',{method:'PATCH',body:JSON.stringify({ai_tone:$('toneSel').value})});if(d&&d.success)tt('✅ Tone saved','ok')}
function tbh(){const el=$('bhTg');el.classList.toggle('on');$('bhFields').style.display=el.classList.contains('on')?'block':'none'}
async function sbh(){const s=$('bhS').value.trim(),e=$('bhE').value.trim();if(!s||!e){tt('Enter start & end time','er');return}
const d=await ap('/api/business/settings',{method:'PATCH',body:JSON.stringify({business_hours_enabled:true,business_hours_start:s,business_hours_end:e})});if(d&&d.success)tt('✅ Hours saved','ok')}
async function som(){const d=await ap('/api/business/settings',{method:'PATCH',body:JSON.stringify({ai_offline_message:$('offMsg').value})});if(d&&d.success)tt('✅ Saved','ok')}
async function spi(){const d=await ap('/api/business/settings',{method:'PATCH',body:JSON.stringify({order_bank_name:$('bn').value,order_bank_account:$('ba').value,order_account_holder:$('bah').value})});if(d&&d.success)tt('✅ Payment info saved','ok')}

ldd();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return ADMIN_HTML.replace("{{ADMIN_API_KEY}}", ADMIN_API_KEY)


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
        return {"bot_online": (time.monotonic() - bot_last_heartbeat) < HEARTBEAT_TIMEOUT, "businesses": biz, "active_subscriptions": act, "trial_count": tr, "users": usr, "orders_30d": o30, "pending_orders": pen, "sub_revenue": round(act * 500, 2), "avg_order_value": round(float(avg), 2), "order_revenue_30d": round(float(rev), 2), "recent_orders": ro}
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
            return {"error": "Message required"}
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
                await bot.send_message(chat_id=b.telegram_chat_id, text=f"📢 *Admin Announcement*\n\n{msg}", parse_mode="Markdown")
                sent += 1
            except Exception:
                fail += 1
            await asyncio.sleep(0.05)
        return {"success": True, "sent": sent, "failed": fail, "total": len(rows)}
    except Exception as e:
        return {"error": str(e)}


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
        sec = time.monotonic()
        d, h, m = int(sec // 86400), int((sec % 86400) // 3600), int((sec % 3600) // 60)
        return {"bot_online": (time.monotonic() - bot_last_heartbeat) < HEARTBEAT_TIMEOUT, "uptime": f"{d}d {h}h {m}m", "businesses": bz, "users": us, "orders": od, "database": "PostgreSQL", "last_backup": "Use /backup in bot"}
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
        from db.database import async_session
        from db.models import Product

        async with async_session() as s:
            p = Product(business_id=b.id, name=body["name"], price=body.get("price", 0))
            s.add(p)
            await s.commit()
            return {"success": True, "id": p.id}
    except Exception as e:
        return {"error": str(e)}


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

        async with async_session() as s:
            p = await s.get(Product, prod_id)
            if not p or p.business_id != b.id:
                return {"error": "Not found"}
            if "name" in body:
                p.name = body["name"]
            if "price" in body:
                p.price = float(body["price"])
            await s.commit()
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


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
            return {"error": "Invalid status"}
        from db.database import async_session
        from db.models import Order

        async with async_session() as s:
            o = await s.get(Order, order_id)
            if not o or o.business_id != b.id:
                return {"error": "Not found"}
            o.status = new_status
            await s.commit()
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}


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
@app.get("/api/backup")
async def api_backup(request: Request):
    await _require_admin(request)
    try:
        from db.backup import backup_database
        path = await backup_database()
        if path:
            return {"success": True, "message": f"Backup saved to {path}"}
        return {"error": "Backup failed"}
    except Exception as e:
        return {"error": str(e)}


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
