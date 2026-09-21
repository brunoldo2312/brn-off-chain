# web_server.py — API REST + dashboard RWA + CLOB + MEV.
#
# Melhorias sobre a versão anterior:
#   - Thread-safe (usa blockchain.lock)
#   - Endpoints CLOB: orderbook, orders, trades, commits
#   - Dashboard com seção de order book + minhas ordens + commits
#   - IP real atrás de NGROK (X-Forwarded-For)
#   - CSRF token para POSTs
#   - Cache de summary (2s)
#   - Faucet com assinatura correta
#   - Reaproveita NGROK quando possível
#   - Import de Transaction no topo
#   - Health com info de NGROK

import os
import secrets
import threading
import time
from datetime import datetime
from functools import wraps

from flask import (Flask, Response, jsonify, render_template_string,
                   request)
from flask_limiter import Limiter

# ---- importa a Blockchain (funciona com brn.py monolítico OU bruno_blockchain_real.py) ----
try:
    from brn import Transaction, Blockchain, MEV_COMMIT_MIN_BLOCKS
except ImportError:
    from bruno_blockchain_real import (Transaction, Blockchain,
                                        MEV_COMMIT_MIN_BLOCKS)

# =====================================================================
# Config
# =====================================================================
NGROK_AUTHTOKEN   = os.environ.get("NGROK_AUTHTOKEN", "")
WEB_HOST          = os.environ.get("BRN_WEB_HOST", "0.0.0.0")
WEB_PORT          = int(os.environ.get("BRN_WEB_PORT", "5000"))
USE_NGROK         = os.environ.get("BRN_USE_NGROK", "0") == "1"

WEB_USER          = os.environ.get("BRN_WEB_USER", "admin")
WEB_PASS          = os.environ.get("BRN_WEB_PASS", "")
FAUCET_MAX_PER_IP = os.environ.get("BRN_FAUCET_MAX_PER_IP", "3")

DEFAULT_BASE      = os.environ.get("BRN_BASE", "BRN")
DEFAULT_QUOTE     = os.environ.get("BRN_QUOTE", "USDC")

CSRF_TOKEN        = os.environ.get("BRN_CSRF_TOKEN") or secrets.token_hex(16)
CACHE_TTL_SUMMARY = float(os.environ.get("BRN_SUMMARY_TTL", "2.0"))

# =====================================================================
# Flask + Limiter (com IP real atrás de proxy)
# =====================================================================
app = Flask(__name__)

def _client_ip():
    """Retorna o IP real do cliente, considerando proxies (NGROK)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("X-Real-IP", "")
    if real:
        return real
    return request.remote_addr or "unknown"


limiter = Limiter(_client_ip, app=app,
                  default_limits=["300 per hour"],
                  storage_uri="memory://")

# =====================================================================
# Estado global (injetado via set_blockchain)
# =====================================================================
_blockchain_ref = None
_faucet_handler = None
_summary_cache = {"ts": 0.0, "data": None}
_web_public_url = None


def set_blockchain(bc):
    global _blockchain_ref
    _blockchain_ref = bc


def set_faucet_handler(fn):
    global _faucet_handler
    _faucet_handler = fn


def set_public_url(url: str):
    global _web_public_url
    _web_public_url = url


# =====================================================================
# Auth + CSRF
# =====================================================================
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not WEB_PASS:
            return Response("Servidor mal configurado: BRN_WEB_PASS vazio.",
                            500)
        auth = request.authorization
        if not auth or auth.username != WEB_USER or auth.password != WEB_PASS:
            return Response("Acesso negado.", 401,
                            {"WWW-Authenticate": 'Basic realm="BRN RWA"'})
        return f(*args, **kwargs)
    return decorated


def require_csrf(f):
    """Valida header X-CSRF-Token. Use em POSTs."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token", "")
        if token != CSRF_TOKEN:
            return jsonify(ok=False, msg="CSRF inválido"), 403
        return f(*args, **kwargs)
    return decorated


# =====================================================================
# Helpers
# =====================================================================
def _iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"


def _short(addr: str) -> str:
    if not addr:
        return "—"
    return addr[:10] + "…" + addr[-6:] if len(addr) > 20 else addr


def _tx_dto(tx: dict, block_index=None, status="confirmada") -> dict:
    md = tx.get("metadata", {}) or {}
    return {
        "type": tx.get("type"),
        "asset_id": tx.get("asset_id"),
        "from": tx.get("from"),
        "to": tx.get("to"),
        "amount": tx.get("amount", 0.0),
        "timestamp": tx.get("timestamp", 0.0),
        "datetime": _iso(tx.get("timestamp", 0.0)),
        "status": status,
        "block": block_index,
        "side": md.get("side", ""),
        "price": md.get("price", None),
        "quote": md.get("quote", ""),
        "commit_hash": md.get("commit_hash", ""),
        "order_id": md.get("order_id", ""),
        "nonce": tx.get("nonce"),
    }


# =====================================================================
# Dashboard HTML
# =====================================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>BRN RWA + CLOB — Painel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system,"Segoe UI",Roboto,sans-serif;
         background:#0d1117; color:#e6edf3; margin:0; padding:24px; }
  h1 { margin:0 0 4px; font-size:22px; }
  h2 { font-size:15px; margin:24px 0 8px; color:#8b949e;
       text-transform:uppercase; letter-spacing:.5px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .cards { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:12px 16px; min-width:150px; }
  .card .label { font-size:11px; color:#8b949e; text-transform:uppercase; }
  .card .value { font-size:20px; font-weight:600; margin-top:4px; }
  table { width:100%; border-collapse:collapse; background:#161b22;
          border-radius:8px; overflow:hidden; margin-bottom:12px; }
  th,td { padding:10px 12px; text-align:left; font-size:13px;
          border-bottom:1px solid #21262d; }
  th { background:#1c2128; font-weight:600; color:#8b949e;
       text-transform:uppercase; font-size:11px; }
  tr:hover { background:#1c2128; }
  .addr { font-family:Consolas,monospace; font-size:12px; color:#58a6ff; }
  .amount { color:#3fb950; font-weight:600; }
  .price  { color:#58a6ff; font-weight:600; font-family:monospace; }
  .badge { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
  .badge.confirmada { background:#1f6feb33; color:#58a6ff; }
  .badge.pendente { background:#d2992233; color:#d29922; }
  .badge.approved { background:#23863633; color:#3fb950; }
  .badge.pending { background:#d2992233; color:#d29922; }
  .badge.revoked, .badge.rejected { background:#f8514933; color:#f85149; }
  .badge.open { background:#23863633; color:#3fb950; }
  .badge.partial { background:#d2992233; color:#d29922; }
  .badge.filled { background:#1f6feb33; color:#58a6ff; }
  .badge.cancelled { background:#f8514933; color:#f85149; }
  .empty { text-align:center; color:#8b949e; padding:40px; }
  .form { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:16px; margin-bottom:20px; display:flex; gap:8px; flex-wrap:wrap; }
  .form input, .form select { padding:8px 12px; background:#0d1117; color:#e6edf3;
                  border:1px solid #30363d; border-radius:6px; font-family:monospace; }
  .form button { padding:8px 16px; background:#238636; color:#fff; border:0;
                 border-radius:6px; cursor:pointer; font-weight:600; }
  .form button:hover { background:#2ea043; }
  .book-cols { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .book-side h3 { font-size:12px; text-transform:uppercase;
                  letter-spacing:.5px; margin:0 0 6px; }
  .book-side.asks h3 { color:#f85149; }
  .book-side.bids h3 { color:#3fb950; }
</style>
</head>
<body>
  <h1>🏦 BRN RWA + CLOB</h1>
  <div class="sub">
    Atualização a cada 3s · <span id="last-update">—</span> ·
    <span id="public-url"></span>
  </div>

  <div class="cards">
    <div class="card"><div class="label">Blocos</div><div class="value" id="stat-blocks">—</div></div>
    <div class="card"><div class="label">Ativos</div><div class="value" id="stat-assets">—</div></div>
    <div class="card"><div class="label">KYC aprovados</div><div class="value" id="stat-kyc">—</div></div>
    <div class="card"><div class="label">Ordens abertas</div><div class="value" id="stat-orders">—</div></div>
    <div class="card"><div class="label">Trades</div><div class="value" id="stat-trades">—</div></div>
    <div class="card"><div class="label">Slashed</div><div class="value" id="stat-slashed">—</div></div>
    <div class="card"><div class="label">Finalizado</div><div class="value" id="stat-final">—</div></div>
  </div>

  <div class="form">
    <input id="faucet-addr" placeholder="Endereço brn1… para o faucet BRN" size="50">
    <button onclick="requestFaucet()">Pedir BRN</button>
    <span id="faucet-msg" style="font-size:12px; align-self:center;"></span>
  </div>

  <h2>Order Book — <span id="book-pair">BRN/USDC</span></h2>
  <div class="book-cols">
    <div class="book-side bids">
      <h3>Bids (compra)</h3>
      <table>
        <thead><tr><th>Preço</th><th>Qtd</th><th>Dono</th></tr></thead>
        <tbody id="bids-rows"><tr><td colspan="3" class="empty">—</td></tr></tbody>
      </table>
    </div>
    <div class="book-side asks">
      <h3>Asks (venda)</h3>
      <table>
        <thead><tr><th>Preço</th><th>Qtd</th><th>Dono</th></tr></thead>
        <tbody id="asks-rows"><tr><td colspan="3" class="empty">—</td></tr></tbody>
      </table>
    </div>
  </div>
  <div style="text-align:center;color:#8b949e;font-size:12px;margin-top:6px;">
    best bid <span class="price" id="bb">—</span> ·
    best ask <span class="price" id="ba">—</span> ·
    spread <span id="spread">—</span> ·
    mid <span class="price" id="mid">—</span>
  </div>

  <h2>Meus trades</h2>
  <table>
    <thead><tr><th>Data</th><th>Papel</th><th>Par</th><th>Preço</th>
      <th>Qtd</th><th>Custo</th></tr></thead>
    <tbody id="trades-rows"><tr><td colspan="6" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Minhas ordens abertas</h2>
  <table>
    <thead><tr><th>ID</th><th>Side</th><th>Preço</th><th>Restante</th>
      <th>Status</th></tr></thead>
    <tbody id="orders-rows"><tr><td colspan="5" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Meus commits (MEV)</h2>
  <table>
    <thead><tr><th>Hash</th><th>Side</th><th>Pronto em</th>
      <th>Expira em</th><th>Status</th></tr></thead>
    <tbody id="commits-rows"><tr><td colspan="5" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Ativos registrados</h2>
  <table>
    <thead><tr><th>ID</th><th>Nome</th><th>Tipo</th><th>Emissor</th>
      <th>Supply</th><th>Max</th><th>Restrito</th></tr></thead>
    <tbody id="assets-rows"><tr><td colspan="7" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Últimas transações</h2>
  <table>
    <thead><tr><th>Data</th><th>Tipo</th><th>Ativo</th><th>Detalhes</th>
      <th>Valor</th><th>Status</th></tr></thead>
    <tbody id="tx-rows"><tr><td colspan="6" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Compliance (KYC)</h2>
  <table>
    <thead><tr><th>Endereço</th><th>Status</th><th>Nível</th>
      <th>Jurisdição</th><th>Verificado por</th><th>Expira</th></tr></thead>
    <tbody id="kyc-rows"><tr><td colspan="6" class="empty">Carregando…</td></tr></tbody>
  </table>

<script>
const CSRF = "{{ csrf_token }}";
const MY_ADDR = localStorage.getItem("brn_addr") || "";

function shortAddr(a){ return a && a.length>22 ? a.slice(0,10)+'…'+a.slice(-8) : (a||'—'); }
function esc(s){ return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmt(n, d=6){ return (n===null||n===undefined) ? '—' : Number(n).toFixed(d); }

// -------- summary --------
async function loadSummary(){
  try {
    const r = await fetch('/api/summary');
    if (r.status === 401) { document.body.innerHTML = '<p style="padding:40px">Login necessário.</p>'; return; }
    const d = await r.json();
    document.getElementById('stat-blocks').textContent  = d.stats.blocks;
    document.getElementById('stat-assets').textContent  = d.stats.assets;
    document.getElementById('stat-kyc').textContent     = d.stats.kyc_approved;
    document.getElementById('stat-orders').textContent  = d.stats.orders_open;
    document.getElementById('stat-trades').textContent  = d.stats.trades;
    document.getElementById('stat-slashed').textContent = d.stats.slashed;
    document.getElementById('stat-final').textContent   = '#' + d.stats.finalized;
    document.getElementById('last-update').textContent  = 'atualizado ' + d.stats.now;

    document.getElementById('assets-rows').innerHTML = d.assets.length ? d.assets.map(a => `
      <tr>
        <td class="addr">${esc(a.asset_id)}</td>
        <td>${esc(a.name)}</td>
        <td>${esc(a.asset_type)}</td>
        <td class="addr" title="${esc(a.issuer)}">${shortAddr(a.issuer)}</td>
        <td class="amount">${fmt(a.total_supply, 4)}</td>
        <td>${a.max_supply || '—'}</td>
        <td>${a.transfer_restricted ? '🔒' : '🟢'}</td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">Nenhum ativo.</td></tr>';

    document.getElementById('tx-rows').innerHTML = d.transfers.length ? d.transfers.map(t => {
      let detail = '';
      if (t.type === 'transfer') detail = `${shortAddr(t.from)} → ${shortAddr(t.to)}`;
      else if (t.type === 'order_place') detail = `${esc(t.side).toUpperCase()} @ ${fmt(t.price)} ${esc(t.quote)}`;
      else if (t.type === 'order_commit') detail = `${esc(t.side).toUpperCase()} commit`;
      else if (t.type === 'order_reveal') detail = `${esc(t.side).toUpperCase()} reveal @ ${fmt(t.price)} ${esc(t.quote)}`;
      else detail = `${shortAddr(t.from)} → ${shortAddr(t.to)}`;
      return `<tr>
        <td>${esc(t.datetime)}</td>
        <td>${esc(t.type)}</td>
        <td class="addr">${esc(t.asset_id)}</td>
        <td>${detail}</td>
        <td class="amount">${fmt(t.amount, 4)}</td>
        <td><span class="badge ${esc(t.status)}">${esc(t.status)}</span></td>
      </tr>`;
    }).join('') : '<tr><td colspan="6" class="empty">Nenhuma transação.</td></tr>';

    document.getElementById('kyc-rows').innerHTML = d.kyc.length ? d.kyc.map(k => `
      <tr>
        <td class="addr" title="${esc(k.address)}">${shortAddr(k.address)}</td>
        <td><span class="badge ${esc(k.status)}">${esc(k.status)}</span></td>
        <td>${esc(k.level)}</td>
        <td>${esc(k.jurisdiction) || '—'}</td>
        <td class="addr">${shortAddr(k.verified_by)}</td>
        <td>${k.expires_at ? new Date(k.expires_at*1000).toLocaleDateString() : '—'}</td>
      </tr>`).join('') : '<tr><td colspan="6" class="empty">Nenhum KYC.</td></tr>';
  } catch(e){ console.error(e); }
}

// -------- order book --------
async function loadBook(){
  try {
    const r = await fetch('/api/orderbook?depth=8');
    if (!r.ok) return;
    const b = await r.json();
    document.getElementById('book-pair').textContent = b.pair || '—';
    document.getElementById('bb').textContent  = b.best_bid ? fmt(b.best_bid) : '—';
    document.getElementById('ba').textContent  = b.best_ask ? fmt(b.best_ask) : '—';
    document.getElementById('spread').textContent = b.spread ? fmt(b.spread) : '—';
    document.getElementById('mid').textContent = b.mid ? fmt(b.mid) : '—';

    document.getElementById('bids-rows').innerHTML = b.bids.length ? b.bids.map(x => `
      <tr><td class="price">${fmt(x.price)}</td>
          <td>${fmt(x.amount, 4)}</td>
          <td class="addr">${esc(x.owner)}</td></tr>`).join('')
      : '<tr><td colspan="3" class="empty">—</td></tr>';
    document.getElementById('asks-rows').innerHTML = b.asks.length ? b.asks.slice().reverse().map(x => `
      <tr><td class="price">${fmt(x.price)}</td>
          <td>${fmt(x.amount, 4)}</td>
          <td class="addr">${esc(x.owner)}</td></tr>`).join('')
      : '<tr><td colspan="3" class="empty">—</td></tr>';
  } catch(e){ console.error(e); }
}

// -------- trades / orders / commits (por endereço) --------
async function loadMine(){
  if (!MY_ADDR) {
    document.getElementById('trades-rows').innerHTML = '<tr><td colspan="6" class="empty">Defina o endereço (localStorage.brn_addr).</td></tr>';
    document.getElementById('orders-rows').innerHTML = '<tr><td colspan="5" class="empty">—</td></tr>';
    document.getElementById('commits-rows').innerHTML = '<tr><td colspan="5" class="empty">—</td></tr>';
    return;
  }
  try {
    const [trades, orders, commits] = await Promise.all([
      fetch('/api/trades/' + MY_ADDR).then(r => r.json()).catch(()=>[]),
      fetch('/api/orders/' + MY_ADDR).then(r => r.json()).catch(()=>[]),
      fetch('/api/commits/' + MY_ADDR).then(r => r.json()).catch(()=>[]),
    ]);
    document.getElementById('trades-rows').innerHTML = (trades && trades.length) ? trades.map(t => `
      <tr><td>${esc(new Date(t.timestamp*1000).toLocaleString())}</td>
          <td>${esc(t.role)}</td>
          <td>${esc(t.pair)}</td>
          <td class="price">${fmt(t.price)}</td>
          <td>${fmt(t.amount, 4)}</td>
          <td class="amount">${fmt(t.cost, 4)} ${esc(t.quote)}</td></tr>`).join('')
      : '<tr><td colspan="6" class="empty">Nenhum trade.</td></tr>';

    document.getElementById('orders-rows').innerHTML = (orders && orders.length) ? orders.map(o => `
      <tr><td class="addr">${esc(o.order_id.slice(0,16))}…</td>
          <td>${esc(o.side)}</td>
          <td class="price">${fmt(o.price)}</td>
          <td>${fmt(o.remaining, 4)}</td>
          <td><span class="badge ${esc(o.status)}">${esc(o.status)}</span></td></tr>`).join('')
      : '<tr><td colspan="5" class="empty">Nenhuma ordem.</td></tr>';

    document.getElementById('commits-rows').innerHTML = (commits && commits.length) ? commits.map(c => `
      <tr><td class="addr">${esc(c.commit_hash)}</td>
          <td>${esc(c.side)}</td>
          <td>${c.blocks_until_ready}b</td>
          <td>${c.blocks_until_expire}b</td>
          <td><span class="badge ${esc(c.status)}">${esc(c.status)}</span></td></tr>`).join('')
      : '<tr><td colspan="5" class="empty">Nenhum commit.</td></tr>';
  } catch(e){ console.error(e); }
}

// -------- faucet --------
async function requestFaucet(){
  const addr = document.getElementById('faucet-addr').value.trim();
  const msg = document.getElementById('faucet-msg');
  msg.textContent = '…';
  try {
    const r = await fetch('/api/faucet', {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'X-CSRF-Token': CSRF},
      body: JSON.stringify({address: addr})
    });
    const j = await r.json();
    msg.textContent = j.msg || (j.ok?'ok':'erro');
    msg.style.color = j.ok ? '#3fb950' : '#f85149';
  } catch(e){ msg.textContent = 'erro de rede'; }
}

// -------- public URL --------
async function loadHealth(){
  try {
    const h = await fetch('/api/health').then(r => r.json());
    if (h.public_url) document.getElementById('public-url').textContent = h.public_url;
  } catch(e){}
}

loadSummary(); loadBook(); loadMine(); loadHealth();
setInterval(loadSummary, 3000);
setInterval(loadBook, 3000);
setInterval(loadMine, 5000);
</script>
</body></html>
"""


# =====================================================================
# Rotas — dashboard
# =====================================================================
@app.route("/")
@require_auth
def dashboard():
    return render_template_string(DASHBOARD_HTML, csrf_token=CSRF_TOKEN)


# =====================================================================
# Rotas — summary (com cache)
# =====================================================================
@app.route("/api/summary")
@require_auth
@limiter.limit("120 per minute")
def api_summary():
    if _blockchain_ref is None:
        return jsonify(error="blockchain não inicializada"), 503
    bc = _blockchain_ref

    now = time.time()
    if _summary_cache["data"] and (now - _summary_cache["ts"]) < CACHE_TTL_SUMMARY:
        return jsonify(_summary_cache["data"])

    try:
        with bc.lock:
            # ---- assets ----
            assets = []
            for a in bc.registry.assets.values():
                d = a.to_dict()
                d["total_supply"] = bc.state.total_supply.get(a.asset_id, 0.0)
                assets.append(d)

            # ---- transfers (chain + mempool) ----
            transfers = []
            for blk in bc.chain:
                for tx in blk.transactions:
                    transfers.append(_tx_dto(tx, block_index=blk.index,
                                              status="confirmada"))
            for tx in bc.pending:
                transfers.append(_tx_dto(tx, block_index=None,
                                          status="pendente"))
            transfers.sort(key=lambda t: t["timestamp"], reverse=True)
            transfers = transfers[:100]

            # ---- kyc ----
            kyc_list = [r.to_dict() for r in bc.registry.compliance.values()]
            kyc_approved = sum(1 for r in kyc_list if r["status"] == "approved")

            # ---- CLOB stats ----
            orders_open = sum(1 for o in bc.state.orders.values()
                              if o["status"] in ("open", "partial"))
            trades_count = len(bc.state.trades)

            stats = {
                "blocks": len(bc.chain),
                "assets": len(bc.registry.assets),
                "kyc_approved": kyc_approved,
                "transfers": len(transfers),
                "orders_open": orders_open,
                "trades": trades_count,
                "slashed": len(bc.slashed),
                "finalized": bc.finality.finalized_height,
                "now": datetime.now().strftime("%H:%M:%S"),
            }

        data = {"assets": assets, "transfers": transfers,
                "kyc": kyc_list, "stats": stats}
        _summary_cache["data"] = data
        _summary_cache["ts"] = now
        return jsonify(data)
    except Exception as e:
        print(f"[web] erro em summary: {e}")
        return jsonify(error=str(e)), 500


# =====================================================================
# Rotas — CLOB
# =====================================================================
@app.route("/api/orderbook")
@require_auth
@limiter.limit("120 per minute")
def api_orderbook():
    if _blockchain_ref is None:
        return jsonify(error="blockchain não inicializada"), 503
    base  = request.args.get("base", DEFAULT_BASE)
    quote = request.args.get("quote", DEFAULT_QUOTE)
    depth = int(request.args.get("depth", 15))
    try:
        with _blockchain_ref.lock:
            book = _blockchain_ref.order_book(base, quote, depth=depth)
        return jsonify(book)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/orders/<address>")
@require_auth
@limiter.limit("60 per minute")
def api_orders(address):
    if _blockchain_ref is None:
        return jsonify([])
    try:
        with _blockchain_ref.lock:
            return jsonify(_blockchain_ref.my_orders(address))
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/trades/<address>")
@require_auth
@limiter.limit("60 per minute")
def api_trades(address):
    if _blockchain_ref is None:
        return jsonify([])
    limit = int(request.args.get("limit", 50))
    try:
        with _blockchain_ref.lock:
            return jsonify(_blockchain_ref.my_trades(address, limit=limit))
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/commits/<address>")
@require_auth
@limiter.limit("60 per minute")
def api_commits(address):
    if _blockchain_ref is None:
        return jsonify([])
    try:
        with _blockchain_ref.lock:
            return jsonify(_blockchain_ref.my_commits(address))
    except Exception as e:
        return jsonify(error=str(e)), 500


# =====================================================================
# Rotas — RWA
# =====================================================================
@app.route("/api/portfolio/<address>")
@require_auth
@limiter.limit("60 per minute")
def api_portfolio(address):
    if _blockchain_ref is None:
        return jsonify(error="blockchain não inicializada"), 503
    try:
        with _blockchain_ref.lock:
            return jsonify(address=address,
                           portfolio=_blockchain_ref.portfolio(address))
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/assets")
@require_auth
def api_assets():
    if _blockchain_ref is None:
        return jsonify([])
    try:
        with _blockchain_ref.lock:
            out = []
            for a in _blockchain_ref.registry.assets.values():
                d = a.to_dict()
                d["total_supply"] = _blockchain_ref.state.total_supply.get(
                    a.asset_id, 0.0)
                out.append(d)
            return jsonify(out)
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/api/tx/<tx_hash>")
@require_auth
@limiter.limit("60 per minute")
def api_tx(tx_hash):
    if _blockchain_ref is None:
        return jsonify(error="blockchain não inicializada"), 503
    bc = _blockchain_ref
    try:
        with bc.lock:
            for blk in bc.chain:
                for tx in blk.transactions:
                    if Transaction.hash(tx) == tx_hash:
                        return jsonify(_tx_dto(tx, block_index=blk.index))
            for tx in bc.pending:
                if Transaction.hash(tx) == tx_hash:
                    return jsonify(_tx_dto(tx, block_index=None,
                                            status="pendente"))
        return jsonify(error="tx não encontrada"), 404
    except Exception as e:
        return jsonify(error=str(e)), 500


# =====================================================================
# Rotas — KYC
# =====================================================================
@app.route("/api/kyc", methods=["POST"])
@require_auth
@require_csrf
@limiter.limit("30 per minute")
def api_kyc_register():
    bc = _blockchain_ref
    if bc is None:
        return jsonify(ok=False, msg="blockchain não inicializada"), 503
    d = request.get_json(silent=True) or {}
    required = ["asset_id", "address", "agent_address",
                "agent_private_key", "agent_public_key", "nonce"]
    for r in required:
        if r not in d:
            return jsonify(ok=False, msg=f"faltando '{r}'"), 400
    md = {
        "status": d.get("status", "approved"),
        "level": d.get("level", "basic"),
        "jurisdiction": d.get("jurisdiction", ""),
        "expires_at": float(d.get("expires_at", 0)),
        "restrictions": d.get("restrictions", []),
        "extra": d.get("extra", {}),
    }
    try:
        tx = Transaction.build(
            tx_type="kyc_register", asset_id=d["asset_id"],
            sender_address=d["agent_address"],
            receiver_address=d["address"],
            amount=0, nonce=int(d["nonce"]),
            private_key_hex=d["agent_private_key"],
            public_key_hex=d["agent_public_key"],
            metadata=md)
        r = bc.add_transaction(tx)
        return jsonify(r), (200 if r.get("ok") else 400)
    except Exception as e:
        return jsonify(ok=False, msg=str(e)), 500


# =====================================================================
# Rotas — Faucet
# =====================================================================
@app.route("/api/faucet", methods=["POST"])
@require_auth
@require_csrf
@limiter.limit(f"{FAUCET_MAX_PER_IP} per hour")
def api_faucet():
    if _faucet_handler is None:
        return jsonify({"ok": False, "msg": "Faucet não inicializado."}), 503
    data = request.get_json(silent=True) or {}
    addr = (data.get("address") or "").strip()
    if not addr.startswith("brn1") or len(addr) < 20:
        return jsonify({"ok": False, "msg": "Endereço inválido."}), 400
    try:
        r = _faucet_handler(addr)
        return jsonify(r), (200 if r.get("ok") else 429)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


# =====================================================================
# Rotas — Diagnóstico
# =====================================================================
@app.route("/api/slashing")
@require_auth
def api_slashing():
    return jsonify(_blockchain_ref.slashing_report() if _blockchain_ref else [])


@app.route("/api/finality")
@require_auth
def api_finality():
    return jsonify(_blockchain_ref.finality_report() if _blockchain_ref else {})


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "ngrok": bool(NGROK_AUTHTOKEN),
        "public_url": _web_public_url,
        "csrf_token": CSRF_TOKEN,
    })


# =====================================================================
# Bootstrap
# =====================================================================
def _run_flask():
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)


def start_web_server(blockchain, peer_manager=None, faucet_handler=None):
    """
    Sobe o servidor web (thread daemon) e opcionalmente expõe via NGROK.

    Args:
        blockchain: instância de Blockchain
        peer_manager: PeerManager (opcional — se já tem NGROK TCP,
                      abre o HTTP em paralelo)
        faucet_handler: função `fn(address) -> dict` para /api/faucet
    """
    set_blockchain(blockchain)
    if faucet_handler:
        set_faucet_handler(faucet_handler)

    threading.Thread(target=_run_flask, daemon=True, name="web").start()
    print(f"[web] painel RWA em http://{WEB_HOST}:{WEB_PORT} (user: {WEB_USER})")

    if not USE_NGROK:
        return None
    if not NGROK_AUTHTOKEN:
        print("[ngrok] NGROK_AUTHTOKEN ausente — pulando túnel HTTP.")
        return None

    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = NGROK_AUTHTOKEN
        # reuse existing session if peer_manager already opened one
        tunnel = ngrok.connect(WEB_PORT, "http")
        url = tunnel.public_url
        set_public_url(url)
        print(f"[web/ngrok] público em: {url}")
        return url
    except ImportError:
        print("[ngrok] pyngrok não instalado. pip install pyngrok")
    except Exception as e:
        print(f"[web/ngrok] erro: {e}")
    return None


# =====================================================================
# Standalone (teste rápido)
# =====================================================================
if __name__ == "__main__":
    bc = Blockchain()
    start_web_server(bc)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[web] encerrando...")