# p2p.py
"""
PeerManager — fachada assíncrona sobre P2PNode (brn_p2p.py).

Interface esperada por node.py:
    pm.start()           -> coroutine
    pm.stop()            -> coroutine
    pm.broadcast(msg)    -> coroutine
    pm.public_endpoint   -> str | None
    pm.peers             -> dict[address, Peer]

Não reimplementa nada: delega tudo ao P2PNode.
"""

import asyncio
from brn_p2p import P2PNode


class PeerManager:
    def __init__(self, chain, port: int = 6001, **kwargs):
        self.chain = chain
        self.port = int(port)

        # P2PNode usa `api` para callbacks de consenso.
        # Passamos o chain — ele expõe:
        #   - _resolve_consensus(chain)
        #   - _verify_tx_structure(tx)
        #   - mempool / mempool_lock
        #   - db.get_raw_chain()
        self.node = P2PNode(api=chain, port=self.port, **kwargs)

        # Endpoint público (preenchido por ngrok/UPnP, se houver).
        # O node.py faz polling nisto para saber quando está exposto.
        self.public_endpoint = None

    # ────────────────────────────────────────────────────
    # Ciclo de vida async
    # ────────────────────────────────────────────────────
    async def start(self):
        """Sobe o P2PNode (threads) e mantém o loop vivo."""
        self.node.start()

        # Mantém a coroutine viva enquanto o node.py roda.
        # Cancelamento via asyncio.CancelledError (quando node.stop()
        # chama _stop.set(), o gather cancela tudo).
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    async def stop(self):
        """Encerra o P2PNode (fecha sockets, para threads)."""
        self.node.stop()

    async def broadcast(self, msg: dict):
        """Broadcast assíncrono — delega para o P2PNode síncrono."""
        self.node.broadcast(msg)

    # ────────────────────────────────────────────────────
    # Acesso direto (útil para explorer, CLI, testes)
    # ────────────────────────────────────────────────────
    @property
    def peers(self):
        return self.node.peers

    def connect_to_peer(self, host, port):
        return self.node.connect_to_peer(host, port)

    def peer_count(self) -> int:
        with self.node.peers_lock:
            return len(self.node.peers)

    def peer_addresses(self):
        with self.node.peers_lock:
            return list(self.node.peers.keys())