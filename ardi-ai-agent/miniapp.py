"""Mini app web server for Ardi AI super admin dashboard."""
import os
import time
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse

app = FastAPI(title="Ardi AI Admin Panel")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")


async def _require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    if ADMIN_API_KEY and auth != f"Bearer {ADMIN_API_KEY}":
        raise HTTPException(status_code=403, detail="Forbidden")


bot_last_heartbeat: float = time.monotonic()
HEARTBEAT_TIMEOUT = 120


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    age = time.monotonic() - bot_last_heartbeat
    if age > HEARTBEAT_TIMEOUT:
        return Response(status_code=503, content="Bot heartbeat expired")
    return Response(status_code=200)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Ardi AI Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--tg-bg:var(--tg-theme-bg-color,#0c0c1a);--tg-card:var(--tg-theme-secondary-bg-color,#16162a);--tg-text:var(--tg-theme-text-color,#eee);--tg-hint:var(--tg-theme-hint-color,#6e6e82);--tg-accent:var(--tg-theme-button-color,#6c5ce7);--tg-accent-text:var(--tg-theme-button-text-color,#fff);--tg-danger:#ff4757;--tg-success:#2ed573;--tg-warning:#ffa502;--radius:14px;--shadow:0 8px 40px rgba(0,0,0,.4)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter',sans-serif;background:var(--tg-bg);color:var(--tg-text);min-height:100vh;overflow-x:hidden;-webkit-font-smoothing:antialiased;padding-bottom:80px}
.page{display:none;padding:16px;max-width:480px;margin:0 auto}.page.active{display:block}

/* Header */
.hdr{display:flex;align-items:center;justify-content:space-between;padding:12px 4px 16px}
.hdr-l{display:flex;align-items:center;gap:12px}
.logo{width:40px;height:40px;background:linear-gradient(135deg,var(--tg-accent),#a29bfe);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;color:#fff;flex-shrink:0;box-shadow:0 4px 16px rgba(108,92,231,.3)}
.hdr-t h1{font-size:18px;font-weight:700;line-height:1.2}.hdr-t p{font-size:12px;color:var(--tg-hint);font-weight:500}
.badge{display:flex;align-items:center;gap:5px;padding:5px 10px;border-radius:20px;font-size:11px;font-weight:600;background:rgba(46,213,115,.12);color:var(--tg-success)}.badge.off{background:rgba(255,71,87,.12);color:var(--tg-danger)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--tg-success);animation:pulse 2s infinite}.badge.off .dot{background:var(--tg-danger);animation:none}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(.8)}}

/* Nav */
.nav{position:fixed;bottom:0;left:0;right:0;background:var(--tg-card);border-top:1px solid rgba(255,255,255,.05);display:flex;justify-content:space-around;padding:8px 0;padding-bottom:calc(8px + env(safe-area-inset-bottom));z-index:100;backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.nav-btn{display:flex;flex-direction:column;align-items:center;gap:2px;background:none;border:none;color:var(--tg-hint);font-family:inherit;font-size:10px;font-weight:500;cursor:pointer;padding:4px 12px;border-radius:8px;transition:.2s;-webkit-tap-highlight-color:transparent}
.nav-btn .nv{font-size:20px;line-height:1}
.nav-btn.active{color:var(--tg-accent)}
.nav-btn:active{transform:scale(.92)}

/* Cards */
.crd{background:var(--tg-card);border-radius:var(--radius);padding:16px;margin-bottom:12px;border:1px solid rgba(255,255,255,.04)}

/* Stat cards */
.st-g{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}
.st-c{background:var(--tg-card);border-radius:var(--radius);padding:14px;border:1px solid rgba(255,255,255,.04);position:relative;overflow:hidden}
.st-c .ic{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;margin-bottom:8px}
.st-c .ic.purple{background:rgba(108,92,231,.15)}.st-c .ic.green{background:rgba(46,213,115,.15)}.st-c .ic.orange{background:rgba(255,165,2,.15)}.st-c .ic.blue{background:rgba(54,164,255,.15)}.st-c .ic.red{background:rgba(255,71,87,.15)}
.st-l{font-size:11px;color:var(--tg-hint);font-weight:500;text-transform:uppercase;letter-spacing:.4px;margin-bottom:2px}
.st-v{font-size:24px;font-weight:800;letter-spacing:-.5px;line-height:1.2}
.st-v.sk{width:50px;height:28px;background:linear-gradient(90deg,rgba(255,255,255,.04) 25%,rgba(255,255,255,.1) 50%,rgba(255,255,255,.04) 75%);background-size:200% 100%;animation:shim 1.5s infinite;border-radius:4px}
@keyframes shim{0%{background-position:200% 0}100%{background-position:-200% 0}}

/* Section head */
.sh{font-size:13px;font-weight:600;color:var(--tg-hint);margin-bottom:10px;padding:0 4px;display:flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.4px}

/* List items */
.li{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:.2s;-webkit-tap-highlight-color:transparent}
.li:last-child{border-bottom:none}.li:active{opacity:.6}
.li-av{width:36px;height:36px;border-radius:10px;background:rgba(108,92,231,.12);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:600;color:var(--tg-accent);flex-shrink:0}
.li-b{flex:1;min-width:0}.li-t{font-size:14px;font-weight:600}.li-s{font-size:12px;color:var(--tg-hint);margin-top:1px}
.li-r{text-align:right;flex-shrink:0}

/* Badge states */
.st{display:inline-block;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600}
.st-active{background:rgba(46,213,115,.12);color:var(--tg-success)}.st-trial{background:rgba(255,165,2,.12);color:var(--tg-warning)}.st-expired{background:rgba(255,71,87,.12);color:var(--tg-danger)}.st-pending{background:rgba(108,92,231,.12);color:var(--tg-accent)}
.st-ok{background:rgba(46,213,115,.12);color:var(--tg-success)}.st-no{background:rgba(255,71,87,.12);color:var(--tg-danger)}

/* Buttons */
.btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:14px;border-radius:12px;font-size:14px;font-weight:600;font-family:inherit;cursor:pointer;border:none;transition:.2s;-webkit-tap-highlight-color:transparent;margin-bottom:8px}
.btn:active{transform:scale(.97)}
.btn-p{background:var(--tg-accent);color:var(--tg-accent-text)}
.btn-s{background:rgba(255,255,255,.06);color:var(--tg-text);border:1px solid rgba(255,255,255,.08)}
.btn-d{background:rgba(255,71,87,.12);color:var(--tg-danger)}

/* Search */
.srch{width:100%;padding:12px 16px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--tg-text);font-family:inherit;font-size:14px;outline:none;margin-bottom:12px;transition:.2s}
.srch:focus{border-color:var(--tg-accent);background:rgba(108,92,231,.06)}
.srch::placeholder{color:var(--tg-hint)}

/* Toast */
.ts{position:fixed;bottom:90px;left:50%;transform:translateX(-50%) translateY(120px);background:var(--tg-card);color:var(--tg-text);padding:12px 18px;border-radius:12px;font-size:13px;font-weight:500;box-shadow:var(--shadow);border:1px solid rgba(255,255,255,.06);z-index:1000;transition:transform .35s cubic-bezier(.34,1.56,.64,1),opacity .25s;opacity:0;max-width:calc(100vw - 48px);text-align:center;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);pointer-events:none}
.ts.s{transform:translateX(-50%) translateY(0);opacity:1}
.ts.er{border-color:rgba(255,71,87,.3)}.ts.ok{border-color:rgba(46,213,115,.3)}

/* Loader */
.ld{position:fixed;top:0;left:0;width:100%;height:3px;z-index:999;display:none;background:rgba(255,255,255,.04)}
.ld.a{display:block}.ld::after{content:'';position:absolute;top:0;left:0;height:100%;width:40%;background:linear-gradient(90deg,var(--tg-accent),#a29bfe);animation:ld 1s ease-in-out infinite;border-radius:2px}
@keyframes ld{0%{left:-40%}100%{left:100%}}

/* Back btn */
.bk{display:inline-flex;align-items:center;gap:6px;background:none;border:none;color:var(--tg-accent);font-family:inherit;font-size:14px;font-weight:600;cursor:pointer;padding:8px 4px;margin-bottom:12px;-webkit-tap-highlight-color:transparent}
.bk:active{opacity:.6}

/* Detail page */
.dt-l{display:flex;flex-direction:column;gap:6px;margin-bottom:16px}
.dt-l .r{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.dt-l .r:last-child{border-bottom:none}
.dt-l .l{font-size:13px;color:var(--tg-hint)}.dt-l .v{font-size:13px;font-weight:600;text-align:right}
.empty{padding:40px 20px;text-align:center;color:var(--tg-hint);font-size:14px}

/* Metric row */
.mr{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:16px}
.mr .c{background:var(--tg-card);border-radius:var(--radius);padding:14px;border:1px solid rgba(255,255,255,.04)}
.mr .lb{font-size:11px;color:var(--tg-hint);font-weight:500;text-transform:uppercase;letter-spacing:.4px}
.mr .vl{font-size:20px;font-weight:800;letter-spacing:-.3px;margin-top:2px}
</style>
</head>
<body>

<div class="ld" id="ld"></div>
<div class="ts" id="ts"></div>

<!-- NAV -->
<nav class="nav" id="nav">
  <button class="nav-btn active" data-page="dashboard"><span class="nv">📊</span>Dashboard</button>
  <button class="nav-btn" data-page="businesses"><span class="nv">🏪</span>Businesses</button>
  <button class="nav-btn" data-page="subscriptions"><span class="nv">💳</span>Subscriptions</button>
  <button class="nav-btn" data-page="orders"><span class="nv">📦</span>Orders</button>
  <button class="nav-btn" data-page="settings"><span class="nv">⚙️</span>Settings</button>
</nav>

<!-- DASHBOARD -->
<div class="page active" id="page-dashboard">
  <div class="hdr">
    <div class="hdr-l">
      <div class="logo">A</div>
      <div class="hdr-t"><h1>Ardi AI</h1><p>Admin Dashboard</p></div>
    </div>
    <div class="badge" id="statusBadge"><span class="dot"></span><span id="statusText">Online</span></div>
  </div>
  <div class="st-g" id="dashStats"></div>
  <div class="sh">💰 Revenue</div>
  <div class="st-g" id="revStats"></div>
  <div class="sh">🕐 Recent Orders</div>
  <div class="crd" id="recentOrders"><div class="empty">Loading...</div></div>
</div>

<!-- BUSINESSES -->
<div class="page" id="page-businesses">
  <div class="hdr"><div class="hdr-t"><h1>🏪 Businesses</h1><p id="bizCount">— registered</p></div></div>
  <input class="srch" id="bizSearch" placeholder="Search businesses..." oninput="filterBiz()">
  <div class="crd" id="bizList"><div class="empty">Loading...</div></div>
</div>

<!-- SUBSCRIPTIONS -->
<div class="page" id="page-subscriptions">
  <div class="hdr">
    <div class="hdr-t"><h1>💳 Subscriptions</h1></div>
    <select id="subFilter" onchange="loadSubs()" style="background:var(--tg-card);color:var(--tg-text);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:6px 10px;font-size:12px;font-family:inherit;outline:none">
      <option value="all">All</option>
      <option value="active">Active</option>
      <option value="trial">Trial</option>
      <option value="suspended">Suspended</option>
      <option value="expired">Expired</option>
      <option value="awaiting_payment">Pending</option>
    </select>
  </div>
  <div id="subList"><div class="empty">Loading...</div></div>
</div>

<!-- ORDERS -->
<div class="page" id="page-orders">
  <div class="hdr"><div class="hdr-t"><h1>📦 Orders</h1><p id="ordCount">— total</p></div></div>
  <div class="mr" id="ordStats"></div>
  <div class="crd" id="ordList"><div class="empty">Loading...</div></div>
</div>

<!-- SETTINGS -->
<div class="page" id="page-settings">
  <div class="hdr"><div class="hdr-t"><h1>⚙️ Settings</h1></div></div>
  <div class="sh">System</div>
  <div class="crd" id="sysHealth"></div>
  <div class="sh">📢 Broadcast</div>
  <div class="crd">
    <div style="font-size:13px;color:var(--tg-hint);margin-bottom:10px">Send a message to all registered business owners</div>
    <textarea id="broadcastMsg" style="width:100%;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.06);background:rgba(255,255,255,.04);color:var(--tg-text);font-family:inherit;font-size:14px;outline:none;resize:none;min-height:80px;margin-bottom:10px;transition:.2s" placeholder="Type your broadcast message..." onfocus="this.style.borderColor='var(--tg-accent)'" onblur="this.style.borderColor='rgba(255,255,255,.06)'"></textarea>
    <div style="display:flex;gap:8px">
      <button class="btn btn-p" style="flex:1;margin:0" onclick="sendBroadcast()">📨 Send to All</button>
    </div>
    <div id="broadcastStatus" style="font-size:12px;color:var(--tg-hint);margin-top:8px;text-align:center"></div>
  </div>
  <div class="sh">Actions</div>
  <button class="btn btn-p" onclick="backupDB()">💾 Backup Database</button>
  <button class="btn btn-d" onclick="confirmRevoke()">🔒 Revoke All Trials</button>
</div>

<!-- DETAIL MODAL (inline) -->
<div class="page" id="page-detail">
  <button class="bk" onclick="showPage('dashboard')">← Back</button>
  <div id="detailContent"></div>
</div>

<script src="https://telegram.org/js/telegram-web-app.js"></script>
<script>
const KEY = "{{ADMIN_API_KEY}}";
Telegram.WebApp.ready();
Telegram.WebApp.expand();

function hd(){const h={"Content-Type":"application/json"};if(KEY)h["Authorization"]="Bearer "+KEY;return h}
function $(id){return document.getElementById(id)}
function toast(m,t){const e=$('ts');e.textContent=m;e.className='ts'+(t?' '+t:'');requestAnimationFrame(()=>{e.classList.add('s');clearTimeout(e._h);e._h=setTimeout(()=>e.classList.remove('s'),3000)})}
function ld(on){$('ld').classList.toggle('a',on)}
function esc(t){const d=document.createElement('div');d.appendChild(document.createTextNode(t));return d.innerHTML}

// Navigation
document.querySelectorAll('.nav-btn').forEach(b=>{
  b.onclick=()=>showPage(b.dataset.page)
});
function showPage(pg){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  const el=$('page-'+pg);
  if(el){el.classList.add('active')}
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.page===pg));
  if(pg==='dashboard')loadDashboard();
  else if(pg==='businesses')loadBiz();
  else if(pg==='subscriptions')loadSubs();
  else if(pg==='orders')loadOrders();
  else if(pg==='settings')loadSettings();
}

// API helpers
async function api(path,opts){
  ld(true);
  try{
    const r=await fetch(path,{headers:hd(),...opts});
    if(r.status===403){toast('Unauthorized','er');return null}
    if(!r.ok)throw new Error('HTTP '+r.status);
    return await r.json();
  }catch(e){toast('Error: '+e.message,'er');return null}
  finally{ld(false)}
}

// ─── DASHBOARD ──────────────────────────────────────────────
async function loadDashboard(){
  const d=await api('/api/admin/dashboard');
  if(!d)return;
  const status=$('statusBadge');
  if(d.bot_online){status.className='badge';$('statusText').textContent='Online'}
  else{status.className='badge off';$('statusText').textContent='Offline'}

  $('dashStats').innerHTML=[
    {ic:'🏪',cl:'purple',lb:'Businesses',v:d.businesses},
    {ic:'✅',cl:'green',lb:'Active Subs',v:d.active_subscriptions},
    {ic:'📦',cl:'orange',lb:'Orders (30d)',v:d.orders_30d},
    {ic:'👥',cl:'blue',lb:'Users',v:d.users},
  ].map(s=>`<div class="st-c"><div class="ic ${s.cl}">${s.ic}</div><div class="st-l">${s.lb}</div><div class="st-v">${esc(String(s.v??'—'))}</div></div>`).join('');

  $('revStats').innerHTML=[
    {ic:'💰',cl:'green',lb:'Sub Revenue',v:'ETB '+(d.sub_revenue??0).toLocaleString()},
    {ic:'📊',cl:'purple',lb:'Avg Order',v:'ETB '+(d.avg_order_value??0).toLocaleString()},
    {ic:'📈',cl:'blue',lb:'Pending Orders',v:d.pending_orders??0},
    {ic:'⭐',cl:'orange',lb:'Trial Businesses',v:d.trial_count??0},
  ].map(s=>`<div class="st-c"><div class="ic ${s.cl}">${s.ic}</div><div class="st-l">${s.lb}</div><div class="st-v">${esc(String(s.v))}</div></div>`).join('');

  const ro=d.recent_orders||[];
  if(ro.length===0){$('recentOrders').innerHTML='<div class="empty">No recent orders</div>';return}
  $('recentOrders').innerHTML=ro.map(o=>`<div class="li" onclick="showOrder(${o.id})">
    <div class="li-av">#${o.id}</div>
    <div class="li-b"><div class="li-t">${esc(o.customer_name||'Customer')}</div><div class="li-s">${esc(o.business_name||'')} · ${o.item_count||0} items</div></div>
    <div class="li-r"><div style="font-weight:700">ETB ${(+o.total_price).toLocaleString()}</div><span class="st st-${o.status==='pending'?'pending':o.status==='confirmed'?'active':o.status==='completed'?'ok':'no'}">${esc(o.status)}</span></div>
  </div>`).join('');
}

// ─── BUSINESSES ──────────────────────────────────────────────
let allBiz=[];
async function loadBiz(){
  const d=await api('/api/admin/businesses');
  if(!d)return;
  allBiz=d.businesses||[];
  $('bizCount').textContent=allBiz.length+' registered';
  renderBiz();
}
function filterBiz(){
  renderBiz($('bizSearch').value.toLowerCase());
}
function renderBiz(q){
  const items=q?allBiz.filter(b=>(b.name||'').toLowerCase().includes(q)||(b.phone||'').includes(q)):allBiz;
  if(items.length===0){$('bizList').innerHTML='<div class="empty">No businesses found</div>';return}
  $('bizList').innerHTML=items.map(b=>`<div class="li" onclick="showBiz(${b.id})">
    <div class="li-av">${(b.name||'?')[0].toUpperCase()}</div>
    <div class="li-b"><div class="li-t">${esc(b.name)}</div><div class="li-s">${esc(b.owner_name||'')} · ${b.product_count||0} products</div></div>
    <div class="li-r"><span class="st st-${b.subscription_status==='active'?'active':b.subscription_status==='trial'?'trial':b.subscription_status==='expired'?'expired':'pending'}">${esc(b.subscription_status||'unknown')}</span></div>
  </div>`).join('');
}

// ─── SUBSCRIPTIONS ───────────────────────────────────────────
async function loadSubs(){
  const f=$('subFilter').value;
  const d=await api('/api/admin/subscriptions?filter='+f);
  if(!d)return;
  const subs=d.subscriptions||[];
  if(subs.length===0){$('subList').innerHTML='<div class="empty">No subscriptions</div>';return}
  $('subList').innerHTML=subs.map(s=>`<div class="crd" style="padding:14px">
    <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px">
      <div><div style="font-weight:700">${esc(s.business_name)}</div><div style="font-size:12px;color:var(--tg-hint)">Plan: ${esc(s.plan||'—')}</div></div>
      <span class="st st-${s.status==='active'?'active':s.status==='trial'?'trial':s.status==='expired'?'expired':'pending'}">${esc(s.status)}</span>
    </div>
    <div style="font-size:12px;color:var(--tg-hint);margin-bottom:${s.status==='awaiting_payment'?'12':'0'}px">${s.end_date?('Ends: '+new Date(s.end_date).toLocaleDateString()):''}</div>
    ${s.status==='awaiting_payment'?`<button class="btn btn-p" style="padding:10px;font-size:13px" onclick="confirmPay(${s.business_id},'${s.plan}')">✅ Confirm Payment</button>`:''}
    ${s.status==='active'||s.status==='trial'?`<button class="btn btn-d" style="padding:10px;font-size:13px;margin:0" onclick="revokeSub(${s.business_id})">🔒 Revoke</button>`:''}
  </div>`).join('');
}

async function confirmPay(bizId,plan){
  const d=await api('/api/admin/subscriptions/confirm',{method:'POST',body:JSON.stringify({business_id:bizId,plan:plan})});
  if(d&&d.success){toast('✅ Subscription activated','ok');loadSubs()}
}
async function revokeSub(bizId){
  Telegram.WebApp.showConfirm('Revoke this subscription?',async ok=>{
    if(!ok)return;
    const d=await api('/api/admin/subscriptions/revoke',{method:'POST',body:JSON.stringify({business_id:bizId})});
    if(d&&d.success){toast('🔒 Subscription revoked','ok');loadSubs()}
  });
}

// ─── ORDERS ──────────────────────────────────────────────────
async function loadOrders(){
  const d=await api('/api/admin/orders');
  if(!d)return;
  $('ordCount').textContent=(d.total_orders||0)+' total';
  $('ordStats').innerHTML=[
    {lb:'Total Revenue',v:'ETB '+(d.total_revenue||0).toLocaleString()},
    {lb:'Pending',v:d.pending_count||0},
    {lb:'Completed',v:d.completed_count||0},
    {lb:'Cancelled',v:d.cancelled_count||0},
  ].map(s=>`<div class="c"><div class="lb">${s.lb}</div><div class="vl">${esc(String(s.v))}</div></div>`).join('');

  const ords=d.orders||[];
  if(ords.length===0){$('ordList').innerHTML='<div class="empty">No orders</div>';return}
  $('ordList').innerHTML=ords.map(o=>`<div class="li">
    <div class="li-av">#${o.id}</div>
    <div class="li-b"><div class="li-t">${esc(o.customer_name||'Customer')}</div><div class="li-s">${esc(o.business_name||'')}</div></div>
    <div class="li-r"><div style="font-weight:700">ETB ${(+o.total_price).toLocaleString()}</div><span class="st st-${o.status==='pending'?'pending':o.status==='confirmed'?'active':o.status==='completed'?'ok':'no'}">${esc(o.status)}</span></div>
  </div>`).join('');
}

// ─── SETTINGS ────────────────────────────────────────────────
async function loadSettings(){
  const d=await api('/api/admin/system');
  if(!d)return;
  $('sysHealth').innerHTML=`<div class="dt-l">
    <div class="r"><span class="l">Bot Status</span><span class="v"><span class="st st-${d.bot_online?'ok':'no'}">${d.bot_online?'Online':'Offline'}</span></span></div>
    <div class="r"><span class="l">Uptime</span><span class="v">${esc(d.uptime||'—')}</span></div>
    <div class="r"><span class="l">Last Backup</span><span class="v">${esc(d.last_backup||'Never')}</span></div>
    <div class="r"><span class="l">Database</span><span class="v">${esc(d.database||'—')}</span></div>
    <div class="r"><span class="l">Total Businesses</span><span class="v">${d.businesses||0}</span></div>
    <div class="r"><span class="l">Total Orders</span><span class="v">${d.orders||0}</span></div>
    <div class="r"><span class="l">Total Users</span><span class="v">${d.users||0}</span></div>
  </div>`;
}

async function backupDB(){
  const d=await api('/api/backup',{method:'POST'});
  if(d&&d.success){toast('✅ Backup saved','ok');loadSettings()}
}
function confirmRevoke(){
  Telegram.WebApp.showConfirm('Revoke ALL trial subscriptions? This cannot be undone.',async ok=>{
    if(!ok)return;
    const d=await api('/api/admin/subscriptions/revoke-all',{method:'POST'});
    if(d&&d.success){toast('🔒 All trials revoked','ok');loadSettings()}
  });
}

// ─── DETAIL PAGES ────────────────────────────────────────────
async function showBiz(id){
  const d=await api('/api/admin/businesses/'+id);
  if(!d)return;
  const suspended=d.subscription_status==='suspended';
  $('detailContent').innerHTML=`<div class="crd">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <div class="logo" style="width:48px;height:48px;font-size:22px">${(d.name||'?')[0]}</div>
      <div><div style="font-size:18px;font-weight:700">${esc(d.name)}</div><div style="font-size:13px;color:var(--tg-hint)">ID: ${d.id}</div></div>
    </div>
    <div class="dt-l">
      <div class="r"><span class="l">Status</span><span class="v"><span class="st st-${suspended?'no':d.subscription_status==='active'?'active':d.subscription_status==='trial'?'trial':'expired'}">${esc(d.subscription_status)}</span></span></div>
      <div class="r"><span class="l">Plan</span><span class="v">${esc(d.plan||'—')}</span></div>
      <div class="r"><span class="l">Owner</span><span class="v">${esc(d.owner_name||'—')}</span></div>
      <div class="r"><span class="l">Phone</span><span class="v">${esc(d.phone||'—')}</span></div>
      <div class="r"><span class="l">Products</span><span class="v">${d.product_count||0}</span></div>
      <div class="r"><span class="l">Orders</span><span class="v">${d.order_count||0}</span></div>
      <div class="r"><span class="l">AI Active</span><span class="v">${d.ai_active?'✅ Yes':'❌ No'}</span></div>
      <div class="r"><span class="l">Created</span><span class="v">${d.created_at?new Date(d.created_at).toLocaleDateString():'—'}</span></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
      ${suspended
        ? `<button class="btn btn-p" style="flex:1;margin:0;padding:10px;font-size:13px" onclick="unsuspendBiz(${d.id})">✅ Unsuspend</button>`
        : `<button class="btn btn-s" style="flex:1;margin:0;padding:10px;font-size:13px" onclick="suspendBiz(${d.id})">⏸️ Suspend</button>`
      }
      <button class="btn btn-d" style="flex:1;margin:0;padding:10px;font-size:13px" onclick="deleteBiz(${d.id})">🗑️ Delete</button>
    </div>
  </div>`;
  showPage('detail');
}

async function suspendBiz(id){
  Telegram.WebApp.showConfirm('Suspend this business? AI will be disabled and owner notified.',async ok=>{
    if(!ok)return;
    const d=await api('/api/admin/businesses/'+id+'/suspend',{method:'POST'});
    if(d&&d.success){toast('⏸️ Business suspended','ok');showBiz(id)}
  });
}
async function unsuspendBiz(id){
  const d=await api('/api/admin/businesses/'+id+'/unsuspend',{method:'POST'});
  if(d&&d.success){toast('✅ Business unsuspended','ok');showBiz(id)}
}
async function deleteBiz(id){
  Telegram.WebApp.showConfirm('PERMANENTLY delete this business and all its data? This CANNOT be undone.',async ok=>{
    if(!ok)return;
    const d=await api('/api/admin/businesses/'+id+'/delete',{method:'POST'});
    if(d&&d.success){toast('🗑️ Business deleted','ok');showPage('businesses');loadBiz()}
  });
}

async function sendBroadcast(){
  const msg=$('broadcastMsg').value.trim();
  if(!msg){toast('Enter a message','er');return}
  Telegram.WebApp.showConfirm('Send this message to ALL registered business owners?',async ok=>{
    if(!ok)return;
    $('broadcastStatus').textContent='Sending...';
    const d=await api('/api/admin/broadcast',{method:'POST',body:JSON.stringify({message:msg})});
    if(d&&d.success){toast('📨 Sent to '+d.sent+' businesses','ok');$('broadcastStatus').textContent='Sent to '+d.sent+' businesses';$('broadcastMsg').value=''}
    else{$('broadcastStatus').textContent='Failed: '+(d&&d.error||'unknown')}
  });
}

async function showOrder(id){
  const d=await api('/api/admin/orders/'+id);
  if(!d)return;
  $('detailContent').innerHTML=`<div class="crd">
    <div style="font-size:18px;font-weight:700;margin-bottom:12px">Order #${d.id}</div>
    <div class="dt-l">
      <div class="r"><span class="l">Customer</span><span class="v">${esc(d.customer_name||'—')}</span></div>
      <div class="r"><span class="l">Phone</span><span class="v">${esc(d.customer_phone||'—')}</span></div>
      <div class="r"><span class="l">Address</span><span class="v">${esc(d.customer_address||'—')}</span></div>
      <div class="r"><span class="l">Business</span><span class="v">${esc(d.business_name||'—')}</span></div>
      <div class="r"><span class="l">Total</span><span class="v" style="font-weight:700">ETB ${(+d.total_price).toLocaleString()}</span></div>
      <div class="r"><span class="l">Status</span><span class="v"><span class="st st-${d.status==='pending'?'pending':d.status==='confirmed'?'active':d.status==='completed'?'ok':'no'}">${esc(d.status)}</span></span></div>
      <div class="r"><span class="l">Date</span><span class="v">${d.created_at?new Date(d.created_at).toLocaleString():'—'}</span></div>
    </div>
    ${d.items&&d.items.length?`<div style="font-size:13px;font-weight:600;color:var(--tg-hint);margin:8px 0 4px">Items</div>
    ${d.items.map(i=>`<div style="display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid rgba(255,255,255,.04)">
      <span>${esc(i.product_name||'Item')} ×${i.quantity||1}</span>
      <span style="font-weight:600">ETB ${(+i.unit_price).toLocaleString()}</span>
    </div>`).join('')}`:''}
  </div>`;
  showPage('detail');
}

// Init
loadDashboard();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML.replace("{{ADMIN_API_KEY}}", ADMIN_API_KEY)


# ─── API ENDPOINTS ──────────────────────────────────────────────

@app.get("/api/admin/dashboard")
async def api_dashboard(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business, User, Order, OrderItem
        from sqlalchemy import select, func
        from datetime import datetime, timedelta, timezone

        async with async_session() as s:
            biz_count = (await s.execute(select(func.count(Business.id)))).scalar() or 0
            active_subs = (await s.execute(
                select(func.count(Business.id)).where(Business.subscription_status == "active")
            )).scalar() or 0
            trial_count = (await s.execute(
                select(func.count(Business.id)).where(Business.subscription_status == "trial")
            )).scalar() or 0
            user_count = (await s.execute(select(func.count(User.id)))).scalar() or 0
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            orders_30d = (await s.execute(
                select(func.count(Order.id)).where(Order.created_at >= cutoff)
            )).scalar() or 0
            pending_orders = (await s.execute(
                select(func.count(Order.id)).where(Order.status == "pending")
            )).scalar() or 0

            # Revenue from confirmed/completed orders
            rev_row = (await s.execute(
                select(func.coalesce(func.sum(Order.total_price), 0)).where(
                    Order.status.in_(["confirmed", "completed"]),
                    Order.created_at >= cutoff,
                )
            )).scalar() or 0.0
            sub_revenue = active_subs * 500  # rough monthly estimate

            # Avg order value
            avg_row = (await s.execute(
                select(func.coalesce(func.avg(Order.total_price), 0)).where(
                    Order.status.in_(["confirmed", "completed"])
                )
            )).scalar() or 0.0

            # Recent orders
            recent = (await s.execute(
                select(Order).order_by(Order.created_at.desc()).limit(5)
            )).scalars().all()
            recent_orders = []
            for o in recent:
                biz = await s.get(Business, o.business_id)
                item_count = (await s.execute(
                    select(func.count(OrderItem.id)).where(OrderItem.order_id == o.id)
                )).scalar() or 0
                recent_orders.append({
                    "id": o.id,
                    "customer_name": o.customer_name,
                    "business_name": biz.name if biz else "",
                    "total_price": str(o.total_price),
                    "status": o.status,
                    "item_count": item_count,
                    "created_at": o.created_at.isoformat() if o.created_at else "",
                })

        return {
            "bot_online": (time.monotonic() - bot_last_heartbeat) < HEARTBEAT_TIMEOUT,
            "businesses": biz_count,
            "active_subscriptions": active_subs,
            "trial_count": trial_count,
            "users": user_count,
            "orders_30d": orders_30d,
            "pending_orders": pending_orders,
            "sub_revenue": round(sub_revenue, 2),
            "avg_order_value": round(float(avg_row), 2),
            "order_revenue_30d": round(float(rev_row), 2),
            "recent_orders": recent_orders,
        }
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
            result = []
            for b in rows:
                pc = (await s.execute(
                    select(func.count(Product.id)).where(Product.business_id == b.id)
                )).scalar() or 0
                oc = (await s.execute(
                    select(func.count(Order.id)).where(Order.business_id == b.id)
                )).scalar() or 0
                result.append({
                    "id": b.id,
                    "name": b.name,
                    "owner_name": b.name,
                    "phone": b.phone,
                    "subscription_status": b.subscription_status,
                    "ai_active": b.ai_active,
                    "product_count": pc,
                    "order_count": oc,
                    "created_at": b.created_at.isoformat() if b.created_at else "",
                })
            return {"businesses": result}
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
                raise HTTPException(status_code=404, detail="Not found")
            pc = (await s.execute(
                select(func.count(Product.id)).where(Product.business_id == b.id)
            )).scalar() or 0
            oc = (await s.execute(
                select(func.count(Order.id)).where(Order.business_id == b.id)
            )).scalar() or 0
            return {
                "id": b.id,
                "name": b.name,
                "description": b.description,
                "address": b.address,
                "phone": b.phone,
                "owner_name": b.name,
                "subscription_status": b.subscription_status,
                "plan": b.subscription_plan,
                "subscription_end": b.subscription_end.isoformat() if b.subscription_end else None,
                "ai_active": b.ai_active,
                "orders_enabled": b.orders_enabled,
                "product_count": pc,
                "order_count": oc,
                "created_at": b.created_at.isoformat() if b.created_at else "",
            }
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
                await bot.send_message(int(ADMIN_TELEGRAM_ID), f"⏸️ Business *{b.name}* (ID: {biz_id}) has been suspended.", parse_mode="Markdown")
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
                await bot.send_message(int(ADMIN_TELEGRAM_ID), f"🗑️ Business *{name}* (ID: {biz_id}) has been permanently deleted.", parse_mode="Markdown")
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
        message = body.get("message", "").strip()
        if not message:
            return {"error": "Message is required"}
        from db.database import async_session
        from db.models import Business
        from sqlalchemy import select
        from telegram import Bot
        from config import TELEGRAM_TOKEN
        import asyncio
        bot = Bot(TELEGRAM_TOKEN)
        async with async_session() as s:
            rows = (await s.execute(
                select(Business).where(Business.telegram_chat_id.isnot(None))
            )).scalars().all()
        sent = 0
        failed = 0
        for b in rows:
            try:
                await bot.send_message(
                    chat_id=b.telegram_chat_id,
                    text=f"📢 *Admin Announcement*\n\n{message}",
                    parse_mode="Markdown",
                )
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        return {"success": True, "sent": sent, "failed": failed, "total": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/subscriptions")
async def api_subscriptions(request: Request, filter: str = "all"):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business

        async with async_session() as s:
            query = select(Business).order_by(Business.created_at.desc())
            if filter != "all":
                query = query.where(Business.subscription_status == filter)
            rows = (await s.execute(query)).scalars().all()
            subs = []
            for b in rows:
                subs.append({
                    "business_id": b.id,
                    "business_name": b.name,
                    "status": b.subscription_status,
                    "plan": b.subscription_plan,
                    "end_date": b.subscription_end.isoformat() if b.subscription_end else None,
                    "created_at": b.created_at.isoformat() if b.created_at else "",
                })
            return {"subscriptions": subs}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/subscriptions/confirm")
async def api_confirm_subscription(request: Request):
    await _require_admin(request)
    try:
        body = await request.json()
        biz_id = body["business_id"]
        plan = body.get("plan", "monthly")
        from bot.handlers import _activate_subscription
        ok = await _activate_subscription(None, biz_id, plan)
        return {"success": ok}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/subscriptions/revoke")
async def api_revoke_subscription(request: Request):
    await _require_admin(request)
    try:
        body = await request.json()
        biz_id = body["business_id"]
        from db.database import async_session
        from db.models import Business
        async with async_session() as s:
            b = await s.get(Business, biz_id)
            if b:
                b.subscription_status = "expired"
                b.subscription_end = None
                b.subscription_plan = None
                await s.commit()
                return {"success": True}
        return {"success": False, "error": "Not found"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/admin/subscriptions/revoke-all")
async def api_revoke_all_trials(request: Request):
    await _require_admin(request)
    try:
        from db.database import async_session
        from db.models import Business
        from sqlalchemy import select
        async with async_session() as s:
            rows = (await s.execute(
                select(Business).where(Business.subscription_status == "trial")
            )).scalars().all()
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
            total = (await s.execute(select(func.count(Order.id)))).scalar() or 0
            pending = (await s.execute(
                select(func.count(Order.id)).where(Order.status == "pending")
            )).scalar() or 0
            completed = (await s.execute(
                select(func.count(Order.id)).where(Order.status == "completed")
            )).scalar() or 0
            cancelled = (await s.execute(
                select(func.count(Order.id)).where(Order.status == "cancelled")
            )).scalar() or 0
            rev = (await s.execute(
                select(func.coalesce(func.sum(Order.total_price), 0)).where(
                    Order.status.in_(["confirmed", "completed"])
                )
            )).scalar() or 0.0

            rows = (await s.execute(
                select(Order).order_by(Order.created_at.desc()).limit(50)
            )).scalars().all()
            orders = []
            for o in rows:
                biz = await s.get(Business, o.business_id)
                orders.append({
                    "id": o.id,
                    "customer_name": o.customer_name,
                    "business_name": biz.name if biz else "",
                    "total_price": str(o.total_price),
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else "",
                })
            return {
                "total_orders": total,
                "pending_count": pending,
                "completed_count": completed,
                "cancelled_count": cancelled,
                "total_revenue": round(float(rev), 2),
                "orders": orders,
            }
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
                raise HTTPException(status_code=404, detail="Not found")
            biz = await s.get(Business, o.business_id)
            items = (await s.execute(
                select(OrderItem).where(OrderItem.order_id == o.id)
            )).scalars().all()
            return {
                "id": o.id,
                "customer_name": o.customer_name,
                "customer_phone": o.customer_phone,
                "customer_address": o.customer_address,
                "business_name": biz.name if biz else "",
                "total_price": str(o.total_price),
                "status": o.status,
                "created_at": o.created_at.isoformat() if o.created_at else "",
                "items": [{
                    "product_name": i.product_name,
                    "quantity": i.quantity,
                    "unit_price": str(i.unit_price),
                } for i in items],
            }
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
            biz_count = (await s.execute(select(func.count(Business.id)))).scalar() or 0
            user_count = (await s.execute(select(func.count(User.id)))).scalar() or 0
            ord_count = (await s.execute(select(func.count(Order.id)))).scalar() or 0

        uptime_secs = time.monotonic()
        days = int(uptime_secs // 86400)
        hours = int((uptime_secs % 86400) // 3600)
        mins = int((uptime_secs % 3600) // 60)
        uptime = f"{days}d {hours}h {mins}m"

        bot_online = (time.monotonic() - bot_last_heartbeat) < HEARTBEAT_TIMEOUT
        return {
            "bot_online": bot_online,
            "uptime": uptime,
            "businesses": biz_count,
            "users": user_count,
            "orders": ord_count,
            "database": "PostgreSQL",
            "last_backup": "Check /backup in bot",
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("MINI_APP_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
