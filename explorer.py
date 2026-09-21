# explorer.py — Ponto de entrada principal.
# Sobe o nó completo (P2P + PoW + CLOB + MEV + ngrok) e imprime relatório.

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH         = os.environ.get("BRN_DB_PATH", "blockchain.db")
REPORT_INTERVAL = int(os.environ.get("BRN_REPORT_INTERVAL", "30"))


# ==================================================================
# Helpers
# ==================================================================
def short(addr: str) -> str:
    if not addr:
        return "—"
    return addr[:14] + "…" + addr[-8:] if len(addr) > 24 else addr


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def tx_summary(tx: dict) -> str:
    t = tx.get("type", "?")
    if t == "transfer":
        return (f"transfer {tx.get('amount', 0):.4f} "
                f"{tx.get('asset_id','?')} | "
                f"{short(tx['from'])} → {short(tx['to'])}")
    if t == "issue":
        return (f"issue    {tx.get('amount', 0):.4f} "
                f"{tx.get('asset_id','?')} → {short(tx['to'])}")
    if t == "redeem":
        return (f"redeem   {tx.get('amount', 0):.4f} "
                f"{tx.get('asset_id','?')} de {short(tx['from'])}")
    if t == "order_place":
        md = tx.get("metadata", {})
        return (f"order    {md.get('side','?').upper():>4} "
                f"{tx.get('amount',0):.4f} {tx.get('asset_id','?')} @ "
                f"{md.get('price',0):.6f} {md.get('quote','?')}")
    if t == "order_commit":
        md = tx.get("metadata", {})
        return (f"commit   {md.get('side','?').upper():>4} "
                f"hash={md.get('commit_hash','')[:12]}…")
    if t == "order_reveal":
        md = tx.get("metadata", {})
        return (f"reveal   {md.get('side','?').upper():>4} "
                f"{tx.get('amount',0):.4f} {tx.get('asset_id','?')} @ "
                f"{md.get('price',0):.6f} {md.get('quote','?')}")
    if t == "order_cancel":
        md = tx.get("metadata", {})
        return f"cancel   order={md.get('order_id','')[:12]}…"
    if t == "asset_create":
        md = tx.get("metadata", {})
        return f"asset    create {md.get('asset_id','?')}"
    if t in ("kyc_register", "kyc_revoke"):
        return f"{t:<8} {short(tx.get('to',''))}"
    return f"{t} | {short(tx.get('from',''))} → {short(tx.get('to',''))}"


# ==================================================================
# Relatório
# ==================================================================
def print_report():
    if not Path(DB_PATH).exists():
        print(f"[explorer] {DB_PATH} ainda não existe.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:
        print(f"[explorer] erro ao abrir {DB_PATH}: {e}")
        return

    print("\n" + "=" * 100)
    print(f" EXPLORER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # ----- BLOCOS -----
    rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
    total_tx = 0
    total_vol = 0.0
    print(f"\n BLOCOS ({len(rows)}):")
    for (data,) in rows:
        blk = json.loads(data)
        txs = blk.get("transactions", [])
        total_tx += len(txs)
        miner = blk.get("miner") or blk.get("validator") or ""
        diff = blk.get("difficulty", "-")
        nonce = blk.get("nonce", "-")
        print(f"  #{blk['index']:>4} | {fmt_time(blk['timestamp'])} | "
              f"miner={short(miner):22} | diff={diff} nonce={nonce} | "
              f"{len(txs)} tx | {blk.get('hash','')[:16]}…")
        for tx in txs:
            total_vol += tx.get("amount", 0.0) or 0.0
            print(f"          → {tx_summary(tx)}")

    # ----- MEMPOOL -----
    mrows = conn.execute("SELECT data FROM mempool ORDER BY ts ASC").fetchall()
    print(f"\n MEMPOOL ({len(mrows)} pendentes):")
    if not mrows:
        print("   (vazia)")
    for (data,) in mrows:
        tx = json.loads(data)
        print(f"   {tx_summary(tx)} | nonce={tx.get('nonce','?')}")

    # ----- SLASHING -----
    srows = conn.execute(
        "SELECT validator, reason, block_idx, ts FROM slashing ORDER BY ts DESC"
    ).fetchall()
    print(f"\n SLASHING ({len(srows)} banidos):")
    if not srows:
        print("   (nenhum)")
    for r in srows:
        print(f"   {short(r[0])} | bloco#{r[2]} | {r[1]} | {fmt_time(r[3])}")

    print("\n" + "-" * 100)
    print(f" Total de blocos ....: {len(rows)}")
    print(f" Total de tx ........: {total_tx}")
    print(f" Volume confirmado ..: {total_vol:.4f}")
    print(f" Pendentes ..........: {len(mrows)}")
    print(f" Slashed ............: {len(srows)}")
    print("-" * 100)


# ==================================================================
# Loop de relatório
# ==================================================================
def report_loop():
    time.sleep(5)
    while True:
        try:
            print_report()
        except Exception as e:
            print(f"[explorer] erro no relatório: {e}")
        time.sleep(REPORT_INTERVAL)


# ==================================================================
# Main
# ==================================================================
def main():
    print("=" * 72)
    print(" BRN Explorer — iniciando nó completo")
    print("=" * 72)

    from node import Node

    node = Node()
    t_node = threading.Thread(target=node.start, daemon=True, name="node")
    t_node.start()

    t_report = threading.Thread(target=report_loop, daemon=True, name="report")
    t_report.start()

    # espera ngrok (até 10s)
    if os.environ.get("BRN_NGROK", "0") == "1":
        for _ in range(50):
            if node.public_endpoint:
                break
            time.sleep(0.2)

    time.sleep(1)
    print("\n" + "=" * 72)
    if node.public_endpoint:
        h, p = node.public_endpoint
        print(f" NGROK público: {h}:{p}")
        print(f" Peers devem usar:")
        print(f"   BRN_HARDCODED_SEEDS={h}:{p}")
    else:
        print(" NGROK: inativo ou ainda subindo.")
    print(f" Banco SQLite: {os.path.abspath(DB_PATH)}")
    print(f" Relatório a cada {REPORT_INTERVAL}s.")
    print(" Pressione Ctrl+C para encerrar.")
    print("=" * 72 + "\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[explorer] encerrando…")
        node.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()