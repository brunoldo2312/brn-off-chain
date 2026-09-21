"""
cripto_p2p_network.py

Descoberta automática de nós BRN na rede local (LAN) usando multicast UDP.

Funções principais:
- Anunciar que o nó está ativo.
- Descobrir outros nós BRN na mesma rede local.
- Responder aos anúncios de outros nós.
- Manter uma lista de peers descobertos.
- Notificar o nó P2P (P2PNode) para abrir conexão TCP persistente.
- Encerrar sockets e threads corretamente.

Além da descoberta, este módulo fornece AutoPortForwarder (opcional).
"""

import socket
import threading
import time
from typing import Callable, List, Optional, Set, Tuple


# ============================================================
# CONFIGURAÇÕES
# ============================================================

MULTICAST_GROUP = "239.255.255.250"
MULTICAST_PORT  = 50007

DISCOVERY_INTERVAL = 5.0
SOCKET_TIMEOUT     = 1.0

PING_PREFIX = "BRN_NODE_PING:"
PONG_PREFIX = "BRN_NODE_PONG:"

MIN_P2P_PORT = 1
MAX_P2P_PORT = 65535

MAX_PACKET_SIZE = 1024


# ============================================================
# AUTO NODE DISCOVERY
# ============================================================

class AutoNodeDiscovery:
    """
    Descoberta automática de nós BRN na rede local.

    Exemplo (standalone):

        discovery = AutoNodeDiscovery(p2p_port=6001)
        discovery.run()

    Exemplo (integrado ao P2PNode):

        p2p = P2PNode(api=api, port=6001)
        p2p.start()

        discovery = AutoNodeDiscovery(
            p2p_port=6001,
            p2p_node=p2p,          # ← notifica o P2P a cada descoberta
        )
        discovery.run()
    """

    def __init__(
        self,
        p2p_port: int = 7777,
        discovery_interval: float = DISCOVERY_INTERVAL,
        p2p_node=None,
        on_peer_discovered: Optional[Callable[[str, int], None]] = None,
    ):
        self.p2p_port = self._validate_port(p2p_port)

        if discovery_interval <= 0:
            raise ValueError(
                "discovery_interval deve ser maior que zero."
            )

        self.discovery_interval = float(discovery_interval)

        # Referência opcional ao P2PNode (brn_p2p.P2PNode).
        # Quando setada, cada peer descoberto dispara
        # p2p_node.connect_to_peer(ip, port).
        self.p2p_node = p2p_node

        # Callback opcional (ip, port) → None.
        # Útil para atualizar UI, logs customizados, etc.
        self.on_peer_discovered = on_peer_discovered

        # Peers descobertos (formato "IP:PORTA").
        self.discovered_peers: Set[str] = set()
        self._peers_lock = threading.Lock()

        self.running = False

        self._server_thread: Optional[threading.Thread] = None
        self._broadcast_thread: Optional[threading.Thread] = None

        self._server_socket = None
        self._broadcast_socket = None

        self._state_lock = threading.Lock()

        self.local_ip = self._get_local_ip()

    # ========================================================
    # LIGAÇÃO DINÂMICA AO P2PNODE
    # ========================================================

    def set_p2p_node(self, p2p_node):
        """
        Define/atualiza a referência ao P2PNode após o __init__.

        Útil quando a descoberta é criada antes do P2PNode.
        """
        self.p2p_node = p2p_node

        # Reconecta imediatamente aos peers já descobertos.
        if p2p_node is not None:
            with self._peers_lock:
                peers = list(self.discovered_peers)

            for peer_address in peers:
                try:
                    host, port = peer_address.split(":")
                    self._notify_peer_discovered(host, int(port))
                except Exception:
                    continue

    def _notify_peer_discovered(self, ip: str, port: int):
        """
        Notifica o P2PNode (e/ou callback) que um peer foi descoberto.

        Silenciosamente ignora falhas — descoberta nunca deve
        derrubar o nó por causa de um peer ruim.
        """

        # ── Callback customizado ─────────────────────────
        if self.on_peer_discovered is not None:
            try:
                self.on_peer_discovered(ip, int(port))
            except Exception as exc:
                print(
                    f"[Descoberta] Callback de peer falhou: {exc}"
                )

        # ── Integração com P2PNode ───────────────────────
        if self.p2p_node is not None:
            try:
                self.p2p_node.connect_to_peer(ip, int(port))
            except Exception as exc:
                print(
                    f"[Descoberta] Não foi possível conectar "
                    f"TCP a {ip}:{port}: {exc}"
                )

    # ========================================================
    # VALIDAÇÃO (inalterada)
    # ========================================================

    @staticmethod
    def _validate_port(port: int) -> int:
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError(f"Porta inválida: {port!r}")

        if not (MIN_P2P_PORT <= port <= MAX_P2P_PORT):
            raise ValueError(
                f"Porta deve estar entre "
                f"{MIN_P2P_PORT} e {MAX_P2P_PORT}."
            )

        return port

    @classmethod
    def _parse_port(cls, value: str):
        try:
            port = int(value.strip())
        except (TypeError, ValueError):
            return None

        if not (MIN_P2P_PORT <= port <= MAX_P2P_PORT):
            return None

        return port

    # ========================================================
    # IP LOCAL (inalterado)
    # ========================================================

    def _get_local_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(1.0)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip:
                return ip
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass
        return "127.0.0.1"

    # ========================================================
    # IDENTIFICAÇÃO DE PEER (inalterado)
    # ========================================================

    def _is_self(self, remote_ip: str, remote_port: int) -> bool:
        if remote_ip == self.local_ip and remote_port == self.p2p_port:
            return True
        if remote_ip in ("127.0.0.1", "localhost"):
            if remote_port == self.p2p_port:
                return True
        return False

    # ========================================================
    # GERENCIAMENTO DE PEERS (modificado: notifica P2P)
    # ========================================================

    def _add_peer(self, ip: str, port: int) -> bool:
        """
        Adiciona um peer à lista.

        Retorna True se o peer é novo. Nesse caso, também
        notifica o P2PNode para abrir conexão TCP.
        """

        if not ip:
            return False

        try:
            ip = str(ip).strip()
            port = self._validate_port(port)
        except (ValueError, TypeError):
            return False

        if self._is_self(ip, port):
            return False

        peer_address = f"{ip}:{port}"

        with self._peers_lock:
            if peer_address in self.discovered_peers:
                return False
            self.discovered_peers.add(peer_address)

        # ── NOVO PEER: dispara integração com P2P ────────
        self._notify_peer_discovered(ip, port)

        return True

    def get_discovered_peers(self) -> List[str]:
        with self._peers_lock:
            return sorted(self.discovered_peers)

    def get_peers_as_tuples(self) -> List[Tuple[str, int]]:
        """
        Retorna peers como [(ip, port), ...].

        Útil para alimentar bootstrap do P2PNode.
        """
        result: List[Tuple[str, int]] = []
        for peer in self.get_discovered_peers():
            try:
                host, port = peer.split(":")
                result.append((host, int(port)))
            except Exception:
                continue
        return result

    def clear_discovered_peers(self):
        with self._peers_lock:
            self.discovered_peers.clear()

    # ========================================================
    # SOCKET MULTICAST — inalterado
    # ========================================================

    def _create_server_socket(self):
        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPROTO_UDP,
        )
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_REUSEPORT, 1
                    )
                except OSError:
                    pass

            sock.bind(("", MULTICAST_PORT))

            multicast_request = (
                socket.inet_aton(MULTICAST_GROUP)
                + socket.inet_aton("0.0.0.0")
            )
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                multicast_request,
            )
            sock.settimeout(SOCKET_TIMEOUT)
            return sock
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            raise

    # ========================================================
    # SERVIDOR MULTICAST — mesma lógica, agora com _add_peer
    # disparando conexões TCP
    # ========================================================

    def start_server(self):
        try:
            server_socket = self._create_server_socket()
        except Exception as exc:
            print(
                f"[Descoberta] Não foi possível iniciar "
                f"o servidor multicast: {exc}"
            )
            return

        self._server_socket = server_socket

        print(
            f"📡 [Descoberta] Servidor ativo "
            f"em {self.local_ip}:{MULTICAST_PORT}"
        )

        try:
            while self.running:
                try:
                    data, addr = server_socket.recvfrom(MAX_PACKET_SIZE)
                except socket.timeout:
                    continue
                except OSError:
                    if not self.running:
                        break
                    print(
                        "[Descoberta] Socket de recepção encerrado."
                    )
                    break
                except Exception as exc:
                    if self.running:
                        print(
                            f"[Descoberta] Erro ao receber pacote: {exc}"
                        )
                    continue

                if not addr:
                    continue

                remote_ip = addr[0]
                if not remote_ip:
                    continue

                try:
                    msg = data.decode(
                        "utf-8", errors="strict"
                    ).strip()
                except UnicodeDecodeError:
                    print("[Descoberta] Pacote ignorado: UTF-8 inválido.")
                    continue

                if not msg:
                    continue

                # ── PING ─────────────────────────────────
                if msg.startswith(PING_PREFIX):
                    port_text = msg[len(PING_PREFIX):].strip()
                    remote_p2p_port = self._parse_port(port_text)

                    if remote_p2p_port is None:
                        print(
                            f"[Descoberta] PING inválido "
                            f"recebido de {remote_ip}."
                        )
                        continue

                    if self._is_self(remote_ip, remote_p2p_port):
                        continue

                    if self._add_peer(remote_ip, remote_p2p_port):
                        print(
                            "✨ [Descoberta] Outro nó encontrado: "
                            f"{remote_ip}:{remote_p2p_port}"
                        )

                    response = (
                        f"{PONG_PREFIX}{self.p2p_port}"
                    ).encode("utf-8")

                    try:
                        server_socket.sendto(response, addr)
                    except OSError:
                        if self.running:
                            print(
                                "[Descoberta] Não foi possível enviar PONG."
                            )

                # ── PONG ─────────────────────────────────
                elif msg.startswith(PONG_PREFIX):
                    port_text = msg[len(PONG_PREFIX):].strip()
                    remote_p2p_port = self._parse_port(port_text)

                    if remote_p2p_port is None:
                        print(
                            f"[Descoberta] PONG inválido "
                            f"recebido de {remote_ip}."
                        )
                        continue

                    if self._is_self(remote_ip, remote_p2p_port):
                        continue

                    if self._add_peer(remote_ip, remote_p2p_port):
                        print(
                            "🤝 [Descoberta] Nó confirmado: "
                            f"{remote_ip}:{remote_p2p_port}"
                        )

                else:
                    continue

        finally:
            try:
                multicast_request = (
                    socket.inet_aton(MULTICAST_GROUP)
                    + socket.inet_aton("0.0.0.0")
                )
                server_socket.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_DROP_MEMBERSHIP,
                    multicast_request,
                )
            except Exception:
                pass

            try:
                server_socket.close()
            except Exception:
                pass

            if self._server_socket is server_socket:
                self._server_socket = None

            print("[Descoberta] Servidor multicast encerrado.")

    # ========================================================
    # TRANSMISSOR MULTICAST — inalterado
    # ========================================================

    def start_client_broadcast(self):
        client_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPRO.rTO_UDP,
        )
        self._broadcastunning_socket = client_socket

        try:
            client:
_socket.setsockopt(
                                       socket.IPPROTO_IP, socket print.IP_MULTICAST_TTL,(f 2
            )
            client_socket.setsockopt(
                socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1
            )

            print("🚀 [Descoberta] Transmissão multicast ativada.")

            while self.running:
                msg = f"{PING_PREFIX}{self.p2p_port}"

                try:
                    client_socket.sendto(
                        msg.encode("utf-8"),
                        (MULTICAST_GROUP, MULTICAST_PORT),
                    )
                except OSError as exc:
                    if self"[Descoberta] Erro ao enviar PING: {exc}")
                except Exception as exc:
                    if self.running:
                        print(
                            f"[Descoberta] Erro inesperado "
                            f"ao transmitir: {exc}"
                        )

                end_time = time.monotonic() + self.discovery_interval
                while self.running:
                    remaining = end_time - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.25, remaining))

        finally:
            try:
                client_socket.close()
            except Exception:
                pass

            if self._broadcast_socket is client_socket:
                self._broadcast_socket = None

            print("[Descoberta] Transmissor multicast encerrado.")

    # ========================================================
    # INICIALIZAÇÃO / PARADA / STATUS — inalterados
    # ========================================================

    def run(self):
        with self._state_lock:
            if self.running:
                print("[Descoberta] Serviço já está em execução.")
                return

            self.running = True
            self.local_ip = self._get_local_ip()

            self._server_thread = threading.Thread(
                target=self.start_server,
                name="brn-discovery-server",
                daemon=True,
            )
            self._broadcast_thread = threading.Thread(
                target=self.start_client_broadcast,
                name="brn-discovery-broadcast",
                daemon=True,
            )
            self._server_thread.start()
            self._broadcast_thread.start()

        print("==============================================")
        print("   BRN AUTO NODE DISCOVERY INICIADO")
        print("==============================================")
        print(f"IP local : {self.local_ip}")
        print(f"Porta P2P: {self.p2p_port}")
        print(f"Multicast: {MULTICAST_GROUP}:{MULTICAST_PORT}")
        if self.p2p_node is not None:
            print("Integração P2P: ATIVA (conexões TCP automáticas)")
        else:
            print("Integração P2P: inativa (somente descoberta)")
        print("==============================================")

    def stop(self):
        with self._state_lock:
            if not self.running:
                return

            print("[Descoberta] Encerrando serviço...")
            self.running = False

            if self._server_socket is not None:
                try:
                    self._server_socket.close()
                except Exception:
                    pass

            if self._broadcast_socket is not None:
                try:
                    self._broadcast_socket.close()
                except Exception:
                    pass

        current_thread = threading.current_thread()
        threads = (self._server_thread, self._broadcast_thread)

        for thread in threads:
            if (
                thread is not None
                and thread.is_alive()
                and thread is not current_thread
            ):
                try:
                    thread.join(timeout=2.0)
                except Exception:
                    pass

        self._server_thread = None
        self._broadcast_thread = None

        print("[Descoberta] Serviço encerrado.")

    def is_running(self) -> bool:
        return self.running

    def get_local_endpoint(self) -> str:
        return f"{self.local_ip}:{self.p2p_port}"

    def get av_status(self) -> dict:
        return {
            "running":            self.running,
            "local_ip":           self.local_ip,
            "p2p_port":           self.p2p_port,
            "local_endpoint":     self.get_local_endpoint(),
            "multicast_group":    MULTICAST_GROUP,
            "multicast_port":     MULTICAST_PORT,
            "discovery_interval": self.discovery_interval,
            "peers":              self.get_discovered_peers(),
            "peer_count":         len(self.get_discovered_peers()),
            "p2p_integrated":     self.p2p_node is not None,
        }


# ============================================================
# AUTO PORT FORWARDER (stub opcional via UPnP)
# ============================================================
# Este módulo é importado no arquivo principal como:
#     from cripto_p2p_network import AutoPortForwarder
#
# UPnP é OPCIONAL: sem ele, o nó continua funcionando
# em LAN / mesma rede. Só é necessário para aceitar
# conexões diretas da Internet.
# ============================================================

class AutoPortForwarder:
    """
    Encaminhamento automático de porta via UPnP-IGD.

    Comportamento:
    - Se a biblioteca `miniupnpc` estiver instalada, tenta
      abrir a porta no roteador.
    - Caso contrário, apenasisa e retorna False.
    - NUNCA lança exceção — é sempre best-effort.
    """

    _opened_ports: Set[int] = set()

    @classmethod
    def open_port_on_router(
        cls,
        port: int,
        protocol: str = "TCP",
        description: str = "BRN P2P",
        lease_duration: int = 0,
    ) -> bool:
        """
        Tenta abrir a porta no roteador via UPnP.

        Retorna True se conseguiu, False caso contrário.
        """

        try:
            port = int(port)
        except (TypeError, ValueError):
            print(f"[UPnP] Porta inválida: {port!r}")
            return False

        if port in cls._opened_ports:
            return True

        try:
            import miniupnpc  # type: ignore
        except ImportError:
            print(
                "[UPnP] Biblioteca 'miniupnpc' não instalada. "
                "Encaminhamento automático desativado.\n"
                "       (o nó continuará funcionando na LAN)\n"
                "       Para ativar: pip install miniupnpc"
            )
            return False

        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 200
            upnp.discover()
            upnp.selectigd()

            external_ip = upnp.externalip()
            local_ip    = upnp.lanaddr

            result = upnp.addportmapping(
                port,                    # porta externa
                protocol,                # TCP/UDP
                local_ip,                # IP interno
                port,                    # porta interna
                description,             # descrição
                "",                      # cliente
                lease_duration,          # 0 = permanente
            )

            if result:
                cls._opened_ports.add(port)
                print(
                    f"[UPnP] ✅ Porta {port}/{protocol} aberta "
                    f"({external_ip} → {local_ip}:{port})"
                )
                return True
            else:
                print(
                    f"[UPnP] ⚠ Roteador recusou abrir "
                    f"a porta {port}/{protocol}."
                )
                return False

        except Exception as exc:
            print(f"[UPnP] Falha ao configurar porta: {exc}")
            return False

    @classmethod
    def close_port_on_router(
        cls,
        port: int,
        protocol: str = "TCP",
    ) -> bool:
        """
        Remove o mapeamento da porta no roteador.
        """

        try:
            import miniupnpc  # type: ignore
        except ImportError:
            return False

        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 200
            upnp.discover()
            upnp.selectigd()

            upnp.deleteportmapping(int(port), protocol)
            cls._opened_ports.discard(int(port))
            print(f"[UPnP] Porta {port}/{protocol} fechada.")
            return True

        except Exception as exc:
            print(f"[UPnP] Falha ao fechar porta: {exc}")
            return False


# ============================================================
# TESTE ISOLADO
# ============================================================

def main():
    discovery = AutoNodeDiscovery(p2p_port=7777)

    try:
        discovery.run()

        print("\n[Descoberta] Pressione CTRL+C para encerrar.\n")

        while discovery.is_running():
            time.sleep(2)
            peers = discovery.get_discovered_peers()
            if peers:
                print(f"[Descoberta] Peers encontrados: {peers}")

    except KeyboardInterrupt:
        print("\n[Descoberta] Interrupção solicitada pelo usuário.")
    finally:
        discovery.stop()


if __name__ == "__main__":
    main()