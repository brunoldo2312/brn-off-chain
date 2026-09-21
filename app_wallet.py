"""
Carteira gráfica BRN — Tkinter.
Mostra: portfolio, order book, minhas ordens, trades.
Permite: comprar, vender, cancelar.
Roda o nó P2P em thread separada.
"""
import asyncio
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from bruno_blockchain_real import Blockchain, NATIVE_ASSET
from p2p import PeerManager

try:
    from cripto_wallet import WalletManager
except ImportError:
    WalletManager = None

PORT = int(os.environ.get("BRN_P2P_PORT", "6001"))
MINER_INTERVAL = float(os.environ.get("BRN_MINER_INTERVAL", "0.5"))
MINER_ENABLED = os.environ.get("BRN_MINE", "1") == "1"
DEFAULT_BASE = os.environ.get("BRN_BASE", "BRN")
DEFAULT_QUOTE = os.environ.get("BRN_QUOTE", "USDC")


def load_identity():
    if WalletManager is None:
        raise RuntimeError("cripto_wallet indisponivel.")
    if hasattr(WalletManager, "load_node_identity"):
        return WalletManager.load_node_identity()
    sk = os.environ.get("BRN_NODE_SK"); pk = os.environ.get("BRN_NODE_PK")
    if not sk or not pk:
        if hasattr(WalletManager, "generate_keypair"):
            sk, pk = WalletManager.generate_keypair()
        else:
            raise RuntimeError("WalletManager sem generate_keypair.")
    addr = WalletManager.address_from_public_key(pk)
    return {"address": addr, "public_key": pk, "spend_secret_key": sk}


# =====================================================================
# Nó rodando em background
# =====================================================================
class NodeRunner:
    def __init__(self, chain, pm):
        self.chain = chain
        self.pm = pm
        self.loop = None
        self.stop_event = None
        self.thread = None

    def start(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:
            print(f"[node] erro: {e}")

    async def _main(self):
        async def miner():
            if not MINER_ENABLED: return
            loop = asyncio.get_event_loop()
            while not self.stop_event.is_set():
                block = await loop.run_in_executor(None, self.chain.produce_block)
                if block is None:
                    await asyncio.sleep(1); continue
                try:
                    await self.pm.broadcast({
                        "type": "inv_block",
                        "height": block.index, "hash": block.hash})
                except Exception: pass
                await asyncio.sleep(MINER_INTERVAL)

        await asyncio.gather(self.pm.start(), miner())

    def stop(self):
        if self.stop_event: self.stop_event.set()
        if self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.pm.stop(), self.loop)
            except Exception: pass


# =====================================================================
# GUI
# =====================================================================
class WalletApp:
    def __init__(self, root, chain, identity):
        self.root = root
        self.chain = chain
        self.identity = identity
        self.base = DEFAULT_BASE
        self.quote = DEFAULT_QUOTE

        root.title(f"Carteira BRN — {identity['address'][:16]}...")
        root.geometry("1100x700")

        self._build_top()
        self._build_portfolio()
        self._build_orderbook()
        self._build_orders()
        self._build_trades()
        self._build_controls()

        self.refresh()

    # ---------------- layout ----------------
    def _build_top(self):
        f = ttk.Frame(self.root, padding=8)
        f.pack(fill="x")
        ttk.Label(f, text="Endereço:", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(f, text=self.identity["address"], foreground="#004488").pack(side="left", padx=6)
        self.lbl_status = ttk.Label(f, text="altura=0", foreground="#666")
        self.lbl_status.pack(side="right")

    def _build_portfolio(self):
        f = ttk.LabelFrame(self.root, text="Portfolio", padding=6)
        f.pack(fill="x", padx=8, pady=4)
        cols = ("asset", "amount", "available", "frozen")
        self.tv_portfolio = ttk.Treeview(f, columns=cols, show="headings", height=4)
        for c, w in zip(cols, (160, 140, 140, 140)):
            self.tv_portfolio.heading(c, text=c.capitalize())
            self.tv_portfolio.column(c, width=w, anchor="e")
        self.tv_portfolio.pack(fill="x")

    def _build_orderbook(self):
        f = ttk.LabelFrame(self.root, text=f"Order Book {self.base}/{self.quote}", padding=6)
        f.pack(fill="x", padx=8, pady=4)
        left = ttk.Frame(f); left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(f); right.pack(side="right", fill="both", expand=True)

        ttk.Label(left, text="ASKS (venda)", foreground="#a00").pack(anchor="w")
        self.tv_asks = ttk.Treeview(left, columns=("p","a","o"), show="headings", height=6)
        for c, w in zip(("p","a","o"), (100, 100, 130)):
            self.tv_asks.heading(c, text={"p":"Preço","a":"Qtd","o":"Dono"}[c])
            self.tv_asks.column(c, width=w, anchor="e")
        self.tv_asks.pack(fill="x")

        ttk.Label(right, text="BIDS (compra)", foreground="#080").pack(anchor="w")
        self.tv_bids = ttk.Treeview(right, columns=("p","a","o"), show="headings", height=6)
        for c, w in zip(("p","a","o"), (100, 100, 130)):
            self.tv_bids.heading(c, text={"p":"Preço","a":"Qtd","o":"Dono"}[c])
            self.tv_bids.column(c, width=w, anchor="e")
        self.tv_bids.pack(fill="x")

        self.lbl_book = ttk.Label(f, text="")
        self.lbl_book.pack(anchor="w", pady=(6,0))

    def _build_orders(self):
        f = ttk.LabelFrame(self.root, text="Minhas ordens abertas", padding=6)
        f.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("id","side","price","amount","filled","remaining","status")
        self.tv_orders = ttk.Treeview(f, columns=cols, show="headings", height=5)
        for c, w in zip(cols, (120, 60, 90, 90, 90, 90, 90)):
            self.tv_orders.heading(c, text=c.capitalize())
            self.tv_orders.column(c, width=w, anchor="center")
        self.tv_orders.pack(fill="both", expand=True)

    def _build_trades(self):
        f = ttk.LabelFrame(self.root, text="Meus trades", padding=6)
        f.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("time","side","price","amount","cost","pair")
        self.tv_trades = ttk.Treeview(f, columns=cols, show="headings", height=4)
        for c, w in zip(cols, (120, 60, 90, 90, 90, 100)):
            self.tv_trades.heading(c, text=c.capitalize())
            self.tv_trades.column(c, width=w, anchor="center")
        self.tv_trades.pack(fill="both", expand=True)

    def _build_controls(self):
        f = ttk.LabelFrame(self.root, text="Nova ordem", padding=6)
        f.pack(fill="x", padx=8, pady=4)

        ttk.Label(f, text="Preço:").grid(row=0, column=0, sticky="e")
        self.var_price = tk.StringVar(value="1.0")
        ttk.Entry(f, textvariable=self.var_price, width=12).grid(row=0, column=1)

        ttk.Label(f, text="Qtd:").grid(row=0, column=2, sticky="e")
        self.var_amount = tk.StringVar(value="10")
        ttk.Entry(f, textvariable=self.var_amount, width=12).grid(row=0, column=3)

        ttk.Button(f, text="COMPRAR", command=lambda: self._place("buy")).grid(row=0, column=4, padx=6)
        ttk.Button(f, text="VENDER",  command=lambda: self._place("sell")).grid(row=0, column=5, padx=6)
        ttk.Button(f, text="Cancelar selecionada", command=self._cancel_selected).grid(row=0, column=6, padx=6)
        ttk.Button(f, text="Atualizar", command=self.refresh).grid(row=0, column=7, padx=6)

    # ---------------- ações ----------------
    def _place(self, side):
        try:
            price = float(self.var_price.get())
            amount = float(self.var_amount.get())
        except ValueError:
            messagebox.showerror("Erro", "Preço/quantidade inválidos."); return
        if price <= 0 or amount <= 0:
            messagebox.showerror("Erro", "Valores devem ser positivos."); return
        r = self.chain.place_order(self.identity, self.base, self.quote,
                                    side, price, amount)
        if not r.get("ok"):
            messagebox.showerror("Ordem rejeitada", r.get("msg", "erro"))
        self.refresh()

    def _cancel_selected(self):
        sel = self.tv_orders.selection()
        if not sel:
            messagebox.showinfo("Cancelar", "Selecione uma ordem."); return
        oid = self.tv_orders.item(sel[0])["values"][0]
        r = self.chain.cancel_order(self.identity, oid)
        if not r.get("ok"):
            messagebox.showerror("Cancelar falhou", r.get("msg", "erro"))
        self.refresh()

    # ---------------- refresh ----------------
    def refresh(self):
        try:
            self._refresh_portfolio()
            self._refresh_book()
            self._refresh_orders()
            self._refresh_trades()
            self.lbl_status.config(
                text=f"altura={self.chain.height} | "
                     f"final={self.chain.finality.finalized_height} | "
                     f"mempool={len(self.chain.pending)}")
        except Exception as e:
            print(f"[gui] refresh erro: {e}")
        self.root.after(1500, self.refresh)

    def _refresh_portfolio(self):
        for i in self.tv_portfolio.get_children(): self.tv_portfolio.delete(i)
        pf = self.chain.portfolio(self.identity["address"])
        for asset, info in sorted(pf.items()):
            self.tv_portfolio.insert("", "end", values=(
                asset, f"{info['amount']:.6f}",
                f"{info['available']:.6f}", f"{info['frozen']:.6f}"))

    def _refresh_book(self):
        for i in self.tv_asks.get_children(): self.tv_asks.delete(i)
        for i in self.tv_bids.get_children(): self.tv_bids.delete(i)
        book = self.chain.order_book(self.base, self.quote, depth=8)
        for a in reversed(book["asks"]):
            self.tv_asks.insert("", "end", values=(
                f"{a['price']:.6f}", f"{a['amount']:.4f}", a["owner"]))
        for b in book["bids"]:
            self.tv_bids.insert("", "end", values=(
                f"{b['price']:.6f}", f"{b['amount']:.4f}", b["owner"]))
        if book["best_bid"] and book["best_ask"]:
            self.lbl_book.config(
                text=f"best bid={book['best_bid']:.6f}  "
                     f"best ask={book['best_ask']:.6f}  "
                     f"spread={book['spread']:.6f}  mid={book['mid']:.6f}")
        else:
            self.lbl_book.config(text="sem ordens")

    def _refresh_orders(self):
        for i in self.tv_orders.get_children(): self.tv_orders.delete(i)
        for o in self.chain.my_orders(self.identity["address"]):
            self.tv_orders.insert("", "end", values=(
                o["order_id"][:16], o["side"],
                f"{o['price']:.6f}", f"{o['amount']:.4f}",
                f"{o['filled']:.4f}", f"{o['remaining']:.4f}",
                o["status"]))

    def _refresh_trades(self):
        for i in self.tv_trades.get_children(): self.tv_trades.delete(i)
        for t in self.chain.my_trades(self.identity["address"], limit=20):
            self.tv_trades.insert("", "end", values=(
                time.strftime("%H:%M:%S", time.localtime(t["timestamp"])),
                t["role"], f"{t['price']:.6f}",
                f"{t['amount']:.4f}", f"{t['cost']:.4f}", t["pair"]))


# =====================================================================
# Bootstrap
# =====================================================================
import time

def main():
    identity = load_identity()
    print(f"[wallet] endereco: {identity['address']}")

    chain = Blockchain(node_identity=identity)
    pm = PeerManager(chain, port=PORT)
    node = NodeRunner(chain, pm)
    node.start()

    root = tk.Tk()
    app = WalletApp(root, chain, identity)
    try:
        root.mainloop()
    finally:
        node.stop()
        print("[wallet] encerrado.")


if __name__ == "__main__":
    main()