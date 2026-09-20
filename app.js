// ============================================================
// APP.JS — Carteira BRN P2P
// Compatível com EscrowFactory JÁ DEPLOYADO (sem alterar .sol)
// - Mural lê direto da blockchain (não precisa conectar carteira)
// - Estado "Consultando..." + contador de ordens ativas
// - Approve automático de BRN (Factory) e USDC (Escrow)
// - Fallback de múltiplos RPCs + timeouts + leitura paralela
//
// IMPORTANTE: o contrato NÃO está verificado no PolygonScan.
// Os nomes das funções foram reconstruídos a partir do bytecode
// (evmole). Por isso usamos os SELECTORS (4 bytes) diretamente
// e decodificamos as respostas manualmente — 100% confiável.
// ============================================================

// --- CONFIGURACOES (POLYGON MAINNET) ---
const ESCROW_FACTORY_ADDRESS = "0x5C305aCFF5cDFAee90276c2acEA4Aa841f7062d8";
const TOKEN_BRN_ADDRESS      = "0xdBc1c747B1D4c27113F65A4620b8fEaC74e2A210";
const TOKEN_USDC_ADDRESS     = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174";

const BRN_DECIMALS   = 18;
const USDC_DECIMALS  = 6;
const POLYGON_CHAIN_ID = 137;

// RPCs públicos com fallback
const RPCS = [
  "https://polygon-bor-rpc.publicnode.com",
  "https://polygon.drpc.org",
  "https://polygon-rpc.com",
  "https://rpc.ankr.com/polygon",
];
const RPC_TIMEOUT_MS = 8000;

// --- SELECTORS (reconstruídos do bytecode via evmole) ---
// FACTORY
const SEL_FACTORY = {
  criarOrdem:   "ceff4da6", // criarOrdem(address,address,uint256,uint256) -> address
  ordensDe:     "0dc80995", // ordensDe(address) -> address[]
  ordem:        "72c453b8", // ordem(uint256) -> address
  totalOrdens:  "8275d6fa", // totalOrdens() -> uint256
  todasOrdens:  "e9b1e327", // todasOrdens() -> address[]
};
// ESCROW
const SEL_ESCROW = {
  criador:        "041c797c",
  tokenDesejado:  "0cd59b77",
  executado:      "2a3a5716",
  obterDados:     "32c9e06c", // -> (address,address,address,uint256,uint256,bool,bool)
  valorDesejado:  "651cc708",
  cancelado:      "7766a742",
  tokenOferecido: "8a337bdc",
  cancelar:       "8ffb1ccf",
  executar:       "b2d44d08",
  factory:        "c45a0155",
  valorOferecido: "f6c467b7",
};
// ERC-20 (padrão, nomes conhecidos)
const SEL_ERC20 = {
  balanceOf: "70a08231",
  allowance: "dd62ed3e",
  approve:   "095ea7b3",
  decimals:  "313ce567",
  symbol:    "95d89b41",
};

// --- ESTADO GLOBAL ---
let provider = null;
let signer = null;
let userAddress = null;
let currentRpc = null;
let ordersCache = [];

// ============================================================
// UTILITÁRIOS
// ============================================================
function $(id) { return document.getElementById(id); }
function short(a) { return a ? a.slice(0, 6) + "…" + a.slice(-4) : "—"; }

function fmt(value, decimals, maxFrac = 4) {
  try {
    const s = ethers.utils.formatUnits(value, decimals);
    const n = Number(s);
    if (!isFinite(n)) return s;
    return n.toLocaleString("pt-BR", { maximumFractionDigits: maxFrac });
  } catch (e) { return String(value); }
}

function toast(msg, type = "info", ms = 5000) {
  const box = $("toasts");
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.innerHTML = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, ms);
}

function setNet(state, text) {
  $("netDot").className = "dot " + (state === "ok" ? "" : state);
  $("netText").textContent = text;
}

// --- helpers de codificação/decodificação manual (ABI) ---
function pad32(hexNo0x) { return hexNo0x.padStart(64, "0"); }
function encAddress(addr) { return pad32(addr.toLowerCase().replace(/^0x/, "")); }
function encUint(n) { return pad32(BigInt(n).toString(16)); }

function decAddress(word) { return "0x" + word.slice(24); }
function decUint(word) { return BigInt("0x" + word); }
function decBool(word) { return BigInt("0x" + word) !== 0n; }

function splitWords(hex) {
  const b = hex.replace(/^0x/, "");
  const out = [];
  for (let i = 0; i < b.length; i += 64) out.push(b.slice(i, i + 64));
  return out;
}

// decodifica um retorno address[] (offset + length + itens)
function decAddressArray(hex) {
  const w = splitWords(hex);
  const off = Number(BigInt("0x" + w[0]));
  const len = Number(BigInt("0x" + w[off / 32]));
  const arr = [];
  for (let i = 0; i < len; i++) arr.push(decAddress(w[off / 32 + 1 + i]));
  return arr;
}

// ============================================================
// PROVIDER COM FALLBACK DE RPCs
// ============================================================
async function withTimeout(promise, ms) {
  let t;
  const timeout = new Promise((_, rej) => { t = setTimeout(() => rej(new Error("timeout")), ms); });
  try { return await Promise.race([promise, timeout]); }
  finally { clearTimeout(t); }
}

async function pickProvider() {
  for (const url of RPCS) {
    try {
      const p = new ethers.providers.JsonRpcProvider(url);
      const net = await withTimeout(p.getNetwork(), RPC_TIMEOUT_MS);
      if (net && Number(net.chainId) === POLYGON_CHAIN_ID) { currentRpc = url; return p; }
    } catch (e) { /* tenta o próximo */ }
  }
  throw new Error("Nenhum RPC respondeu");
}

async function getProvider() {
  if (provider) return provider;
  provider = await pickProvider();
  return provider;
}

// chamada eth_call crua
async function rawCall(to, data) {
  const p = await getProvider();
  return await withTimeout(p.call({ to, data }), RPC_TIMEOUT_MS);
}

// ============================================================
// LEITURA DO MURAL (sem carteira)
// ============================================================
async function carregarMural() {
  const box = $("orders");
  box.innerHTML = `<div class="state"><div class="spinner"></div>Consultando a blockchain…</div>`;
  $("counter").textContent = "⏳ Consultando…";
  setNet("load", "Consultando…");

  try {
    // 1) lista de escrows
    let addrs = [];
    try {
      const r = await rawCall(ESCROW_FACTORY_ADDRESS, "0x" + SEL_FACTORY.todasOrdens);
      addrs = decAddressArray(r);
    } catch (e) {
      const rTotal = await rawCall(ESCROW_FACTORY_ADDRESS, "0x" + SEL_FACTORY.totalOrdens);
      const n = Number(decUint(splitWords(rTotal)[0]));
      const rs = await Promise.all(Array.from({ length: n }, (_, i) =>
        rawCall(ESCROW_FACTORY_ADDRESS, "0x" + SEL_FACTORY.ordem + encUint(i))));
      addrs = rs.map(r => decAddress(splitWords(r)[0]));
    }

    // 2) leitura PARALELA dos detalhes
    const detalhes = await Promise.all(addrs.map(async (addr) => {
      try {
        const r = await rawCall(addr, "0x" + SEL_ESCROW.obterDados);
        const w = splitWords(r);
        return {
          endereco: addr,
          criador: decAddress(w[0]),
          tokenOferecido: decAddress(w[1]),
          tokenDesejado: decAddress(w[2]),
          valorOferecido: decUint(w[3]),
          valorDesejado: decUint(w[4]),
          executado: decBool(w[5]),
          cancelado: decBool(w[6]),
        };
      } catch (e) { return { endereco: addr, erro: true }; }
    }));

    ordersCache = detalhes;
    renderMural(detalhes);

    const ativas = detalhes.filter(o => !o.erro && !o.executado && !o.cancelado).length;
    $("counter").textContent = `📋 ${ativas} ordem(ns) ativa(s) · ${detalhes.length} no total`;
    setNet("ok", "Polygon · online");
  } catch (e) {
    console.error(e);
    box.innerHTML = `<div class="empty"><div class="big">⚠️</div>Não foi possível consultar a blockchain.<br><small>${e.message}</small></div>`;
    $("counter").textContent = "❌ Falha na consulta";
    setNet("off", "Offline");
  }
}

function renderMural(orders) {
  const box = $("orders");
  if (!orders.length) {
    box.innerHTML = `<div class="empty"><div class="big">📭</div>Nenhuma ordem no mural ainda.<br>Crie a primeira ordem de venda acima.</div>`;
    return;
  }
  const rank = o => o.erro ? 3 : o.cancelado ? 2 : o.executado ? 1 : 0;
  const sorted = [...orders].sort((a, b) => rank(a) - rank(b));

  box.innerHTML = sorted.map((o, i) => {
    if (o.erro) {
      return `<div class="order"><div class="order-id">${o.endereco}</div>
        <div class="state" style="padding:10px">⚠️ Não foi possível ler esta ordem.</div></div>`;
    }
    const isBRN = o.tokenOferecido.toLowerCase() === TOKEN_BRN_ADDRESS.toLowerCase();
    const isUSDC = o.tokenDesejado.toLowerCase() === TOKEN_USDC_ADDRESS.toLowerCase();
    const decOf = isBRN ? BRN_DECIMALS : 18;
    const decDe = isUSDC ? USDC_DECIMALS : 18;
    const symOf = isBRN ? "BRN" : short(o.tokenOferecido);
    const symDe = isUSDC ? "USDC" : short(o.tokenDesejado);

    const status = o.cancelado ? "cancelled" : o.executado ? "done" : "active";
    const tagTxt = o.cancelado ? "Cancelada" : o.executado ? "Executada" : "Ativa";

    const podeExecutar = !o.executado && !o.cancelado && userAddress &&
      userAddress.toLowerCase() !== o.criador.toLowerCase();
    const podeCancelar = !o.executado && !o.cancelado && userAddress &&
      userAddress.toLowerCase() === o.criador.toLowerCase();

    return `
      <div class="order ${status}">
        <div class="order-top">
          <span class="order-id">#${i + 1} · ${short(o.endereco)}</span>
          <span class="tag ${status}">${tagTxt}</span>
        </div>
        <div class="swap">
          <div class="side">
            <div class="lbl">Oferece</div>
            <div class="amt ${isBRN ? "brn" : ""}">${fmt(o.valorOferecido, decOf)} ${symOf}</div>
          </div>
          <div class="arrow">⇄</div>
          <div class="side">
            <div class="lbl">Pede</div>
            <div class="amt ${isUSDC ? "usdc" : ""}">${fmt(o.valorDesejado, decDe)} ${symDe}</div>
          </div>
        </div>
        <div class="order-meta">
          <span>Criador: <b>${short(o.criador)}</b></span>
          <span>Escrow: <b>${short(o.endereco)}</b></span>
        </div>
        <div class="order-actions">
          ${podeExecutar ? `<button class="btn-ok btn-sm" onclick="executarOrdem('${o.endereco}')">⚡ Executar (pagar ${fmt(o.valorDesejado, decDe)} ${symDe})</button>` : ""}
          ${podeCancelar ? `<button class="btn-err btn-sm" onclick="cancelarOrdem('${o.endereco}')">✖ Cancelar</button>` : ""}
          ${(!podeExecutar && !podeCancelar && !o.executado && !o.cancelado) ? `<span class="order-id">Conecte a carteira para interagir</span>` : ""}
        </div>
      </div>`;
  }).join("");
}

// ============================================================
// CARTEIRA (MetaMask)
// ============================================================
async function conectarCarteira() {
  if (!window.ethereum) { toast("MetaMask não encontrada. Instale a extensão.", "err"); return; }
  try {
    const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
    userAddress = accounts[0];

    const chainId = await window.ethereum.request({ method: "eth_chainId" });
    if (parseInt(chainId, 16) !== POLYGON_CHAIN_ID) {
      try {
        await window.ethereum.request({
          method: "wallet_switchEthereumChain",
          params: [{ chainId: "0x" + POLYGON_CHAIN_ID.toString(16) }],
        });
      } catch (e) { toast("Troque para a rede Polygon na MetaMask.", "warn"); }
    }

    signer = new ethers.providers.Web3Provider(window.ethereum).getSigner();

    $("walletInfo").style.display = "block";
    $("addr").textContent = userAddress;
    $("btnConnect").textContent = "🔌 Conectado";
    $("btnConnect").disabled = true;
    $("btnCreate").disabled = false;
    $("btnApproveBRN").disabled = false;

    await carregarSaldos();
    renderMural(ordersCache);
    toast("Carteira conectada: " + short(userAddress), "ok");

    window.ethereum.on("accountsChanged", () => location.reload());
    window.ethereum.on("chainChanged", () => location.reload());
  } catch (e) {
    console.error(e);
    toast("Falha ao conectar: " + e.message, "err");
  }
}

function desconectar() {
  userAddress = null; signer = null;
  $("walletInfo").style.display = "none";
  $("btnConnect").textContent = "🔌 Conectar carteira";
  $("btnConnect").disabled = false;
  $("btnCreate").disabled = true;
  $("btnApproveBRN").disabled = true;
  renderMural(ordersCache);
  toast("Carteira desconectada.", "info");
}

async function lerSaldo(token, addr) {
  const r = await rawCall(token, "0x" + SEL_ERC20.balanceOf + encAddress(addr));
  return decUint(splitWords(r)[0]);
}
async function lerAllowance(token, owner, spender) {
  const r = await rawCall(token, "0x" + SEL_ERC20.allowance + encAddress(owner) + encAddress(spender));
  return decUint(splitWords(r)[0]);
}

async function carregarSaldos() {
  try {
    const [b, u] = await Promise.all([
      lerSaldo(TOKEN_BRN_ADDRESS, userAddress),
      lerSaldo(TOKEN_USDC_ADDRESS, userAddress),
    ]);
    $("balBRN").textContent = fmt(b, BRN_DECIMALS);
    $("balUSDC").textContent = fmt(u, USDC_DECIMALS);
  } catch (e) {
    console.error(e);
    $("balBRN").textContent = "—";
    $("balUSDC").textContent = "—";
  }
}

// ============================================================
// CRIAR ORDEM (approve BRN + criarOrdem)
// ============================================================
async function aprovarBRN() {
  const val = $("inBRN").value;
  if (!val || Number(val) <= 0) { toast("Informe quanto BRN você oferece.", "warn"); return; }
  try {
    const amount = ethers.utils.parseUnits(val, BRN_DECIMALS);
    const data = "0x" + SEL_ERC20.approve + encAddress(ESCROW_FACTORY_ADDRESS) + encUint(amount);
    toast("Aprovando BRN… confirme na MetaMask.", "info");
    const tx = await signer.sendTransaction({ to: TOKEN_BRN_ADDRESS, data });
    await tx.wait();
    toast("✅ BRN aprovado!", "ok");
  } catch (e) {
    console.error(e);
    toast("Falha no approve: " + (e.data?.message || e.message), "err");
  }
}

async function criarOrdem() {
  const brnVal = $("inBRN").value;
  const usdcVal = $("inUSDC").value;
  if (!brnVal || Number(brnVal) <= 0) { toast("Informe quanto BRN você oferece.", "warn"); return; }
  if (!usdcVal || Number(usdcVal) <= 0) { toast("Informe quanto USDC você pede.", "warn"); return; }
  try {
    const vOf = ethers.utils.parseUnits(brnVal, BRN_DECIMALS);
    const vDe = ethers.utils.parseUnits(usdcVal, USDC_DECIMALS);

    const allow = await lerAllowance(TOKEN_BRN_ADDRESS, userAddress, ESCROW_FACTORY_ADDRESS);
    if (allow < vOf) { toast("Aprove o BRN primeiro (botão 'Aprovar BRN').", "warn"); return; }

    const data = "0x" + SEL_FACTORY.criarOrdem +
      encAddress(TOKEN_BRN_ADDRESS) + encAddress(TOKEN_USDC_ADDRESS) +
      encUint(vOf) + encUint(vDe);

    toast("Criando ordem… confirme na MetaMask.", "info");
    const tx = await signer.sendTransaction({ to: ESCROW_FACTORY_ADDRESS, data });
    toast("Transação enviada: " + short(tx.hash) + " — aguardando…", "info");
    await tx.wait();
    toast("✅ Ordem criada on-chain!", "ok");
    $("inBRN").value = ""; $("inUSDC").value = "";
    await carregarSaldos();
    await carregarMural();
  } catch (e) {
    console.error(e);
    toast("Falha ao criar ordem: " + (e.data?.message || e.message), "err");
  }
}

// ============================================================
// EXECUTAR ORDEM (approve USDC no escrow + executar)
// ============================================================
async function executarOrdem(escrowAddr) {
  try {
    const o = ordersCache.find(x => x.endereco.toLowerCase() === escrowAddr.toLowerCase());
    if (!o) { toast("Ordem não encontrada.", "err"); return; }

    const allow = await lerAllowance(TOKEN_USDC_ADDRESS, userAddress, escrowAddr);
    if (allow < o.valorDesejado) {
      toast("Aprovando USDC para o escrow… confirme na MetaMask.", "info");
      const dataA = "0x" + SEL_ERC20.approve + encAddress(escrowAddr) + encUint(o.valorDesejado);
      const txA = await signer.sendTransaction({ to: TOKEN_USDC_ADDRESS, data: dataA });
      await txA.wait();
      toast("✅ USDC aprovado!", "ok");
    }

    toast("Executando ordem… confirme na MetaMask.", "info");
    const tx = await signer.sendTransaction({ to: escrowAddr, data: "0x" + SEL_ESCROW.executar });
    toast("Transação enviada: " + short(tx.hash) + " — aguardando…", "info");
    await tx.wait();
    toast("✅ Ordem executada! Troca concluída.", "ok");
    await carregarSaldos();
    await carregarMural();
  } catch (e) {
    console.error(e);
    toast("Falha ao executar: " + (e.data?.message || e.message), "err");
  }
}

// ============================================================
// CANCELAR ORDEM
// ============================================================
async function cancelarOrdem(escrowAddr) {
  try {
    toast("Cancelando ordem… confirme na MetaMask.", "info");
    const tx = await signer.sendTransaction({ to: escrowAddr, data: "0x" + SEL_ESCROW.cancelar });
    toast("Transação enviada: " + short(tx.hash) + " — aguardando…", "info");
    await tx.wait();
    toast("✅ Ordem cancelada. BRN devolvido.", "ok");
    await carregarSaldos();
    await carregarMural();
  } catch (e) {
    console.error(e);
    toast("Falha ao cancelar: " + (e.data?.message || e.message), "err");
  }
}

// ============================================================
// COTAÇÃO (informativo)
// ============================================================
function atualizarCotacao() {
  const b = Number($("inBRN").value);
  const u = Number($("inUSDC").value);
  const info = $("rateInfo");
  if (b > 0 && u > 0) {
    const preco = u / b;
    info.style.display = "block";
    info.innerHTML = `💱 Preço: <b>1 BRN = ${preco.toLocaleString("pt-BR", { maximumFractionDigits: 6 })} USDC</b>`;
  } else {
    info.style.display = "none";
  }
}

// ============================================================
// INIT
// ============================================================
window.addEventListener("DOMContentLoaded", () => {
  $("btnConnect").addEventListener("click", conectarCarteira);
  $("btnDisconnect").addEventListener("click", desconectar);
  $("btnRefresh").addEventListener("click", carregarMural);
  $("btnApproveBRN").addEventListener("click", aprovarBRN);
  $("btnCreate").addEventListener("click", criarOrdem);
  $("inBRN").addEventListener("input", atualizarCotacao);
  $("inUSDC").addEventListener("input", atualizarCotacao);

  carregarMural();
  setInterval(carregarMural, 60000);
});
