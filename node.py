"""
node.py — Encapsula Blockchain + PeerManager + mineração + auto-reveal MEV.
Usado por main.py e explorer.py.
"""
import asyncio
import os
import secrets
import threading
import time

from bruno_blockchain_real import (
    Blockchain, NATIVE_ASSET,
    MEV_COMMIT_MIN_BLOCKS, MEV_COMMIT_MAX_BLOCKS,
)
from p2p import PeerManager

try:
    from cripto_wallet import WalletManager
except ImportError:
    WalletManager = None

PORT           = int(os.environ.get("BRN_P2P_PORT", "6001"))
MINER_INTERVAL = float(os.environ.get("BRN_MINER_INTERVAL", "0.5"))
MINER_ENABLED  = os.environ.get("BRN_MINE", "1") == "1"
BASE           = os.environ.get("BRN_BASE", "BRN")
QUOTE          = os.environ.get("BRN_QUOTE", "USDC")


def load_identity():
    """Carrega ou gera identidade do nó (chaves públicas/privadas)."""
    if WalletManager is None:
        raise RuntimeError("cripto_wallet indisponivel.")
    if hasattr(WalletManager, "load_node_identity"):
        return WalletManager.load_node_identity()
    sk = os.environ.get("BRN_NODE_SK")
    pk = os.environ.get("BRN_NODE_PK")
    if not sk or not pk:
        if hasattr(WalletManager, "generate_keypair"):
            sk, pk = WalletManager.generate_keypair()
            print("[node] identidade nova gerada — guarde as chaves!")
        else:
            raise RuntimeError("WalletManager sem generate_keypair.")
    return {"address": WalletManager.address_from_public_key(pk),
            "public_key": pk, "spend_secret_key": sk}


class Node:
    """
    Sobe blockchain + P2P + mineração + auto-reveal MEV em thread própria.
    Expõe API pública para o main.py (CLI) e explorer.py (dashboard).
    """

    def __init__(self, identity=None, enable_miner=None, enable_auto_reveal=True):
        self.identity = identity or load_identity()
        self.chain = Blockchain(node_identity=self.identity)
        self.pm = PeerManager(self.chain, port=PORT)
        self.enable_miner = MINER_ENABLED if enable_miner is None else enable_miner
        self.enable_auto_reveal = enable_auto_reveal
        self.running = False
        self._loop = None
        self._thread = None
        self._stop = None
        # commits pendentes deste nó (commit_hash -> info)
        self.pending_commits: dict[str, dict] = {}
        # preenchido após start_ngrok()
        self.public_endpoint = None

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def start(self):
        """Inicia a thread do nó (não bloqueia)."""
        self._thread = threading.Thread(target=self._run, daemon=True, name="brn-node")
        self._thread.start()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            print(f"[node] erro: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self):
        self.running = True
        self._stop = asyncio.Event()

        # -------------------- sub-loops --------------------
        async def sync_endpoint():
            """Copia public_endpoint do p2p assim que ngrok subir."""
            while not self._stop.is_set():
                if self.pm.public_endpoint and not self.public_endpoint:
                    self.public_endpoint = self.pm.public_endpoint
                await asyncio.sleep(0.5)

        async def miner():
            if not self.enable_miner:
                print("[node] mineração desativada")
                return
            loop = asyncio.get_event_loop()
            while not self._stop.is_set():
                block = await loop.run_in_executor(None, self.chain.produce_block)
                if block is None:
                    await asyncio.sleep(1)
                    continue
                try:
                    await self.pm.broadcast({
                        "type": "inv_block",
                        "height": block.index,
                        "hash": block.hash,
                    })
                except Exception as e:
                    print(f"[node] broadcast falhou: {e}")
                # auto-reveal sempre que um bloco é minerado
                await self._maybe_reveal()
                await asyncio.sleep(MINER_INTERVAL)

        async def auto_reveal():
            if not self.enable_auto_reveal:
                return
            while not self._stop.is_set():
                await self._maybe_reveal()
                await asyncio.sleep(1.0)

        # -------------------- gather --------------------
        try:
            await asyncio.gather(
                self.pm.start(),
                miner(),
                auto_reveal(),
                sync_endpoint(),
            )
        except asyncio.CancelledError:
            pass

    def stop(self):
        """Encerra P2P, mineração e auto-reveal."""
        self.running = False
        if self._stop and self._loop:
            try:
                self._stop.set()
                asyncio.run_coroutine_threadsafe(self.pm.stop(), self._loop)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # MEV — commit / reveal
    # ------------------------------------------------------------------
    async def _maybe_reveal(self):
        """Percorre commits pendentes e revela os que já passaram do MIN_BLOCKS."""
        cur = self.chain.height
        for ch, info in list(self.pending_commits.items()):
            delta = cur - info["block"]
            if delta >= MEV_COMMIT_MIN_BLOCKS:
                r = self.chain.reveal_order(
                    self.identity, ch, info["salt"],
                    info["base"], info["quote"], info["side"],
                    info["price"], info["amount"])
                if r.get("ok"):
                    print(f"[reveal] {r.get('tx_hash','')[:16]}... ok")
                    del self.pending_commits[ch]
                elif ("aguarde" not in r.get("msg", "") and
                      "duplicada" not in r.get("msg", "")):
                    print(f"[reveal] falhou: {r.get('msg')}")
                    del self.pending_commits[ch]

    def commit_order(self, side: str, price: float, amount: float,
                     base: str = None, quote: str = None) -> dict:
        """
        Envia order_commit e registra para auto-reveal.
        Retorna dict com ok/msg e (se ok) commit_hash + salt.
        """
        base = base or BASE
        quote = quote or QUOTE
        r = self.chain.commit_order(self.identity, base, quote, side, price, amount)
        if r.get("ok"):
            self.pending_commits[r["commit_hash"]] = {
                "salt": r["salt"], "side": side,
                "base": base, "quote": quote,
                "price": price, "amount": amount,
                "block": self.chain.height,
            }
        return r

    def reveal_order(self, commit_hash: str, salt: str,
                     side: str, price: float, amount: float,
                     base: str = None, quote: str = None) -> dict:
        """Reveal manual (usado pela CLI se o usuário quiser)."""
        base = base or BASE
        quote = quote or QUOTE
        return self.chain.reveal_order(self.identity, commit_hash, salt,
                                        base, quote, side, price, amount)

    # ------------------------------------------------------------------
    # Atalhos úteis (usados pela CLI)
    # ------------------------------------------------------------------
    def place_order(self, side: str, price: float, amount: float,
                    base: str = None, quote: str = None) -> dict:
        """Ordem direta (sem MEV) — mantido para compatibilidade/teste."""
        base = base or BASE
        quote = quote or QUOTE
        return self.chain.place_order(self.identity, base, quote,
                                       side, price, amount)

    def cancel_order(self, order_id: str) -> dict:
        return self.chain.cancel_order(self.identity, order_id)

    def order_book(self, depth: int = 8, base: str = None, quote: str = None) -> dict:
        base = base or BASE
        quote = quote or QUOTE
        return self.chain.order_book(base, quote, depth=depth)

    def my_orders(self):
        return self.chain.my_orders(self.identity["address"])

    def my_trades(self, limit: int = 20):
        return self.chain.my_trades(self.identity["address"], limit=limit)

    def my_commits(self):
        return self.chain.my_commits(self.identity["address"])

    def portfolio(self):
        return self.chain.portfolio(self.identity["address"])

    @property
    def height(self):
        return self.chain.height

    @property
    def tip_hash(self):
        return self.chain.tip_hash