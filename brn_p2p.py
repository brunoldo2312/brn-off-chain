# brn_p2p.py
"""
Camada P2P robusta para a rede Bruno (BRN).

Protocolo:
  - Cada mensagem é um JSON UTF-8 terminado em '\n' (framing).
  - Handshake trocado nos dois sentidos (inbound/outbound).
  - PING/PONG a cada 30s, timeout 90s.
  - Validação por nonce evita auto-conexão.
  - Reconexão automática via bootstrap e mensagens PEERS.

Tipos de mensagem:
  HANDSHAKE          → enviado ao abrir conexão
  HANDSHAKE_RESPONSE → resposta ao HANDSHAKE
  PING / PONG        → keepalive
  GET_PEERS / PEERS  → descoberta de peers conhecidos
  NEW_TX             → broadcast de transação
  NEW_CHAIN          → broadcast de cadeia (após mineração)
  GET_HEIGHT/HEIGHT  → altura remota
  GET_CHAIN / CHAIN  → cadeia remota completa
"""

import socket
import threading
import json
import time
import secrets


# ════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ════════════════════════════════════════════════════════════
MSG_DELIMITER    = b"\n"
MAX_MSG_SIZE     = 10 * 1024 * 1024     # 10 MB
PING_INTERVAL    = 30                   # segundos
PEER_TIMEOUT     = 90                   # segundos
MAX_PEERS        = 25
CONNECT_TIMEOUT  = 5.0

NETWORK_MAGIC    = "BRN_MAINNET_V1"
PROTOCOL_VERSION = 1


# ════════════════════════════════════════════════════════════
# PEER
# ════════════════════════════════════════════════════════════
class Peer:
    """
    Encapsula uma conexão TCP com outro nó.
    Cada Peer tem thread própria de leitura (spawned pelo P2PNode).
    """

    def __init__(self, host, port, sock=None, outbound=False):
        self.host           = host
        self.port           = int(port)
        self.address        = f"{host}:{port}"
        self.sock           = sock
        self.outbound       = outbound
        self.handshake_done = False
        self.last_seen      = time.time()
        self.buffer         = b""
        self.lock           = threading.Lock()
        self.closed         = False
        self.remote_height  = 0

    # ────────────────────────────────────────────────────
    # Conexão de saída
    # ────────────────────────────────────────────────────
    def connect(self, timeout=CONNECT_TIMEOUT):
        """Abre a conexão TCP (modo outbound)."""
        self.sock = socket.create_connection(
            (self.host, self.port), timeout=timeout
        )
        self.sock.settimeout(None)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return self

    # ────────────────────────────────────────────────────
    # Envia um dict como JSON + '\n'
    # ────────────────────────────────────────────────────
    def send(self, msg_dict):
        """Serializa e envia. Retorna True/False."""
        if self.closed or not self.sock:
            return False
        try:
            payload = (json.dumps(msg_dict) + "\n").encode("utf-8")
            with self.lock:
                self.sock.sendall(payload)
            return True
        except Exception:
            self.close()
            return False

    # ────────────────────────────────────────────────────
    # Fecha a conexão
    # ────────────────────────────────────────────────────
    def close(self):
        """Fecha socket (idempotente)."""
        if self.closed:
            return
        self.closed = True
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    # ────────────────────────────────────────────────────
    # Generator que decodifica mensagens (framing por '\n')
    # ────────────────────────────────────────────────────
    def recv_messages(self):
        """
        Loop bloqueante que devolve dicts à medida que chegam.
        Encerra quando a conexão fecha ou ocorre erro.
        """
        while not self.closed:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break

            if not chunk:
                break

            self.buffer += chunk

            # Proteção contra flood
            if len(self.buffer) > MAX_MSG_SIZE:
                break

            # Extrai todas as mensagens completas do buffer
            while MSG_DELIMITER in self.buffer:
                line, self.buffer = self.buffer.split(MSG_DELIMITER, 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                    self.last_seen = time.time()
                    yield msg
                except Exception:
                    # Fragmento inválido — ignora e continua
                    continue


# ════════════════════════════════════════════════════════════
# NÓ P2P
# ════════════════════════════════════════════════════════════
class P2PNode:
    """
    Gere sockets, peers, handshake, ping e dispatch de mensagens.

    `api` deve expor (opcionalmente):
        - api.connect_and_sync(host, port)
        - api._verify_tx_structure(tx)
        - api._validate_block(block)
        - api._resolve_consensus(chain)
        - api.mempool, api.mempool_lock
        - api.db.get_raw_chain()
    """

    def __init__(self, api, port, bootstrap_peers=None):
        self.api              = api
        self.port             = int(port)
        self.bootstrap_peers  = list(bootstrap_peers or [])
        self.peers            = {}          # address -> Peer
        self.peers_lock       = threading.Lock()
        self.node_nonce       = secrets.token_hex(8)
        self.server_sock      = None
        self.running          = False

    # ────────────────────────────────────────────────────
    # API pública
    # ────────────────────────────────────────────────────
    def start(self):
        """Inicia servidor + loops de background (não bloqueia)."""
        self.running = True
        threading.Thread(target=self._serve,          daemon=True,
                         name="brn-p2p-serve").start()
        threading.Thread(target=self._ping_loop,      daemon=True,
                         name="brn-p2p-ping").start()
        threading.Thread(target=self._bootstrap_loop, daemon=True,
                         name="brn-p2p-bootstrap").start()

    def stop(self):
        """Encerra tudo (servidor, loops e conexões)."""
        self.running = False
        try:
            if self.server_sock:
                self.server_sock.close()
        except Exception:
            pass

        with self.peers_lock:
            peers = list(self.peers.values())

        for p in peers:
            p.close()

        with self.peers_lock:
            self.peers.clear()

    def broadcast(self, msg, exclude=None):
        """Envia `msg` para todos os peers com handshake completo."""
        with self.peers_lock:
            peers = list(self.peers.values())

        for p in peers:
            if p is exclude:
                continue
            if not p.handshake_done:
                continue
            p.send(msg)

    def connect_to_peer(self, host, port):
        """Abre conexão TCP de saída e envia handshake."""
        address = f"{host}:{port}"

        with self.peers_lock:
            if address in self.peers:
                return self.peers[address]
            if len(self.peers) >= MAX_PEERS:
                return None

        try:
            p = Peer(host, port, outbound=True).connect()
        except Exception:
            return None

        with self.peers_lock:
            # double-check (outra thread pode ter adicionado)
            if address in self.peers:
                p.close()
                return self.peers[address]
            self.peers[address] = p

        print(f"[P2P] → Conectado a {address}. Enviando handshake...")
        p.send(self._make_handshake())

        threading.Thread(
            target=self._handle_peer, args=(p,), daemon=True,
            name=f"brn-p2p-peer-{address}"
        ).start()
        return p

    # ────────────────────────────────────────────────────
    # Servidor TCP
    # ────────────────────────────────────────────────────
    def _serve(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            s.bind(("0.0.0.0", self.port))
        except OSError as e:
            print(f"[P2P] ❌ Não foi possível escutar na porta "
                  f"{self.port}: {e}")
            return

        s.listen(50)
        self.server_sock = s
        print(f"[P2P] 🔵 Escutando na porta {self.port} "
              f"(nonce={self.node_nonce})")

        while self.running:
            try:
                conn, addr = s.accept()
            except Exception:
                break

            peer = Peer(addr[0], addr[1], sock=conn, outbound=False)

            # Nós escutando também enviam handshake proativamente
            peer.send(self._make_handshake())

            threading.Thread(
                target=self._handle_peer, args=(peer,), daemon=True,
                name=f"brn-p2p-in-{addr[0]}:{addr[1]}"
            ).start()

    # ────────────────────────────────────────────────────
    # Handler por peer (thread dedicada)
    # ────────────────────────────────────────────────────
    def _handle_peer(self, peer):
        # registra provisoriamente (será validado no handshake)
        with self.peers_lock:
            self.peers.setdefault(peer.address, peer)

        try:
            for msg in peer.recv_messages():
                self._process_message(msg, peer)
        except Exception as e:
            print(f"[P2P] erro em {peer.address}: {e}")
        finally:
            peer.close()
            with self.peers_lock:
                if self.peers.get(peer.address) is peer:
                    self.peers.pop(peer.address, None)
            print(f"[P2P] ✖ Desconectado: {peer.address}")

    # ────────────────────────────────────────────────────
    # Handshake
    # ────────────────────────────────────────────────────
    def _make_handshake(self):
        return {
            "type":      "HANDSHAKE",
            "version":   PROTOCOL_VERSION,
            "magic":     NETWORK_MAGIC,
            "nonce":     self.node_nonce,
            "node_port": self.port,
            "timestamp": time.time(),
        }

    def _validate_handshake(self, msg):
        return (
            msg.get("magic") == NETWORK_MAGIC
            and msg.get("nonce") != self.node_nonce
        )

    # ────────────────────────────────────────────────────
    # Dispatch de mensagens
    # ────────────────────────────────────────────────────
    def _process_message(self, msg, peer):
        mtype = msg.get("type")

        # ── HANDSHAKE ────────────────────────────────────
        if mtype == "HANDSHAKE":
            if not self._validate_handshake(msg):
                print(f"[P2P] ❌ Handshake inválido de {peer.address}")
                return peer.close()

            peer.handshake_done = True
            peer.send({
                "type":        "HANDSHAKE_RESPONSE",
                "status":      "ACCEPTED",
                "magic":       NETWORK_MAGIC,
                "nonce":       self.node_nonce,
                "node_port":   self.port,
                "known_peers": self._peer_list(),
            })
            print(f"[P2P] ✅ Handshake recebido de {peer.address}")

            # Puxa lista de peers conhecidos
            peer.send({"type": "GET_PEERS"})

            # Dispara sync se o peer tiver mais blocos
            if hasattr(self.api, "connect_and_sync"):
                threading.Thread(
                    target=self.api.connect_and_sync,
                    args=(peer.host, peer.port),
                    daemon=True,
                ).start()

        # ── HANDSHAKE_RESPONSE ───────────────────────────
        elif mtype == "HANDSHAKE_RESPONSE":
            if not self._validate_handshake(msg):
                return peer.close()

            peer.handshake_done = True
            print(f"[P2P] ✅ {peer.address} VALIDADO (outbound)")

            for kp in msg.get("known_peers", []):
                self._try_connect_addr(kp)

            if hasattr(self.api P, "connect_and_sync"):
ING                threading.Thread(
                    target=self.api.connect/P_and_sync,
                    args=(peer.host, peer.port),
                    daemon=True,
                ).start()

        # ──ONG ────────────────────────────────────
        elif mtype == "PING":
            peer.send({"type": "PONG", "timestamp": time.time()})

        elif mtype == "PONG":
            peer.last_seen = time.time()

        # ── PEERS ────────────────────────────────────────
        elif mtype == "GET_PEERS":
            peer.send({"type": "PEERS", "peers": self._peer_list()})

        elif mtype == "PEERS":
            for kp in msg.get("peers", []):
                self._try_connect_addr(kp)

        # ── TRANSAÇÕES ───────────────────────────────────
        elif mtype == "NEW_TX":
            tx = msg.get("tx")
            if not tx:
                return
            if hasattr(self.api, "_verify_tx_structure") and \
               self.api._verify_tx_structure(tx):
                with self.api.mempool_lock:
                    if tx not in self.api.mempool:
                        self.api.mempool.append(tx)
                # Rebroadcast (evita loop com exclude)
                self.broadcast({"type": "NEW_TX", "tx": tx}, exclude=peer)

        # ── CADEIA ───────────────────────────────────────
        elif mtype == "NEW_CHAIN":
            chain = msg.get("chain")
            if chain and hasattr(self.api, "_resolve_consensus"):
                result = self.api._resolve_consensus(chain)
                print(f"[P2P] 🔄 NEW_CHAIN de {peer.address}: {result}")

        elif mtype == "GET_HEIGHT":
            chain = self.api.db.get_raw_chain()
            peer.send({"type": "HEIGHT", "height": len(chain)})

        elif mtype == "HEIGHT":
            peer.remote_height = int(msg.get("height", 0))

        elif mtype == "GET_CHAIN":
            chain = self.api.db.get_raw_chain()
            peer.send({"type": "CHAIN", "chain": chain})

        elif mtype == "CHAIN":
            if hasattr(self.api, "_resolve_consensus"):
                result = self.api._resolve_consensus(msg.get("chain", []))
                print(f"[P2P] 🔄 CHAIN de {peer.address}: {result}")

        else:
            print(f"[P2P] ⚠ tipo desconhecido: {mtype}")

    # ────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────
    def _peer_list(self):
        """Retorna lista de endereços 'host:port' conectados."""
        with self.peers_lock:
            return list(self.peers.keys())

    def _try_connect_addr(self, addr):
        """Conecta a um endereço 'host:port' se válido e não-self."""
        try:
            host, port = addr.split(":")
            port = int(port)
            if port == self.port and host in (
                "127.0.0.1", "0.0.0.0", "localhost"
            ):
                return
            self.connect_to_peer(host, port)
        except Exception:
            pass

    # ────────────────────────────────────────────────────
    # Loops de background
    # ────────────────────────────────────────────────────
    def _ping_loop(self):
        """Envia PING a cada 30s e remove peers sem resposta."""
        while self.running:
            time.sleep(PING_INTERVAL)
            now = time.time()

            with self.peers_lock:
                peers = list(self.peers.values())

            for p in peers:
                p.send({"type": "PING", "timestamp": now})
                if now - p.last_seen > PEER_TIMEOUT:
                    print(f"[P2P] ⏱ {p.address} sem resposta — removendo.")
                    p.close()

    def _bootstrap_loop(self):
        """Tenta conectar aos seeds iniciais."""
        time.sleep(2)
        for host, port in self.bootstrap_peers:
            if int(port) == self.port and host in (
                "127.0.0.1", "0.0.0.0", "localhost"
            ):
                continue
            self.connect_to_peer(host, port)