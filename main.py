"""
main.py — BRN-Estável

Pipeline principal:
    L2 Ledger
        ↓
    Merkle Root
        ↓
    Ancoragem Bitcoin
        ↓
    Lote processado

A recompensa de mineração da blockchain BRN é definida em
bruno_blockchain_real.py como 1.0 BRN por bloco.

Este arquivo não altera a recompensa de mineração.
"""

import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List


# ============================================================
# CONFIGURAÇÃO
# ============================================================

NETWORK_NAME = "BRN-L2"
ASSET_NAME = "BRN-Estavel"
ASSET_SYMBOL = "BRN"

# Recompensa oficial da mineração.
# A regra efetiva da blockchain está em bruno_blockchain_real.py.
MINING_REWARD = 1.0

PEG_USD = 1.00


# ============================================================
# 1. GERENCIADOR DO LEDGER — L2
# ============================================================

class LedgerManager:

    def __init__(
        self,
        data_folder: str = "data"
    ):
        self.data_folder = data_folder

        self.ledger_path = os.path.join(
            data_folder,
            "ledger.json"
        )

        self.reserve_path = os.path.join(
            data_folder,
            "collateral_reserve.json"
        )

        self._ensure_files_exist()


    # --------------------------------------------------------
    # GARANTE ARQUIVOS
    # --------------------------------------------------------

    def _ensure_files_exist(self):

        os.makedirs(
            self.data_folder,
            exist_ok=True
        )

        # ----------------------------------------------------
        # LEDGER
        # ----------------------------------------------------

        if not os.path.exists(
            self.ledger_path
        ):

            initial_data = {
                "network": NETWORK_NAME,
                "pending_transactions": [
                    {
                        "tx_id": "tx001",
                        "sender": "Alice",
                        "receiver": "Bob",
                        "amount": 100.0,
                        "status": "pending"
                    },
                    {
                        "tx_id": "tx002",
                        "sender": "Bob",
                        "receiver": "Charlie",
                        "amount": 25.5,
                        "status": "pending"
                    }
                ],
                "processed_batches": []
            }

            self.save_json(
                self.ledger_path,
                initial_data
            )


        # ----------------------------------------------------
        # RESERVA
        # ----------------------------------------------------

        if not os.path.exists(
            self.reserve_path
        ):

            initial_reserve = {
                "asset": ASSET_NAME,
                "symbol": ASSET_SYMBOL,
                "peg_usd": PEG_USD,
                "total_supply": 1000.0,
                "collateral_usd": 1000.0
            }

            self.save_json(
                self.reserve_path,
                initial_reserve
            )


    # --------------------------------------------------------
    # LEITURA JSON
    # --------------------------------------------------------

    def load_json(
        self,
        path: str
    ) -> Dict[str, Any]:

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)


    # --------------------------------------------------------
    # GRAVAÇÃO JSON
    # --------------------------------------------------------

    def save_json(
        self,
        path: str,
        data: Dict[str, Any]
    ):

        temporary_path = (
            f"{path}.tmp"
        )

        with open(
            temporary_path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4,
                ensure_ascii=False
            )

        os.replace(
            temporary_path,
            path
        )


    # --------------------------------------------------------
    # TRANSAÇÕES PENDENTES
    # --------------------------------------------------------

    def get_pending_transactions(
        self
    ) -> List[Dict[str, Any]]:

        data = self.load_json(
            self.ledger_path
        )

        return data.get(
            "pending_transactions",
            []
        )


    # --------------------------------------------------------
    # ANCORAR LOTE
    # --------------------------------------------------------

    def mark_transactions_as_anchored(
        self,
        batch_id: str,
        merkle_root: str,
        btc_txid: str
    ):

        data = self.load_json(
            self.ledger_path
        )

        pending = data.get(
            "pending_transactions",
            []
        )

        if not pending:
            return False


        batch_record = {
            "batch_id": batch_id,
            "merkle_root": merkle_root,
            "bitcoin_txid": btc_txid,
            "tx_count": len(pending),
            "transactions": pending,
            "timestamp": time.time()
        }


        processed_batches = data.get(
            "processed_batches",
            []
        )

        processed_batches.append(
            batch_record
        )

        data["processed_batches"] = (
            processed_batches
        )

        data["pending_transactions"] = []


        self.save_json(
            self.ledger_path,
            data
        )

        return True


    # --------------------------------------------------------
    # RESERVA
    # --------------------------------------------------------

    def get_reserve(
        self
    ) -> Dict[str, Any]:

        return self.load_json(
            self.reserve_path
        )


# ============================================================
# 2. SEQUENCIADOR DO ROLLUP
# ============================================================

class RollupSequencer:

    @staticmethod
    def calculate_merkle_root(
        transactions: list
    ) -> str:

        if not transactions:
            return ""


        hashes = [

            hashlib.sha256(
                json.dumps(
                    tx,
                    sort_keys=True
                ).encode("utf-8")
            ).hexdigest()

            for tx in transactions
        ]


        while len(hashes) > 1:

            # ------------------------------------------------
            # DUPLICA O ÚLTIMO HASH SE ÍMPAR
            # ------------------------------------------------

            if len(hashes) % 2 != 0:

                hashes.append(
                    hashes[-1]
                )


            new_level = []


            for i in range(
                0,
                len(hashes),
                2
            ):

                combined = (
                    hashes[i]
                    + hashes[i + 1]
                )

                new_level.append(
                    hashlib.sha256(
                        combined.encode(
                            "utf-8"
                        )
                    ).hexdigest()
                )


            hashes = new_level


        return hashes[0]


# ============================================================
# 3. ANCORAGEM NO BITCOIN
# ============================================================

class BitcoinAnchor:

    def __init__(
        self,
        network: str = "testnet"
    ):

        self.network = network


    # --------------------------------------------------------
    # OP_RETURN
    # --------------------------------------------------------

    def build_op_return_data(
        self,
        merkle_root: str
    ) -> str:

        return (
            f"BRN:{merkle_root[:32]}"
        )


    # --------------------------------------------------------
    # ANCORAGEM
    # --------------------------------------------------------

    def anchor_to_bitcoin(
        self,
        merkle_root: str,
        wallet_name: str = "main_wallet"
    ) -> str:

        payload = (
            self.build_op_return_data(
                merkle_root
            )
        )


        try:

            from bitcoinlib.wallets import Wallet

            wallet = Wallet(
                wallet_name
            )

            tx = wallet.send_to(
                outputs=[
                    (None, 0)
                ],
                data=payload.encode(
                    "utf-8"
                ),
                fee=1000
            )

            return tx.txid


        except Exception:

            # ------------------------------------------------
            # MODO SIMULAÇÃO
            # ------------------------------------------------

            simulated_txid = (
                hashlib.sha256(
                    f"{payload}{time.time()}".encode()
                ).hexdigest()
            )


            print(
                "[Aviso] Executando em modo "
                "simulação (sem conexão RPC "
                "ativa com o Bitcoin)."
            )

            print(
                "[BTC Anchor] Payload "
                f"OP_RETURN preparado: {payload}"
            )


            return simulated_txid


# ============================================================
# 4. INFORMAÇÕES DO SISTEMA
# ============================================================

def print_system_info():

    print(
        "=================================================="
    )

    print(
        "             BRN-ESTAVEL"
    )

    print(
        "=================================================="
    )

    print(
        f"Rede:                  {NETWORK_NAME}"
    )

    print(
        f"Ativo:                 {ASSET_NAME}"
    )

    print(
        f"Símbolo:               {ASSET_SYMBOL}"
    )

    print(
        f"Peg:                   ${PEG_USD:.2f}"
    )

    print(
        f"Recompensa mineração:  "
        f"{MINING_REWARD:.8f} {ASSET_SYMBOL}"
    )

    print(
        "=================================================="
    )


# ============================================================
# 5. PIPELINE PRINCIPAL
# ============================================================

def run_pipeline():

    print_system_info()

    print(
        "\n      ENGINE DE ROLLUP"
    )

    print(
        "=================================================="
    )


    # --------------------------------------------------------
    # LEDGER
    # --------------------------------------------------------

    ledger = LedgerManager()


    # --------------------------------------------------------
    # TRANSAÇÕES PENDENTES
    # --------------------------------------------------------

    pending_txs = (
        ledger.get_pending_transactions()
    )


    if not pending_txs:

        print(
            "[!] Nenhuma transação pendente "
            "encontrada no ledger.json."
        )

        return


    print(
        "[+] Transações pendentes carregadas: "
        f"{len(pending_txs)} item(ns)."
    )


    # --------------------------------------------------------
    # MERKLE ROOT
    # --------------------------------------------------------

    merkle_root = (
        RollupSequencer.calculate_merkle_root(
            pending_txs
        )
    )


    print(
        "[+] Raiz de Merkle calculada "
        "para o Rollup:"
    )

    print(
        f"    -> Merkle Root: {merkle_root}"
    )


    # --------------------------------------------------------
    # BITCOIN
    # --------------------------------------------------------

    print(
        "\n[+] Ancorando o estado no Bitcoin..."
    )


    anchor = BitcoinAnchor(
        network="testnet"
    )


    btc_txid = (
        anchor.anchor_to_bitcoin(
            merkle_root
        )
    )


    print(
        f"    -> Bitcoin TXID: {btc_txid}"
    )


    # --------------------------------------------------------
    # FINALIZA LOTE
    # --------------------------------------------------------

    batch_id = (
        f"batch_{str(uuid.uuid4())[:8]}"
    )


    success = (
        ledger.mark_transactions_as_anchored(
            batch_id,
            merkle_root,
            btc_txid
        )
    )


    if not success:

        print(
            "\n[!] Não foi possível finalizar "
            "o lote: não existem transações "
            "pendentes."
        )

        return


    print(
        "\n[+] Sucesso!"
    )

    print(
        f"    Lote: {batch_id}"
    )

    print(
        "    ledger.json atualizado."
    )

    print(
        f"    Recompensa de mineração: "
        f"{MINING_REWARD:.8f} {ASSET_SYMBOL}"
    )


# ============================================================
# ENTRADA DO PROGRAMA
# ============================================================

def main():

    try:

        run_pipeline()

    except KeyboardInterrupt:

        print(
            "\n[!] Operação interrompida pelo usuário."
        )

    except Exception as e:

        print(
            "\n[ERRO] Falha na execução:"
        )

        print(
            f"       {e}"
        )

        raise


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    main()