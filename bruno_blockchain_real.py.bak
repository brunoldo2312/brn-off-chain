import hashlib
import time
import json
import sqlite3
import secrets
import socket
import threading
import sys

import webview

from cripto_wallet import WalletManager
from cripto_db import BlockchainDB
# CORRIGIDO: AutoPortForwarder não existe no projeto — import comentado
# from cripto_p2p_network import AutoPortForwarder


# ============================================================
# CONFIGURAÇÃO DA MOEDA
# ============================================================

COIN_NAME = "Bruno"
COIN_SYMBOL = "BRN"

# ============================================================
# RECOMPENSA DE MINERAÇÃO
# ============================================================
# Recompensa FIXA por bloco minerado.
#
# IMPORTANTE:
# Não usar variável de ambiente para alterar este valor.
# A recompensa da mineração é sempre exatamente 1 BRN.
#
BLOCK_REWARD = 1.0


# ============================================================
# CONFIGURAÇÃO DA MINERAÇÃO
# ============================================================

DIFFICULTY_ADJUSTMENT_INTERVAL = 5
TARGET_BLOCK_TIME = 10.0


# ============================================================
# IDENTIFICAÇÃO DA REDE
# ============================================================

MONERO_FORK_NETWORK_ID = [
    0xAA,
    0xBB,
    0xCC,
    0xDD,
    0x11,
    0x22,
    0x33,
    0x44
]


# ============================================================
# PEERS INICIAIS
# ============================================================

BOOTSTRAP_PEERS = [
    ("192.168.0.17", 6001)
]


# ============================================================
# BLOCO
# ============================================================

class BrunoBlock:

    def __init__(
        self,
        index,
        previous_hash,
        transactions,
        difficulty=4,
        nonce=0,
        timestamp=None,
        block_hash=None
    ):
        self.index = int(index)

        self.timestamp = (
            float(timestamp)
            if timestamp is not None
            else time.time()
        )

        self.previous_hash = str(previous_hash)

        if isinstance(transactions, list):
            self.transactions = transactions
        else:
            self.transactions = json.loads(transactions)

        self.difficulty = int(difficulty)
        self.nonce = int(nonce)

        self.network_id = MONERO_FORK_NETWORK_ID

        if block_hash:
            self.hash = str(block_hash)
        else:
            self.hash = self.calculate_hash()


    # --------------------------------------------------------
    # HASH
    # --------------------------------------------------------

    def calculate_hash(self) -> str:

        block_string = json.dumps(
            {
                "index": self.index,
                "timestamp": self.timestamp,
                "previous_hash": self.previous_hash,
                "transactions": self.transactions,
                "difficulty": self.difficulty,
                "nonce": self.nonce,
                "network_id": self.network_id
            },
            sort_keys=True
        ).encode()

        return hashlib.sha256(block_string).hexdigest()


    # --------------------------------------------------------
    # PROVA DE TRABALHO
    # --------------------------------------------------------

    def mine_block(self, stop_event=None):

        target = "0" * self.difficulty

        while self.hash[:self.difficulty] != target:

            if stop_event and stop_event.is_set():
                return False

            self.nonce += 1
            self.hash = self.calculate_hash()

        return True


    # --------------------------------------------------------
    # CONVERSÃO PARA DICIONÁRIO
    # --------------------------------------------------------

    def to_dict(self):

        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "difficulty": self.difficulty,
            "nonce": self.nonce,
            "hash": self.hash
        }


# ============================================================
# API PRINCIPAL
# ============================================================

class CriptoAPI:

    def __init__(self, node_port):

        self.p2p_port = int(node_port)

        self.db_path = (
            f"blockchain_node_{self.p2p_port}.db"
        )

        self.db = BlockchainDB(self.db_path)

        # ----------------------------------------------------
        # MEMPOOL
        # ----------------------------------------------------

        self.mempool = []

        self.mempool_lock = threading.Lock()

        # ----------------------------------------------------
        # PEERS
        # ----------------------------------------------------

        self.connected_peers = set()

        # ----------------------------------------------------
        # MINERAÇÃO CONTÍNUA
        # ----------------------------------------------------

        self.is_mining = False

        self.mining_stop_event = threading.Event()

        self.miner_thread = None

        # ----------------------------------------------------
        # BANCO
        # ----------------------------------------------------

        self._init_database()

        # ----------------------------------------------------
        # SERVIDOR P2P
        # ----------------------------------------------------

        self.server_thread = threading.Thread(
            target=self._start_p2p_server,
            daemon=True
        )

        self.server_thread.start()

        # ----------------------------------------------------
        # DESCOBERTA DE PEERS
        # ----------------------------------------------------

        threading.Thread(
            target=self._run_bootstrap_discovery,
            daemon=True
        ).start()


    # ========================================================
    # BANCO DE DADOS / GÊNESIS
    # ========================================================

    def _init_database(self):

        with sqlite3.connect(self.db_path) as conn:

            cursor = conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    id_index INTEGER PRIMARY KEY,
                    timestamp REAL,
                    previous_hash TEXT,
                    transactions TEXT,
                    difficulty INTEGER,
                    nonce INTEGER,
                    hash TEXT
                )
                """
            )

            cursor.execute(
                "SELECT COUNT(*) FROM blocks"
            )

            count = cursor.fetchone()[0]

            if count == 0:

                genesis = BrunoBlock(
                    0,
                    "0",
                    [
                        {
                            "sender": "SISTEMA",
                            "receiver": (
                                "brn1111cd943fa71e1f91dcd62f52fc6138bc845ab"
                            ),
                            "amount": 100000.0
                        }
                    ],
                    difficulty=4
                )

                genesis.mine_block()

                self.db.insert_block(genesis)


    # ========================================================
    # PORTA P2P
    # ========================================================

    def get_p2p_port(self):

        return self.p2p_port


    # ========================================================
    # AJUSTE DE DIFICULDADE
    # ========================================================

    def _calculate_next_difficulty(self) -> int:

        chain = self.db.get_raw_chain()

        if not chain:
            return 4

        if len(chain) < DIFFICULTY_ADJUSTMENT_INTERVAL + 1:
            return chain[-1]["difficulty"]

        latest_block = chain[-1]

        if (
            latest_block["index"]
            % DIFFICULTY_ADJUSTMENT_INTERVAL
            != 0
        ):
            return latest_block["difficulty"]

        prev_adjustment_block = chain[
            -DIFFICULTY_ADJUSTMENT_INTERVAL
        ]

        time_expected = (
            TARGET_BLOCK_TIME
            * DIFFICULTY_ADJUSTMENT_INTERVAL
        )

        time_taken = (
            latest_block["timestamp"]
            - prev_adjustment_block["timestamp"]
        )

        current_diff = latest_block["difficulty"]

        if time_taken < (time_expected / 2):

            return current_diff + 1

        elif time_taken > (time_expected * 2):

            return max(
                1,
                current_diff - 1
            )

        return current_diff


    # ========================================================
    # RECEBIMENTO DE DADOS P2P
    # ========================================================

    def _receive_all(
        self,
        sock,
        buffer_size=4096,
        max_size=10 * 1024 * 1024
    ):

        data = b""

        try:

            while True:

                chunk = sock.recv(buffer_size)

                if not chunk:
                    break

                data += chunk

                if len(data) > max_size:

                    raise ValueError(
                        "Mensagem excede tamanho maximo "
                        f"de {max_size} bytes"
                    )

        except socket.timeout:

            pass

        except Exception as e:

            print(
                f"Erro ao receber dados: {e}"
            )

        return data.decode(
            "utf-8",
            errors="ignore"
        )


    # ========================================================
    # SERVIDOR P2P
    # ========================================================

    def _start_p2p_server(self):

        server_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )

        server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        server_socket.bind(
            ("0.0.0.0", self.p2p_port)
        )

        server_socket.listen(10)

        print(
            f"[P2P] Servidor iniciado na porta "
            f"{self.p2p_port}"
        )

        while True:

            client_conn = None

            try:

                client_conn, client_addr = (
                    server_socket.accept()
                )

                client_conn.settimeout(5.0)

                data = self._receive_all(
                    client_conn
                )

                # --------------------------------------------
                # REGISTRA PEER
                # --------------------------------------------

                if client_addr[0] != "127.0.0.1":

                    self.connected_peers.add(
                        (
                            client_addr[0],
                            self.p2p_port
                        )
                    )

                # --------------------------------------------
                # ALTURA
                # --------------------------------------------

                if data == "GET_HEIGHT":

                    chain = self.db.get_raw_chain()

                    client_conn.sendall(
                        str(len(chain)).encode("utf-8")
                    )

                # --------------------------------------------
                # BLOCKCHAIN
                # --------------------------------------------

                elif data == "GET_CHAIN":

                    chain = self.db.get_raw_chain()

                    client_conn.sendall(
                        json.dumps(chain).encode("utf-8")
                    )

                # --------------------------------------------
                # TRANSAÇÃO
                # --------------------------------------------

                elif data.startswith(
                    "BROADCAST_TX:"
                ):

                    tx_data = json.loads(
                        data.split(":", 1)[1]
                    )

                    if self._verify_tx_structure(
                        tx_data
                    ):

                        with self.mempool_lock:

                            if tx_data not in self.mempool:

                                self.mempool.append(
                                    tx_data
                                )

                # --------------------------------------------
                # SINCRONIZAÇÃO
                # --------------------------------------------

                elif data.startswith(
                    "SYNC_CHAIN:"
                ):

                    incoming_chain = json.loads(
                        data.split(":", 1)[1]
                    )

                    self._resolve_consensus(
                        incoming_chain
                    )

            except Exception:

                pass

            finally:

                if client_conn:

                    try:
                        client_conn.close()
                    except Exception:
                        pass


    # ========================================================
    # DESCOBERTA DE PEERS
    # ========================================================

    def _run_bootstrap_discovery(self):

        time.sleep(2)

        for ip, port in BOOTSTRAP_PEERS:

            if port == self.p2p_port:
                continue

            try:

                self.connect_and_sync(
                    ip,
                    port
                )

            except Exception:

                pass


    # ========================================================
    # BROADCAST DE TRANSAÇÃO
    # ========================================================

    def _broadcast_transaction_to_network(
        self,
        tx
    ):

        for ip, port in list(
            self.connected_peers
        ):

            try:

                s = socket.socket(
                    socket.AF_INET,
                    socket.SOCK_STREAM
                )

                s.settimeout(2.0)

                s.connect(
                    (
                        ip,
                        int(port)
                    )
                )

                payload = (
                    "BROADCAST_TX:"
                    + json.dumps(tx)
                )

                s.sendall(
                    payload.encode("utf-8")
                )

                s.close()

            except Exception:

                self.connected_peers.discard(
                    (ip, port)
                )


    # ========================================================
    # CONEXÃO E SINCRONIZAÇÃO
    # ========================================================

    def connect_and_sync(
        self,
        ip,
        port
    ):

        try:

            # --------------------------------------------
            # OBTÉM ALTURA REMOTA
            # --------------------------------------------

            s_height = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            s_height.settimeout(3.0)

            s_height.connect(
                (
                    ip,
                    int(port)
                )
            )

            s_height.sendall(
                b"GET_HEIGHT"
            )

            remote_height = int(
                s_height.recv(1024)
                .decode("utf-8")
            )

            s_height.close()

            local_chain = (
                self.db.get_raw_chain()
            )

            if remote_height <= len(local_chain):

                return {
                    "status": "sucesso",
                    "message": (
                        "Sua blockchain ja esta atualizada."
                    )
                }

            # --------------------------------------------
            # OBTÉM BLOCKCHAIN REMOTA
            # --------------------------------------------

            client_socket = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            client_socket.settimeout(10.0)

            client_socket.connect(
                (
                    ip,
                    int(port)
                )
            )

            client_socket.sendall(
                b"GET_CHAIN"
            )

            response = self._receive_all(
                client_socket
            )

            client_socket.close()

            self.connected_peers.add(
                (
                    ip,
                    int(port)
                )
            )

            remote_chain = json.loads(
                response
            )

            return {
                "status": "sucesso",
                "message": self._resolve_consensus(
                    remote_chain
                )
            }

        except Exception as e:

            return {
                "status": "erro",
                "message": str(e)
            }


    # ========================================================
    # VALIDAÇÃO DE ENDEREÇO
    # ========================================================

    def _validate_address(
        self,
        address
    ):

        if (
            not isinstance(address, str)
            or not address.startswith("brn1")
            or len(address) != 44
        ):

            raise ValueError(
                "Tamanho ou formato de endereco "
                f"invalido: {address}"
            )

        try:

            int(
                address[4:],
                16
            )

        except ValueError:

            raise ValueError(
                "Endereco contem caracteres "
                "hexadecimais invalidos"
            )

        return True


    # ========================================================
    # VALIDAÇÃO DE TRANSAÇÃO
    # ========================================================

    def _verify_tx_structure(
        self,
        tx
    ):

        if not isinstance(tx, dict):

            return False

        # Transações de sistema são permitidas.
        if tx.get("sender") == "SISTEMA":

            return True

        required_keys = [
            "sender",
            "receiver",
            "amount",
            "public_key",
            "signature",
            "timestamp"
        ]

        if not all(
            key in tx
            for key in required_keys
        ):

            return False

        try:

            amount = float(
                tx["amount"]
            )

            if amount <= 0:
                return False

            self._validate_address(
                tx["sender"]
            )

            self._validate_address(
                tx["receiver"]
            )

            payload = {
                "sender": tx["sender"],
                "receiver": tx["receiver"],
                "amount": amount,
                "timestamp": tx["timestamp"]
            }

            return WalletManager.verify_signature(
                tx["public_key"],
                payload,
                tx["signature"]
            )

        except Exception:

            return False


    # ========================================================
    # VALIDAÇÃO DE BLOCO
    # ========================================================

    def _validate_block(
        self,
        block_data
    ):

        try:

            block = BrunoBlock(
                block_data["index"],
                block_data["previous_hash"],
                block_data["transactions"],
                block_data["difficulty"],
                block_data["nonce"],
                block_data["timestamp"],
                block_data["hash"]
            )

        except Exception as e:

            return (
                False,
                f"Bloco malformado: {e}"
            )

        # --------------------------------------------
        # VERIFICA HASH
        # --------------------------------------------

        if (
            block.calculate_hash()
            != block_data["hash"]
        ):

            return (
                False,
                "Hash nao corresponde"
            )

        # --------------------------------------------
        # VERIFICA PROVA DE TRABALHO
        # --------------------------------------------

        difficulty = int(
            block_data["difficulty"]
        )

        if difficulty < 1:

            return (
                False,
                "Dificuldade invalida"
            )

        target = "0" * difficulty

        if not block_data["hash"].startswith(
            target
        ):

            return (
                False,
                "Prova de Trabalho invalida"
            )

        # --------------------------------------------
        # VERIFICA TRANSAÇÕES
        # --------------------------------------------

        transactions = block_data[
            "transactions"
        ]

        if not isinstance(
            transactions,
            list
        ):

            return (
                False,
                "Lista de transacoes invalida"
            )

        for tx in transactions:

            if not self._verify_tx_structure(
                tx
            ):

                return (
                    False,
                    "Transacao invalida detectada "
                    f"no bloco: {tx}"
                )

        return True, "OK"


    # ========================================================
    # CONSENSO
    # ========================================================

    def _resolve_consensus(
        self,
        remote_chain
    ):

        local_chain = (
            self.db.get_raw_chain()
        )

        if len(remote_chain) <= len(local_chain):

            return (
                "Cadeia local ja e dominante."
            )

        if not remote_chain:

            return (
                "Cadeia remota vazia."
            )

        # --------------------------------------------
        # VERIFICA LIGAÇÃO DOS BLOCOS
        # --------------------------------------------

        for i in range(
            1,
            len(remote_chain)
        ):

            if (
                remote_chain[i]["previous_hash"]
                != remote_chain[i - 1]["hash"]
            ):

                return (
                    "Hashes corrompidos "
                    "na cadeia remota."
                )

        # --------------------------------------------
        # VALIDA TODOS OS BLOCOS
        # --------------------------------------------

        for block_data in remote_chain:

            is_valid, message = (
                self._validate_block(
                    block_data
                )
            )

            if not is_valid:

                return (
                    f"Bloco #{block_data['index']} "
                    f"rejeitado: {message}"
                )

        # --------------------------------------------
        # SUBSTITUI CADEIA
        # --------------------------------------------

        self.db.replace_chain(
            remote_chain
        )

        return (
            "Sincronizado com sucesso para "
            f"{len(remote_chain)} blocos."
        )


    # ========================================================
    # INFORMAÇÕES DA BLOCKCHAIN
    # ========================================================

    def get_full_chain(self):

        with self.mempool_lock:

            mempool_size = len(
                self.mempool
            )

        chain = (
            self.db.get_raw_chain()
        )

        return {
            "chain": chain,
            "length": len(chain),
            "mempool_size": mempool_size,
            "is_mining": self.is_mining,
            "current_difficulty": (
                chain[-1]["difficulty"]
                if chain
                else 4
            ),
            "block_reward": BLOCK_REWARD,
            "coin": COIN_SYMBOL
        }


    # ========================================================
    # GERAR CARTEIRA
    # ========================================================

    def generate_wallet(self):

        return WalletManager.generate_keypair()


    # ========================================================
    # CONSULTAR SALDO
    # ========================================================

    def get_balance(
        self,
        address
    ):

        try:

            self._validate_address(
                address
            )

            balance = 0.0

            chain = (
                self.db.get_raw_chain()
            )

            for block in chain:

                for tx in block["transactions"]:

                    if tx.get("sender") == address:

                        balance -= float(
                            tx.get("amount", 0)
                        )

                    if tx.get("receiver") == address:

                        balance += float(
                            tx.get("amount", 0)
                        )

            return {
                "address": address,
                "balance": balance
            }

        except ValueError as e:

            return {
                "status": "erro",
                "message": str(e)
            }


    # ========================================================
    # ENVIO DE FUNDOS
    # ========================================================

    def send_funds(
        self,
        sender,
        receiver,
        amount,
        spend_secret_key,
        public_key
    ):

        try:

            self._validate_address(
                sender
            )

            self._validate_address(
                receiver
            )

            amount = float(amount)

            if amount <= 0:

                return {
                    "status": "erro",
                    "message": "Quantia invalida."
                }

            sender_balance = (
                self.get_balance(sender)
                .get("balance", 0)
            )

            if sender_balance < amount:

                return {
                    "status": "erro",
                    "message": "Saldo insuficiente!"
                }

            tx_payload = {
                "sender": str(sender).strip(),
                "receiver": str(receiver).strip(),
                "amount": amount,
                "timestamp": time.time()
            }

            signature = (
                WalletManager.sign_transaction(
                    spend_secret_key,
                    tx_payload
                )
            )

            full_tx = {
                **tx_payload,
                "public_key": public_key,
                "signature": signature
            }

            # --------------------------------------------
            # MEMPOOL
            # --------------------------------------------

            with self.mempool_lock:

                if full_tx not in self.mempool:

                    self.mempool.append(
                        full_tx
                    )

            # --------------------------------------------
            # BROADCAST
            # --------------------------------------------

            threading.Thread(
                target=self._broadcast_transaction_to_network,
                args=(full_tx,),
                daemon=True
            ).start()

            return {
                "status": "sucesso",
                "message": (
                    "Transacao assinada e enviada "
                    "a mempool!"
                )
            }

        except Exception as e:

            return {
                "status": "erro",
                "message": str(e)
            }


    # ========================================================
    # CONTROLE DA MINERAÇÃO
    # ========================================================

    def toggle_continuous_mining(
        self,
        miner_address
    ):

        if self.is_mining:

            self.is_mining = False

            self.mining_stop_event.set()

            return {
                "status": "sucesso",
                "message": (
                    "Mineracao continua pausada."
                ),
                "is_mining": False
            }

        try:

            self._validate_address(
                miner_address
            )

            self.is_mining = True

            self.mining_stop_event.clear()

            self.miner_thread = threading.Thread(
                target=self._continuous_mining_loop,
                args=(miner_address,),
                daemon=True
            )

            self.miner_thread.start()

            return {
                "status": "sucesso",
                "message": (
                    "Mineracao continua iniciada!"
                ),
                "is_mining": True,
                "block_reward": BLOCK_REWARD
            }

        except Exception as e:

            return {
                "status": "erro",
                "message": str(e)
            }


    # ========================================================
    # LOOP DE MINERAÇÃO
    # ========================================================

    def _continuous_mining_loop(
        self,
        miner_address
    ):

        print(
            "⛏️ Loop de mineracao continua ativo "
            f"para: {miner_address}"
        )

        print(
            f"💰 Recompensa por bloco: "
            f"{BLOCK_REWARD:.8f} {COIN_SYMBOL}"
        )

        while (
            self.is_mining
            and not self.mining_stop_event.is_set()
        ):

            try:

                # ----------------------------------------
                # OBTÉM ÚLTIMO BLOCO
                # ----------------------------------------

                local_chain = (
                    self.db.get_raw_chain()
                )

                if not local_chain:

                    time.sleep(1)

                    continue

                last_block = local_chain[-1]

                # ----------------------------------------
                # CALCULA DIFICULDADE
                # ----------------------------------------

                next_difficulty = (
                    self._calculate_next_difficulty()
                )

                # ----------------------------------------
                # MONTA TRANSAÇÕES
                # ----------------------------------------
                #
                # A recompensa é SEMPRE:
                #
                # 1.0 BRN
                #
                # ----------------------------------------

                reward_transaction = {
                    "sender": "SISTEMA",
                    "receiver": str(
                        miner_address
                    ).strip(),
                    "amount": BLOCK_REWARD
                }

                with self.mempool_lock:

                    pending_transactions = list(
                        self.mempool
                    )

                block_transactions = [
                    reward_transaction
                ] + pending_transactions

                # ----------------------------------------
                # CRIA NOVO BLOCO
                # ----------------------------------------

                new_block = BrunoBlock(
                    last_block["index"] + 1,
                    last_block["hash"],
                    block_transactions,
                    difficulty=next_difficulty
                )

                # ----------------------------------------
                # MINERA
                # ----------------------------------------

                success = new_block.mine_block(
                    stop_event=self.mining_stop_event
                )

                if not success:

                    continue

                if not self.is_mining:

                    continue

                # ----------------------------------------
                # SALVA BLOCO
                # ----------------------------------------

                self.db.insert_block(
                    new_block
                )

                # ----------------------------------------
                # REMOVE TRANSAÇÕES MINERADAS
                # ----------------------------------------

                with self.mempool_lock:

                    mined_transactions = set(
                        json.dumps(
                            tx,
                            sort_keys=True
                        )
                        for tx in block_transactions
                        if tx.get("sender") != "SISTEMA"
                    )

                    self.mempool = [
                        tx
                        for tx in self.mempool
                        if json.dumps(
                            tx,
                            sort_keys=True
                        ) not in mined_transactions
                    ]

                # ----------------------------------------
                # ENVIA BLOCKCHAIN AOS PEERS
                # ----------------------------------------

                raw_chain_json = json.dumps(
                    self.db.get_raw_chain()
                )

                for ip, port in list(
                    self.connected_peers
                ):

                    try:

                        s = socket.socket(
                            socket.AF_INET,
                            socket.SOCK_STREAM
                        )

                        s.settimeout(3.0)

                        s.connect(
                            (
                                ip,
                                int(port)
                            )
                        )

                        payload = (
                            "SYNC_CHAIN:"
                            + raw_chain_json
                        )

                        s.sendall(
                            payload.encode("utf-8")
                        )

                        s.close()

                    except Exception:

                        pass

                # ----------------------------------------
                # LOG
                # ----------------------------------------

                print(
                    f"✅ Bloco #{new_block.index} "
                    f"minerado "
                    f"(Diff: {next_difficulty}) "
                    f"Recompensa: "
                    f"{BLOCK_REWARD:.8f} {COIN_SYMBOL} "
                    f"Hash: "
                    f"{new_block.hash[:16]}..."
                )

            except Exception as e:

                print(
                    "⚠️ Erro no loop de mineracao: "
                    f"{e}"
                )

                time.sleep(2)

        print(
            "🛑 Loop de mineracao continua desligado."
        )


# ============================================================
# EXECUÇÃO DIRETA
# ============================================================

if __name__ == "__main__":

    p2p_port = 6001

    if len(sys.argv) > 1:

        try:

            p2p_port = int(
                sys.argv[1]
            )

        except ValueError:

            pass

    # --------------------------------------------------------
    # TENTA CONFIGURAR PORTA NO ROTEADOR
    # --------------------------------------------------------
    # CORRIGIDO: AutoPortForwarder não existe no projeto —
    # a chamada foi comentada. UPnP é opcional; o nó funciona
    # sem ele (ideal para uso local / mesma rede).
    # --------------------------------------------------------

    # AutoPortForwarder.open_port_on_router(
    #     p2p_port
    # )

    # --------------------------------------------------------
    # INICIA API
    # --------------------------------------------------------

    api_local = CriptoAPI(
        p2p_port
    )

    # --------------------------------------------------------
    # INTERFACE
    # --------------------------------------------------------

    webview.create_window(
        title=(
            f"Carteira Nativa "
            f"{COIN_NAME} "
            f"(Porta: {p2p_port})"
        ),
        url="index.html",
        js_api=api_local,
        width=740,
        height=800,
        resizable=True
    )

    webview.start()