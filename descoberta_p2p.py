"""
cripto_p2p_network.py

Descoberta automática de nós BRN na rede local (LAN) usando multicast UDP.

Funções principais:
- Anunciar que o nó está ativo.
- Descobrir outros nós BRN na mesma rede local.
- Responder aos anúncios de outros nós.
- Manter uma lista de peers descobertos.
- Validar mensagens recebidas.
- Encerrar sockets e threads corretamente.

Observação:
Este módulo faz descoberta de peers na LAN.
Ele não substitui o servidor P2P da blockchain e não cria conexões
TCP permanentes entre os nós.
"""

import socket
import threading
import time
from typing import List, Set, Tuple


# ============================================================
# CONFIGURAÇÕES
# ============================================================

MULTICAST_GROUP = "239.255.255.250"
MULTICAST_PORT = 50007

DISCOVERY_INTERVAL = 5.0
SOCKET_TIMEOUT = 1.0

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

    Exemplo:

        discovery = AutoNodeDiscovery(p2p_port=6001)
        discovery.run()

        peers = discovery.get_discovered_peers()

        discovery.stop()
    """

    def __init__(
        self,
        p2p_port: int = 7777,
        discovery_interval: float = DISCOVERY_INTERVAL,
    ):
        self.p2p_port = self._validate_port(p2p_port)

        if discovery_interval <= 0:
            raise ValueError(
                "discovery_interval deve ser maior que zero."
            )

        self.discovery_interval = float(discovery_interval)

        # Conjunto de endereços encontrados.
        # Formato: "IP:PORTA"
        self.discovered_peers: Set[str] = set()

        # Lock para proteger discovered_peers.
        self._peers_lock = threading.Lock()

        # Controle de execução.
        self.running = False

        # Threads.
        self._server_thread = None
        self._broadcast_thread = None

        # Socket utilizado pelo servidor multicast.
        self._server_socket = None

        # Socket utilizado para transmissão multicast.
        self._broadcast_socket = None

        # Lock para impedir start() simultâneo.
        self._state_lock = threading.Lock()

        # IP local utilizado para evitar adicionar o próprio nó.
        self.local_ip = self._get_local_ip()

    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    @staticmethod
    def _validate_port(port: int) -> int:
        """
        Valida uma porta TCP/UDP.
        """

        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError(
                f"Porta inválida: {port!r}"
            )

        if not (
            MIN_P2P_PORT
            <= port
            <= MAX_P2P_PORT
        ):
            raise ValueError(
                f"Porta deve estar entre "
                f"{MIN_P2P_PORT} e {MAX_P2P_PORT}."
            )

        return port

    @classmethod
    def _parse_port(cls, value: str):
        """
        Converte uma porta recebida pela rede para inteiro.

        Retorna:
            int | None
        """

        try:
            port = int(value.strip())
        except (TypeError, ValueError):
            return None

        if not (
            MIN_P2P_PORT
            <= port
            <= MAX_P2P_PORT
        ):
            return None

        return port

    # ========================================================
    # IP LOCAL
    # ========================================================

    def _get_local_ip(self) -> str:
        """
        Descobre o IP local utilizado para sair da máquina.

        Não envia dados efetivos para a Internet; a conexão UDP
        é utilizada apenas pelo sistema operacional para determinar
        a interface de rede apropriada.
        """

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )

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
    # IDENTIFICAÇÃO DE PEER
    # ========================================================

    def _is_self(self, remote_ip: str, remote_port: int) -> bool:
        """
        Verifica se o peer recebido corresponde ao próprio nó.
        """

        if remote_ip == self.local_ip and remote_port == self.p2p_port:
            return True

        # Também evita localhost apontando para nossa própria porta.
        if remote_ip in ("127.0.0.1", "localhost"):
            if remote_port == self.p2p_port:
                return True

        return False

    # ========================================================
    # GERENCIAMENTO DE PEERS
    # ========================================================

    def _add_peer(self, ip: str, port: int) -> bool:
        """
        Adiciona um peer à lista.

        Retorna:
            True  -> peer novo
            False -> já existia ou é inválido
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

        return True

    def get_discovered_peers(self) -> List[str]:
        """
        Retorna uma cópia da lista de peers encontrados.
        """

        with self._peers_lock:
            return sorted(self.discovered_peers)

    def clear_discovered_peers(self):
        """
        Limpa a lista de peers descobertos.
        """

        with self._peers_lock:
            self.discovered_peers.clear()

    # ========================================================
    # CRIAÇÃO DO SOCKET MULTICAST
    # ========================================================

    def _create_server_socket(self):
        """
        Cria o socket responsável por receber multicast.
        """

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPROTO_UDP,
        )

        try:
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_REUSEADDR,
                1,
            )

            # Algumas plataformas suportam SO_REUSEPORT.
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_REUSEPORT,
                        1,
                    )
                except OSError:
                    pass

            sock.bind(
                ("", MULTICAST_PORT)
            )

            # Entrada no grupo multicast.
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
    # SERVIDOR DE DESCOBERTA
    # ========================================================

    def start_server(self):
        """
        Escuta anúncios multicast de outros nós.
        """

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
                    data, addr = server_socket.recvfrom(
                        MAX_PACKET_SIZE
                    )

                except socket.timeout:
                    continue

                except OSError:
                    # Socket fechado durante stop().
                    if not self.running:
                        break

                    print(
                        "[Descoberta] Socket de recepção "
                        "foi encerrado."
                    )
                    break

                except Exception as exc:
                    if self.running:
                        print(
                            f"[Descoberta] Erro ao receber "
                            f"pacote: {exc}"
                        )
                    continue

                # --------------------------------------------
                # VALIDA IP DE ORIGEM
                # --------------------------------------------

                if not addr:
                    continue

                remote_ip = addr[0]

                if not remote_ip:
                    continue

                # --------------------------------------------
                # DECODIFICA MENSAGEM
                # --------------------------------------------

                try:
                    msg = data.decode(
                        "utf-8",
                        errors="strict",
                    ).strip()

                except UnicodeDecodeError:
                    print(
                        "[Descoberta] Pacote ignorado: "
                        "UTF-8 inválido."
                    )
                    continue

                if not msg:
                    continue

                # --------------------------------------------
                # PING
                # --------------------------------------------

                if msg.startswith(PING_PREFIX):

                    port_text = msg[
                        len(PING_PREFIX):
                    ].strip()

                    remote_p2p_port = self._parse_port(
                        port_text
                    )

                    if remote_p2p_port is None:
                        print(
                            f"[Descoberta] PING inválido "
                            f"recebido de {remote_ip}."
                        )
                        continue

                    # Ignora o próprio nó.
                    if self._is_self(
                        remote_ip,
                        remote_p2p_port,
                    ):
                        continue

                    is_new = self._add_peer(
                        remote_ip,
                        remote_p2p_port,
                    )

                    if is_new:
                        print(
                            "✨ [Descoberta] "
                            "Outro nó encontrado: "
                            f"{remote_ip}:{remote_p2p_port}"
                        )

                    # ----------------------------------------
                    # ENVIA PONG DIRETO AO REMETENTE
                    # ----------------------------------------

                    response = (
                        f"{PONG_PREFIX}{self.p2p_port}"
                    ).encode("utf-8")

                    try:
                        server_socket.sendto(
                            response,
                            addr,
                        )

                    except OSError:
                        if self.running:
                            print(
                                "[Descoberta] Não foi possível "
                                "enviar PONG."
                            )

                # --------------------------------------------
                # PONG
                # --------------------------------------------

                elif msg.startswith(PONG_PREFIX):

                    port_text = msg[
                        len(PONG_PREFIX):
                    ].strip()

                    remote_p2p_port = self._parse_port(
                        port_text
                    )

                    if remote_p2p_port is None:
                        print(
                            f"[Descoberta] PONG inválido "
                            f"recebido de {remote_ip}."
                        )
                        continue

                    if self._is_self(
                        remote_ip,
                        remote_p2p_port,
                    ):
                        continue

                    is_new = self._add_peer(
                        remote_ip,
                        remote_p2p_port,
                    )

                    if is_new:
                        print(
                            "🤝 [Descoberta] "
                            "Nó confirmado: "
                            f"{remote_ip}:{remote_p2p_port}"
                        )

                # --------------------------------------------
                # MENSAGEM DESCONHECIDA
                # --------------------------------------------

                else:
                    # Não interrompe o serviço por mensagens
                    # que pertencem a outros protocolos.
                    continue

        finally:

            try:
                # Sai do grupo multicast antes de fechar.
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

            print(
                "[Descoberta] Servidor multicast encerrado."
            )

    # ========================================================
    # TRANSMISSOR MULTICAST
    # ========================================================

    def start_client_broadcast(self):
        """
        Envia um PING multicast periodicamente.
        """

        client_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
            socket.IPPROTO_UDP,
        )

        self._broadcast_socket = client_socket

        try:
            # TTL 2 permite alcançar a rede local e alguns
            # segmentos próximos, dependendo da configuração.
            client_socket.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_TTL,
                2,
            )

            # Loop multicast habilitado para compatibilidade
            # com diferentes sistemas.
            client_socket.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_LOOP,
                1,
            )

            print(
                "🚀 [Descoberta] "
                "Transmissão multicast ativada."
            )

            while self.running:

                msg = (
                    f"{PING_PREFIX}"
                    f"{self.p2p_port}"
                )

                try:
                    client_socket.sendto(
                        msg.encode("utf-8"),
                        (
                            MULTICAST_GROUP,
                            MULTICAST_PORT,
                        ),
                    )

                except OSError as exc:
                    if self.running:
                        print(
                            "[Descoberta] Erro ao enviar "
                            f"PING: {exc}"
                        )

                except Exception as exc:
                    if self.running:
                        print(
                            "[Descoberta] Erro inesperado "
                            f"ao transmitir: {exc}"
                        )

                # --------------------------------------------
                # Espera interrompível.
                # --------------------------------------------

                end_time = (
                    time.monotonic()
                    + self.discovery_interval
                )

                while self.running:

                    remaining = (
                        end_time
                        - time.monotonic()
                    )

                    if remaining <= 0:
                        break

                    time.sleep(
                        min(0.25, remaining)
                    )

        finally:

            try:
                client_socket.close()
            except Exception:
                pass

            if self._broadcast_socket is client_socket:
                self._broadcast_socket = None

            print(
                "[Descoberta] Transmissor multicast encerrado."
            )

    # ========================================================
    # INICIALIZAÇÃO
    # ========================================================

    def run(self):
        """
        Inicia servidor e transmissor de descoberta.

        A função retorna imediatamente.
        """

        with self._state_lock:

            if self.running:
                print(
                    "[Descoberta] "
                    "Serviço já está em execução."
                )
                return

            self.running = True

            # Atualiza IP local antes de iniciar.
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

        print(
            "=============================================="
        )
        print(
            "   BRN AUTO NODE DISCOVERY INICIADO"
        )
        print(
            "=============================================="
        )
        print(
            f"IP local : {self.local_ip}"
        )
        print(
            f"Porta P2P: {self.p2p_port}"
        )
        print(
            f"Multicast: "
            f"{MULTICAST_GROUP}:{MULTICAST_PORT}"
        )
        print(
            "=============================================="
        )

    # ========================================================
    # PARADA
    # ========================================================

    def stop(self):
        """
        Encerra o serviço de descoberta.
        """

        with self._state_lock:

            if not self.running:
                return

            print(
                "[Descoberta] Encerrando serviço..."
            )

            self.running = False

            # Fecha o socket do servidor para acordar
            # recvfrom() imediatamente.
            if self._server_socket is not None:
                try:
                    self._server_socket.close()
                except Exception:
                    pass

            # Fecha socket de transmissão.
            if self._broadcast_socket is not None:
                try:
                    self._broadcast_socket.close()
                except Exception:
                    pass

        # --------------------------------------------
        # Aguarda as threads terminarem.
        # --------------------------------------------

        current_thread = threading.current_thread()

        threads = (
            self._server_thread,
            self._broadcast_thread,
        )

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

        print(
            "[Descoberta] Serviço encerrado."
        )

    # ========================================================
    # STATUS
    # ========================================================

    def is_running(self) -> bool:
        """
        Retorna True se a descoberta estiver ativa.
        """

        return self.running

    def get_local_endpoint(self) -> str:
        """
        Retorna o endpoint local anunciado pelo nó.

        Exemplo:
            192.168.1.100:6001
        """

        return f"{self.local_ip}:{self.p2p_port}"

    def get_status(self) -> dict:
        """
        Retorna informações do serviço.
        """

        return {
            "running": self.running,
            "local_ip": self.local_ip,
            "p2p_port": self.p2p_port,
            "local_endpoint": self.get_local_endpoint(),
            "multicast_group": MULTICAST_GROUP,
            "multicast_port": MULTICAST_PORT,
            "discovery_interval": self.discovery_interval,
            "peers": self.get_discovered_peers(),
            "peer_count": len(
                self.get_discovered_peers()
            ),
        }


# ============================================================
# TESTE ISOLADO
# ============================================================

def main():
    """
    Teste isolado do sistema de descoberta.
    """

    discovery = AutoNodeDiscovery(
        p2p_port=7777
    )

    try:

        discovery.run()

        print(
            "\n[Descoberta] Pressione CTRL+C "
            "para encerrar.\n"
        )

        while discovery.is_running():

            time.sleep(2)

            peers = (
                discovery.get_discovered_peers()
            )

            if peers:
                print(
                    f"[Descoberta] "
                    f"Peers encontrados: {peers}"
                )

    except KeyboardInterrupt:

        print(
            "\n[Descoberta] "
            "Interrupção solicitada pelo usuário."
        )

    finally:

        discovery.stop()


# ============================================================
# EXECUÇÃO DIRETA
# ============================================================

if __name__ == "__main__":
    main()