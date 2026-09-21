// wallet_core.js — Funções operacionais da carteira BRN RWA conectadas ao Python.

const $ = id => document.getElementById(id);

function log(msg, color="#58a6ff") {
  const c = $('console-log');
  if (c) {
    c.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    c.style.color = color;
  }
}

window.toggleKeys = function() {
  const sk = $('w-sk');
  const pk = $('w-pk');
  if (sk && pk) {
    const target = sk.type === 'password' ? 'text' : 'password';
    sk.type = target;
    pk.type = target;
  }
}

window.uiGenerateWallet = function() {
  log("Gerando par de chaves criptográficas SECP256k1...");
  window.pywebview.api.generate_wallet().then(res => {
    if (res && res.address) {
      $('w-addr').value = res.address;
      $('w-sk').value = res.spend_secret_key;
      $('w-pk').value = res.public_key;
      log("✓ Nova carteira gerada localmente com sucesso!", "#3fb950");
      window.uiSyncPortfolio();
    } else {
      log("❌ Erro ao gerar chaves.", "#f85149");
    }
  }).catch(err => log("❌ Erro de barramento: " + err, "#f85149"));
}

window.uiSyncPortfolio = function() {
  const addr = $('w-addr').value.trim();
  if (!addr) { log("⚠ Insira ou gere um endereço primeiro.", "#d29922"); return; }
  log(`Buscando balanço do endereço ${addr.slice(0,12)}... no nó local...`);
  
  window.pywebview.api.portfolio(addr).then(res => {
    if (res.erro) {
      $('portfolio-display').textContent = JSON.stringify(res, null, 2);
      $('asset-count').textContent = "Ativos: 0";
      log(`❌ Falha de sincronização: ${res.erro}`, "#f85149");
    } else {
      const count = Object.keys(res).length;
      $('asset-count').textContent = `Ativos: ${count}`;
      $('portfolio-display').textContent = count ? JSON.stringify(res, null, 2) : "Sua carteira está ativa na rede, mas não possui saldos em nenhum token ainda.";
      log("✓ Livro-razão sincronizado e atualizado!", "#3fb950");
    }
  }).catch(err => log("❌ Erro de conexão: " + err, "#f85149"));
}

window.uiMineBlock = function() {
  const addr = $('w-addr').value.trim();
  if (!addr) { log("⚠ Defina o endereço do validador (sua carteira) para receber a recompensa.", "#d29922"); return; }
  log("Acionando os motores criptográficos para minerar novo bloco...");

  window.pywebview.api.mine_block(addr).then(res => {
    if (res.ok) {
      log(`⛏️ ${res.msg} (Transações processadas: ${res.tx_count})`, "#3fb950");
      setTimeout(window.uiSyncPortfolio, 1000);
    } else {
      log(`❌ Mineração rejeitada: ${res.msg}`, "#f85149");
    }
  }).catch(err => log("❌ Falha no motor de consenso: " + err, "#f85149"));
}

window.uiCallFaucet = function() {
  const addr = $('w-addr').value.trim();
  if (!addr) { log("⚠ Forneça o endereço de destino para o Faucet.", "#d29922"); return; }
  log("Invocando faucet de liquidação RWA on-chain...");
  
  window.pywebview.api.faucet(addr).then(res => {
    if (res.ok) {
      log(`✓ Sucesso! Recompensa do faucet injetada na rede: ${res.msg}`, "#3fb950");
      setTimeout(window.uiSyncPortfolio, 1500);
    } else {
      log(`❌ Recusado pelo Faucet: ${res.msg || JSON.stringify(res)}`, "#f85149");
    }
  }).catch(err => log("❌ Erro de Faucet: " + err, "#f85149"));
}

window.uiSaveWallet = function() {
  const name = $('f-name').value.trim();
  const pass = $('f-pass').value;
  const addr = $('w-addr').value.trim();
  const sk = $('w-sk').value.trim();
  const pk = $('w-pk').value.trim();

  if(!name || !pass || !addr) { log("⚠ Preencha os campos de arquivo, senha master e chaves.", "#d29922"); return; }
  log("Cifrando chaves com Argon2id + AES-256-GCM...");

  window.pywebview.api.save_wallet(name, pass, addr, sk, pk).then(res => {
    if(res.status === "sucesso") {
      log(`✓ ${res.message}`, "#3fb950");
    } else {
      log(`❌ Falha ao exportar: ${res.message}`, "#f85149");
    }
  }).catch(err => log("❌ Erro de salvamento: " + err, "#f85149"));
}

window.uiLoadWallet = function() {
  const name = $('f-name').value.trim();
  const pass = $('f-pass').value;

  if(!name || !pass) { log("⚠ Digite o nome do arquivo da carteira e a senha master.", "#d29922"); return; }
  log("Decodificando arquivo criptografado...");

  window.pywebview.api.load_wallet(name, pass).then(res => {
    if(res.status === "sucesso") {
      $('w-addr').value = res.address;
      $('w-sk').value = res.spend_secret_key;
      $('w-pk').value = res.public_key;
      log("✓ Carteira importada com sucesso!", "#3fb950");
      window.uiSyncPortfolio();
    } else {
      log(`❌ Erro de descriptografia: ${res.message}`, "#f85149");
    }
  }).catch(err => log("❌ Erro de carregamento: " + err, "#f85149"));
}

window.uiExecuteTransfer = function() {
  const from = $('w-addr').value.trim();
  const sk = $('w-sk').value.trim();
  const pk = $('w-pk').value.trim();
  const to = $('t-to').value.trim();
  const asset = $('t-asset').value.trim().toUpperCase();
  const amount = $('t-amount').value;

  if(!from || !to || !amount) { log("⚠ Dados de transferência incompletos.", "#d29922"); return; }
  log(`Assinando transação digital de ${amount} ${asset}...`);

  window.pywebview.api.transfer(from, to, asset, amount, sk, pk).then(res => {
    if(res.ok) {
      log(`✓ Transação Aceita: ${res.msg}`, "#3fb950");
      setTimeout(window.uiSyncPortfolio, 1000);
    } else {
      log(`❌ Rejeitada pelo Nó: ${res.msg}`, "#f85149");
    }
  }).catch(err => log("❌ Falha de rede/assinatura: " + err, "#f85149"));
}
