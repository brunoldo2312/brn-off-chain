# Carteira BRN P2P — Mural de Ordens On-Chain (BRN ⇄ USDC)

Este documento explica, passo a passo, **como funciona** o mural de ordens de venda BRN ⇄ USDC que lê e escreve **direto na blockchain Polygon**, usando o contrato **EscrowFactory já deployado** (sem alterar o `.sol`).

* * *

## 1\. O que foi entregue

| Arquivo | O que é |
| --- | --- |
| `static/index.html` | Página do mural (UI completa + explicação embutida) |
| `static/style.css` | Design moderno, responsivo e print-friendly |
| `static/app.js` | Toda a lógica: leitura da blockchain, approve, criar/executar/cancelar |
| `static/favicon.svg` | Ícone da aba |
| `EXPLICACAO.md` | Este documento |

O front-end é **100% estático** — pode ser aberto direto no navegador ou servido por qualquer servidor (inclusive o `main.py` do projeto).

* * *

## 2\. Endereços usados (Polygon Mainnet — chainId 137)

| Contrato | Endereço | Decimais |
| --- | --- | --- |
| **EscrowFactory** | `0x5C305aCFF5cDFAee90276c2acEA4Aa841f7062d8` | — |
| **Token BRN** | `0xdBc1c747B1D4c27113F65A4620b8fEaC74e2A210` | 18 |
| **Token USDC** | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | 6 |

> ⚠️ O contrato **não está verificado** no PolygonScan. A interface (nomes das funções) foi **reconstruída a partir do bytecode** com a ferramenta `evmole`. Por isso o `app.js` usa os **selectors de 4 bytes** diretamente e decodifica as respostas manualmente — assim funciona mesmo sem o ABI oficial.

* * *

## 3\. Como o contrato funciona (arquitetura)

O `EscrowFactory` é uma **fábrica de escrows**. Cada ordem de venda gera um **novo contrato de escrow** que guarda os tokens do vendedor.

```
                 ┌─────────────────────────┐
                 │      EscrowFactory      │
                 │  (fábrica de escrows)   │
                 └───────────┬─────────────┘
                             │ criarOrdem(...)
                             ▼
        ┌────────────────────────────────────────┐
        │  Escrow #1   Escrow #2   Escrow #3 ...  │
        │  (cada um guarda os BRN de uma ordem)   │
        └────────────────────────────────────────┘
```

### Funções do Factory (selectors decodificados)

| Selector | Assinatura | O que faz |
| --- | --- | --- |
| `ceff4da6` | `criarOrdem(address,address,uint256,uint256)` | Cria um escrow e trava os BRN do vendedor |
| `0dc80995` | `ordensDe(address)` | Lista os escrows de um criador |
| `72c453b8` | `ordem(uint256)` | Endereço do escrow pelo índice |
| `8275d6fa` | `totalOrdens()` | Quantidade total de ordens |
| `e9b1e327` | `todasOrdens()` | Lista com todos os escrows |

### Funções do Escrow (selectors decodificados)

| Selector | Assinatura | O que faz |
| --- | --- | --- |
| `32c9e06c` | `obterDados()` | Retorna tudo: criador, tokens, valores, executado, cancelado |
| `041c797c` | `criador()` | Quem criou a ordem |
| `8a337bdc` | `tokenOferecido()` | Token que o vendedor oferece (BRN) |
| `0cd59b77` | `tokenDesejado()` | Token que o vendedor pede (USDC) |
| `f6c467b7` | `valorOferecido()` | Quantidade oferecida |
| `651cc708` | `valorDesejado()` | Quantidade pedida |
| `2a3a5716` | `executado()` | Se a ordem já foi executada |
| `7766a742` | `cancelado()` | Se a ordem foi cancelada |
| `b2d44d08` | `executar()` | Comprador executa a troca (atômica) |
| `8ffb1ccf` | `cancelar()` | Vendedor cancela e recebe os BRN de volta |

* * *

## 4\. O fluxo de uma ordem (passo a passo)

```
1. Vendedor aprova BRN  →  2. Cria ordem (BRN travado)  →  3. Comprador aprova USDC
        →  4. Executa a ordem  →  5. Troca atômica (BRN ⇄ USDC)
```

1.  **Aprovar BRN** — o vendedor chama `approve(factory, valor)` no token BRN, autorizando a fábrica a mover seus BRN.
2.  **Criar ordem** — o vendedor chama `criarOrdem(BRN, USDC, valorBRN, valorUSDC)`. A fábrica cria um escrow novo e **puxa os BRN** para dentro dele.
3.  **Aprovar USDC** — o comprador chama `approve(escrow, valor)` no token USDC.
4.  **Executar** — o comprador chama `executar()` no escrow. O contrato faz a **troca atômica**: manda os BRN para o comprador e os USDC para o vendedor.
5.  **Cancelar** (opcional) — enquanto ninguém executou, o vendedor pode chamar `cancelar()` e receber os BRN de volta.

### Por que é seguro?

A troca acontece **dentro do contrato, na mesma transação**. Ou as duas pernas (BRN e USDC) acontecem juntas, ou nada acontece — é **atômico**. Os BRN ficam **travados no escrow**, então o comprador tem a garantia de que o vendedor realmente possui os tokens.

* * *

## 5\. O que o front-end faz

-   **Lê o mural direto da blockchain** (via RPC público), **sem precisar de carteira**.
-   Mostra o **contador de ordens ativas** e o estado de cada uma (Ativa / Executada / Cancelada).
-   Faz o **approve automático** de BRN (para a fábrica) e de USDC (para o escrow).
-   Permite **criar**, **executar** e **cancelar** ordens pela MetaMask.
-   Usa **vários RPCs com fallback** + **timeouts** + **leitura paralela** para nunca travar.
-   Estado **"Consultando…"** enquanto busca os dados na rede.

* * *

## 6\. Como rodar

### Opção A — Abrir direto no navegador

Basta abrir `static/index.html` no navegador (Chrome/Brave com MetaMask).

### Opção B — Servir pelo projeto (recomendado)

```bash
python main.py
# abre http://localhost:8000
```

### Opção C — Servidor simples de teste

```bash
cd static
python -m http.server 8099
# abre http://localhost:8099
```

* * *

## 7\. Como usar (na prática)

1.  Abra a página. O mural carrega sozinho e mostra as ordens existentes.
2.  Clique em **"🔌 Conectar carteira"** e aprove na MetaMask (rede Polygon).
3.  Para **vender BRN**:
    -   Digite quanto BRN oferece e quanto USDC pede.
    -   Clique em **"✅ Aprovar BRN"** (1ª transação).
    -   Clique em **"➕ Criar ordem (trava BRN)"** (2ª transação).
4.  Para **comprar BRN** (executar uma ordem):
    -   Clique em **"⚡ Executar"** na ordem desejada.
    -   O app aprova o USDC automaticamente e executa a troca.
5.  Para **cancelar** sua própria ordem: clique em **"✖ Cancelar"**.

* * *

## 8\. Observações importantes

-   **Rede:** tudo na **Polygon Mainnet (chainId 137)**. Você precisa de POL para o gás.
-   **Contrato não verificado:** a interface foi reconstruída do bytecode. **Teste com valores pequenos** antes de usar valores reais.
-   **USDC usado:** `0x2791...4174` (USDC.e / bridged). Se preferir o USDC nativo (`0x3c49...3359`), basta trocar a constante `TOKEN_USDC_ADDRESS` no `app.js`.
-   **RPCs públicos** podem ter limite de requisições. Para produção, use um RPC dedicado (Alchemy, Infura, QuickNode) — basta adicionar a URL no topo do `app.js`.

* * *

## 9\. Verificação feita

-   ✅ Leitura on-chain confirmada: **5 ordens** lidas corretamente (10.000 BRN/1 USDC, 1.000/1, 300/1, 250/1, 1.000/50).
-   ✅ Renderização no navegador confirmada (screenshot).
-   ✅ Sintaxe do JavaScript validada (`node --check`).
-   ✅ Todos os arquivos servidos com HTTP 200.