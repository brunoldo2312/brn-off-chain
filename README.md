# 1. dependências
pip install pywebview cryptography argon2-cffi bitcoinlib
# opcional:
pip install miniupnpc

# 2. nó A (webview)
python launcher.py 6001

# 3. nó B (outro terminal / outro PC)
python launcher.py 6002

# 4. dashboard async (opcional)
python explorer.py

# 5. pipeline L2 (opcional)
python main.py
🧪 Como testar (dois nós, mesma máquina)

Terminal A — nó 1

```bash
export NGROK_AUTHTOKEN="SEU_TOKEN_NOVO"
export BRN_P2P_PORT=7777
export BRN_WEB_PORT=5000
export BRN_DB_PATH=node1.db
export BRN_IDENTITY_FILE=node1_identity.json
python node.py
```

Terminal B — nó 2 (aponta para o nó 1)

```bash
export BRN_P2P_PORT=7778
export BRN_WEB_PORT=5001
export BRN_DB_PATH=node2.db
export BRN_IDENTITY_FILE=node2_identity.json
export BRN_SEED_PEERS=127.0.0.1:7777
python node.py
```

O que observar

· No nó 1, o loop de consenso assina o bloco com a chave de node1_identity.json.
· No nó 2, ao receber a mensagem chain, o replace_chain valida cada bloco:
  · hash == calculate_hash()
  · validator == address_from_public_key(validator_public_key)
  · verify_signature(pubkey, {"block_hash", "index"}, signature)

# 1) Instalar dependências
pip install -r requirements.txt

# 2) Testar carteira
python main.py

# 3) Subir nó (em outra máquina ou terminal)
BRN_P2P_PORT=7777 python node.py
✅ O que você precisa fazer
Você ainda não baixou o projeto. Precisa clonar primeiro. Siga exatamente estes comandos, um de cada vez:

1. Volte uma pasta (para não clonar dentro de brn)
No terminal, digite:

cmd
cd ..
Agora você estará em C:\Users\mayra\OneDrive\Imagens\bruno crypto\.

2. Clone o repositório
cmd
git clone https://github.com/brunoldo2312/brnfinal.git
Isso vai criar uma nova pasta chamada brnfinal com todos os arquivos do projeto. Se der erro dizendo que git não é reconhecido, você precisa instalar o Git primeiro: https://git-scm.com/download/win

3. Entre na pasta clonada
cmd
cd brnfinal
4. Veja o que tem dentro
cmd
dir
Agora sim você deve ver os arquivos do projeto: package.json, iniciar.bat, README.md, etc. Só a partir daqui os comandos npm install e iniciar.bat vão funcionar.

5. A partir daqui, siga um destes caminhos:
Se existir iniciar.bat (o arquivo que você mencionou):

cmd
iniciar.bat
Se existir package.json (projeto Node.js):

cmd
npm install
npm start
⚠️ Um alerta importante sobre o OneDrive
Sua pasta está dentro do OneDrive (C:\Users\mayra\OneDrive\...). Isso costuma causar problemas com projetos de programação, porque:

O OneDrive fica sincronizando arquivos enquanto o npm/node tenta ler e escrever

Isso pode causar erros estranhos, lentidão e arquivos "travados"

Recomendação: clone o projeto em uma pasta fora do OneDrive, por exemplo:

cmd
cd C:\
mkdir projetos
cd projetos
git clone https://github.com/brunoldo2312/brnfinal.git
📋 Resumo do que aconteceu
O que você fez	O que deveria ter feito
Colou a URL no terminal	Rodar git clone <URL>
Rodou npm install numa pasta vazia	Clonar o projeto primeiro, depois entrar na pasta
Tentou rodar iniciar.bat que não existe	Primeiro clonar, depois conferir se o arquivo existe
Rode o passo 2 (git clone ...) e me diga o que apareceu. Se der certo, rode dir dentro da pasta brnfinal e me mande a lista de arquivos — assim eu te digo exatamente qual comando usar para iniciar.

Agora sim, deu certo! 🎉 O projeto foi clonado com sucesso e vejo que você já tem os arquivos dentro da pasta brnfinal. E olha só: é um projeto em Python, não em Node.js. Isso explica por que o npm install não funcionou antes — não tinha nada a ver com Node.

📋 O que você tem aí
Olhando a lista de arquivos, dá para identificar:

Arquivo	O que é
iniciar_brn.bat	✅ Provavelmente é o seu "iniciar tudo de uma vez"
iniciar_brn.cmd	Outra versão do inicializador
instalar_ngrok.bat	Instalador do ngrok (usado para expor serviços localmente)
main.py, explorer.py, crypto_wallet.py	Código Python do projeto
blockchain_node_6001.db	Banco de dados do nó da blockchain
index.html	Página web do projeto
README.md	Documentação
LICENSE	Licença
✅ Como executar
Como você está na pasta certa (brnfinal), agora basta rodar:

cmd
iniciar_brn.bat
explorador de blocos  no link https://seventy-rigging-ploy.ngrok-free.dev
1. Localmente (na sua própria máquina)
Abra o navegador em:

text
http://localhost:8080
ou

text
http://127.0.0.1:8080
Importante: o explorer.py é um processo separado. O main.py sobe o nó BRN (porta 6001) e o túnel Ngrok (porta 8080), mas não sobe o explorer. Você precisa rodar o explorer em outra janela do CMD, com o venv ativo:

cmd
cd "C:\Users\adnac\Desktop\bruno crypto\agora-brn"
env\Scripts\activate
python explorer.py
Saída esperada:

text
* Running on http://127.0.0.1:8080
Deixe essa janela aberta. Em outra janela, rode o main.py 6001. Aí o explorador aparece no navegador.

Se o explorer.py não estiver na pasta, é porque não foi baixado. Verifique com:

cmd
dir explorer.py
Se não achar, baixe do repositório original:

cmd
curl -L -o explorer.py https://raw.githubusercontent.com/brunoldo2312/brnfinal/main/explorer.py
(se o caminho não funcionar, abra o repositório no GitHub, clique em explorer.py → Raw → salve com Ctrl+S em explorer.py.)

2. Pelo painel do Ngrok (mostra a URL pública)
Enquanto o main.py estiver rodando, abra:

text
http://127.0.0.1:4040
Esse é o painel local do Ngrok. Ele mostra uma URL do tipo:

text
https://abcd-1234.ngrok-free.app

🖥️ Opção 1: Prompt de Comando (CMD)

No CMD, a sintaxe VAR=valor do Linux não funciona. Usamos set e && para encadear os comandos. Copie e cole:

```cmd
set "NGROK_AUTHTOKEN=3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4" && python main.py
```

⚡ Opção 2: PowerShell

Se você usa o PowerShell, a sintaxe é um pouco diferente:

```powershell
$env:NGROK_AUTHTOKEN="3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4"; python main.py
```

---

📄 Opção 3: Criar um Script .cmd (Recomendado)

Se você quiser dar apenas um duplo clique para rodar tudo, crie um arquivo chamado iniciar_brn.cmd na mesma pasta do seu main.py e cole o código abaixo. Ele configura o token e inicia o programa automaticamente:

```cmd
@echo off
title Iniciar Moeda Bruno (BRN) com Ngrok
cd /d "%~dp0"

echo ========================================================
echo       CONFIGURANDO TOKEN DO NGROK E INICIANDO O NO
echo ========================================================
echo.

:: Token configurado abaixo
set "NGROK_AUTHTOKEN=3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4"

echo Token configurado. Iniciando main.py...
echo.

python main.py

echo.
echo O programa foi encerrado.
pause
```

📌 Observações Importantes:

1. O main.py inicia o Ngrok sozinho? Se o seu main.py já estiver programado para ler a variável NGROK_AUTHTOKEN e criar o túnel automaticamente, os comandos acima funcionarão perfeitamente.
2. Se o main.py não iniciar o Ngrok: Você precisará abrir outro terminal e rodar ngrok http 8080 manualmente, como fizemos nos passos anteriores.
3. Substitua o token: Novamente, use o token apenas para testar e depois troque-o no painel do Ngrok por um novo, mantendo este em segredo.

🚀 Passo 1: Criar uma Conta no Ngrok

1. Acesse https://dashboard.ngrok.com/signup e crie uma conta gratuita.
2. Após o login, o painel exibirá o seu Authtoken (uma sequência longa de letras e números). Copie-o, pois você precisará dele no próximo passo.

💾 Passo 2: Instalar o Ngrok no Linux (Ubuntu/Debian)

No terminal do seu computador, execute os comandos abaixo para instalar via apt (a forma mais simples):

```bash
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | \
  sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null && \
  echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | \
  sudo tee /etc/apt/sources.list.d/ngrok.list && \
  sudo apt update && \
  sudo apt install ngrok
```

Caso prefira baixar o binário diretamente:

```bash
wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
tar xvzf ngrok-v3-stable-linux-amd64.tgz
sudo mv ngrok /usr/local/bin/
```

Após a instalação, verifique se funcionou com ngrok version.

🔑 Passo 3: Conectar o Ngrok à Sua Conta

Execute o comando abaixo, substituindo SEU_TOKEN_AQUI pelo Authtoken que você copiou no Passo 1:

```bash
ngrok config add-authtoken SEU_TOKEN_AQUI
```

Isso vincula o Ngrok instalado na sua máquina à sua conta.

⛓️ Passo 4: Iniciar o Explorador de Blocos

Em um terminal, navegue até a pasta do projeto e execute o explorador (criado anteriormente) na porta 8080:

```bash
python3 explorer.py
```

Mantenha este terminal aberto. O explorador precisa estar rodando para que o Ngrok consiga redirecionar o tráfego para ele.

🌐 Passo 5: Criar o Túnel Público com Ngrok

Abra outro terminal e execute:

```bash
ngrok http 8080
```

Você verá uma saída semelhante a esta (o link é um exemplo):

```
Forwarding    https://abcd-1234.ngrok-free.app -> http://localhost:8080
```

O endereço https://abcd-1234.ngrok-free.app é o seu link público temporário. Qualquer pessoa que acessá-lo verá o seu explorador de blocos.

📋 Passo 6: Preencher o Formulário da Exchange

No formulário da cexswap.cc, no campo "Explorador de blocos", cole o link que o Ngrok gerou (ex: https://abcd-1234.ngrok-free.app). O link deve começar com https://.

---

⚠️ Avisos Importantes sobre a "Opção Rápida"

· Temporário: O link gerado pelo Ngrok é efêmero. Ele expira assim que você fecha o terminal do Ngrok ou desliga o computador. Se a exchange fizer uma verificação depois, o link estará quebrado.
· Limitações do Plano Gratuito: Contas gratuitas do Ngrok têm restrições de tempo de sessão e número de conexões simultâneas. Para um uso mais estável, seria necessário um plano pago.
· Segurança: Expor seu computador local à internet traz riscos. O Ngrok cria um túnel, mas não substitui a segurança de um servidor dedicado. Certifique-se de que seu explorador não exponha dados sensíveis.
· Não é uma Solução Definitiva: Para que a exchange aceite e mantenha a listagem, o ideal é hospedar o explorador em uma VPS (Servidor Virtual Privado) com um domínio próprio. O Ngrok é excelente para testes rápidos, mas não é indicado para produção.

Resumo: Use o Ngrok para obter o link rapidamente e preencher o formulário, mas esteja ciente de que a exchange pode rejeitar a solicitação por causa da natureza temporária do link. O próximo passo recomendado é migrar o explorador para uma hospedagem permanente.
# 💼 Moeda Bruno (BRN) - Carteira Avançada & Blockchain P2P

A **Moeda Bruno (BRN)** é uma implementação experimental de um ecossistema de criptomoeda descentralizado baseado em princípios acadêmicos do protocolo *CryptoNote/Monero*. O projeto apresenta uma arquitetura modular com um livro-razão imutável, sincronização autônoma de nós Peer-to-Peer (P2P), propagação de transações via Mempool Broadcast e um utilitário automático de redirecionamento de portas (UPnP).

---

## 🚀 Funcionalidades

*   **Consenso de Maior Cadeia (*Longest Chain Rule*):** Algoritmo de resolução de consenso que substitui atomicamente a cadeia local se um par remoto apresentar uma blockchain estritamente mais longa e válida.
*   **Gênese Determinístico (Cadeia Única):** O bloco 0 tem timestamp, nonce e hash fixos (`GENESIS_HASH`), então **todo computador cria exatamente a mesma gênese** e todos compartilham **uma única blockchain**. Nós com gênese diferente (cadeias antigas/divergentes) são recusados na sincronização.
*   **Mempool Persistente com Gossip:** Transações pendentes são gravadas em disco (`mempool_node_<porta>.json`), sobrevivem a reinicializações, são retransmitidas aos demais pares (relay) e removidas automaticamente quando confirmadas em bloco ou após sincronização de cadeia. Transações recebidas com remetente ainda sem saldo local (nó atrasado) vão para uma fila de órfãs (`*.orphan.json`) e são promovidas assim que a cadeia se atualiza.
*   **Livro-Razão Relacional (SQLite3):** Persistência imutável indexada com auditoria histórica e reconstituição dinâmica de saldos em tempo real.
*   **Backup Criptografado de Chaves (.wallet):** Cifragem simétrica em fluxo utilizando derivação de chaves PBKDF de 5000 rounds para proteção de Spend Keys locais.
*   **Interface Gráfica Nativa (Desktop Puro):** Janela escura desacoplada construída sobre a ponte de injeção JavaScript-Python (`pywebview` + `PyQt6`).
*   **Endereço de Recebimento:** Botão para copiar o endereço público da carteira com um clique, facilitando o recebimento de BRN sem expor a chave privada.

---

## 📁 Estrutura Modular do Projeto

O ecossistema foi dividido em módulos isolados para garantir a consistência de dados, mitigar erros de tokenização e otimizar o tempo de compilação:

```text
├── bruno_blockchain_real.py  # Motor principal, API de controle, mempool e chamadas P2P
├── cripto_db.py              # Camada de persistência relacional e queries ordinais SQLite3
├── cripto_wallet.py          # Gerenciador de backup e criptografia PBKDF simétrica
├── index.html                # Interface visual baseada na ponte Javascript-Python Native
├── blockchain_node_<porta>.db   # Cadeia de blocos do nó (SQLite)
├── mempool_node_<porta>.json    # Transações pendentes do nó (persistidas em disco)
└── wallets/                     # Backups .wallet criptografados
```

### Receber BRN

1. Crie ou importe uma carteira.
2. Abaixo de **Seu Endereço Público**, clique em **Copiar endereço de recebimento**.
3. Envie somente esse endereço `brn1...` para quem fará o depósito. Nunca compartilhe a chave privada.

## Segurança e limites do protótipo

Esta é uma blockchain educacional, não indicada para valores reais. A versão atual valida a assinatura contra o endereço do remetente, impede gasto duplo na mempool, rejeita blocos/cadeias com gastos sem saldo e limita mensagens P2P recebidas.

Backups novos usam `cryptography` (Fernet com PBKDF2-SHA256 e 600.000 iterações), exigem senha de ao menos 12 caracteres e são gravados na pasta `wallets/`. Backups antigos Fernet ainda podem ser importados e devem ser reexportados. Instale as dependências antes de iniciar:

```bash
pip install ecdsa cryptography pywebview pyqt6
```

O UPnP deixou de ser ativado automaticamente. Só exponha a porta da carteira à internet se você entender e aceitar esse risco; para testes na mesma rede, use a sincronização manual da interface.

---

## 🛠️ Como Executar o Projeto (Máquina Local)

### 1. Preparação do Ambiente e Dependências (Linux Ubuntu)
Abra o terminal no diretório do projeto e execute os comandos para instalar as bibliotecas de sistema e isolar o ambiente virtual:

```bash
# Instalar pacotes de sistema necessários
sudo apt update && sudo apt install python3-venv python3-full python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 -y

# Criar e ativar o ambiente virtual (VENV)
python3 -m venv env
source env/bin/activate

# Instalar dependências de execução e o motor gráfico isolado PyQt6
pip install --upgrade pip
pip install pywebview flask pyqt6 PyQt6-WebEngine qtpy
```

### 2. Inicialização do Nó Principal
Sempre limpe os bancos de dados corrompidos ou inconsistentes de sessões anteriores antes de iniciar o nó na porta de sua escolha (Ex: `6001`):

```bash
rm -rf __pycache__
rm -f *.db mempool_node_*.json
python3 bruno_blockchain_real.py 6001
```

---

## 🌐 Sincronização entre Máquinas Físicas Diferentes

Para rodar a Moeda Bruno em múltiplos computadores conectados na mesma rede Wi-Fi ou cabo:

### 1. Identificar o IP da Máquina Principal
No terminal do seu nó principal (Ex: Seu HP Pavilion), execute:
```bash
hostname -I
# Retornará algo como: 192.168.0.17
```

### 2. Executar o Nó na Segunda Máquina

> **IMPORTANTE — cadeia única:** antes de iniciar, apague os bancos antigos/divergentes em **todos** os computadores (`blockchain_node_*.db`, `mempool_node_*.json`). Cadeias criadas por versões anteriores têm gênese com hash diferente e **não sincronizam** (o nó avisa "gênese pertence a outra rede"). Com a gênese determinística, cada nó recria o mesmo bloco 0 e todos passam a compartilhar **uma única blockchain**.

Copie os arquivos do projeto para o segundo computador. Abra o terminal dele e inicie o script alterando a porta de escuta para não gerar conflitos:

*   **No Linux:** `python3 bruno_blockchain_real.py 6002`
*   **No Windows (CMD Administrador):** 
    ```cmd
    python -m venv env
    .\env\Scripts\activate
    pip install pywebview flask pyqt6 PyQt6-WebEngine qtpy
    python bruno_blockchain_real.py 6002
    ```

### 3. Sincronizar as Cadeias de Blocos
1. Vá até a tela do aplicativo na **Segunda Máquina**.
2. No painel superior rosa (**Rede Descentralizada**), insira o IP do seu nó principal: `192.168.0.17`.
3. Defina a porta remota do nó principal: `6001`.
4. Clique em **"Conectar e Sincronizar Cadeira"**. O ecossistema fará o download e a verificação criptográfica do livro-razão automaticamente.

*Nota de Firewall:* A conexão P2P precisa de **porta de entrada liberada nos dois computadores**.
- **Windows (CMD Administrador):** `netsh advfirewall firewall add rule name="Bruno BRN 6001" dir=in action=allow protocol=TCP localport=6001` (troque 6001 pela porta do nó; faça em cada máquina para a sua porta).
- **Linux:** `sudo ufw allow 6001/tcp`.

### 4. Os dois computadores DEVEM compartilhar a mesma blockchain

Se um micro "envia mas o outro não recebe", quase sempre é porque estão em cadeias diferentes. Checklist:

1. **Código atualizado nos dois:** copie `bruno_blockchain_real.py`, `cripto_db.py`, `cripto_wallet.py` e `index.html` atualizados para o outro computador. Nó com código antigo cria gênese divergente.
2. **Convergência automática:** ao iniciar com o código novo, se o banco local tiver gênese antiga/divergente o nó faz backup (`*.divergent-*.bak`) e recria a gênese padrão `0000d904…`. Não é preciso apagar nada manualmente.
3. **Confirme na tela:** o card "🌐 Rede" mostra `🟢 cadeia única compartilhada` com a gênese. Se aparecer `🔴 cadeia divergente`, reinicie o nó. Os dois computadores devem mostrar o **mesmo** hash de gênese.
4. **Firewall nos dois sentidos:** cada máquina deve aceitar entrada na própria porta (veja acima).
5. **Mesma rede:** os dois IPs devem se enxergar (mesmo Wi-Fi/cabo). Teste com `ping <IP-do-outro>`.
6. **Sincronize:** em um dos nós, informe o IP/porta do outro e clique em "Conectar e Sincronizar". A cadeia maior vence e ambos ficam idênticos; transações pendentes se propagam via mempool/gossip.

---

## 📦 Como Gerar o Executável Binário (.App / .Exe)

Para distribuir a carteira de privacidade como um aplicativo desktop nativo e independente (sem a necessidade de instalação prévia do Python na máquina de destino):

### No Linux (Gera binário executável nativo)
```bash
pip install pyinstaller
pyinstaller --onefile --add-data "index.html:." --windowed bruno_blockchain_real.py

# Para executar o binário gerado na pasta dist/
cd dist
chmod +x bruno_blockchain_real
./bruno_blockchain_real 6001
```

### No Windows (Gera o arquivo executável .exe)
Abra o Prompt de Comando (CMD) do Windows dentro da pasta do projeto e execute:
```cmd
pip install pyinstaller
pyinstaller --onefile --add-data "index.html;." --windowed bruno_blockchain_real.py
```
O arquivo unificado estará disponível no diretório `dist/bruno_blockchain_real.exe`.
