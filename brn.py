"""
brn.py — Nó BRN monolítico.

Contém TUDO:
  - Config (.env)
  - Block / Transaction / State / Finality / Slashing
  - Blockchain (PoW + CLOB + MEV + RWA)
  - PeerManager (P2P + NGROK)
  - Node (wrapper thread)
  - CLI + Explorer + entry point

Modos:
  python brn.py node       → CLI interativa + mineração
  python brn.py explorer   → dashboard de relatório
  python brn.py both       → os dois (roda CLI + relatório)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple

# carrega .env antes de qualquer coisa
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# dependências opcionais do projeto (se existirem, são usadas)
try:
    from cripto_wallet import WalletManager
except ImportError:
    WalletManager = None

try:
    from assets import (AssetRegistry, AssetDefinition, ComplianceRecord,
                        ASSET_TYPES, KYC_STATUS, KYC_LEVELS)
except ImportError:
    # Fallback mínimo para rodar sem assets.py
    ASSET_TYPES  = {"currency", "equity", "bond", "real_estate", "commodity"}
    KYC_STATUS   = {"pending", "approved", "rejected", "revoked"}
    KYC_LEVELS   = {"basic", "advanced", "institutional"}

    @dataclass
    class AssetDefinition:
        asset_id: str
        name: str = ""
        symbol: str = ""
        asset_type: str = "currency"
        decimals: int = 8
        issuer: str = ""
        transfer_agent: str = ""
        custodian: str = ""
        isin: str = ""
        max_supply: float = 0.0
        transfer_restricted: bool = False
        frozen: bool = False
        legal_doc_hash: str = ""
        metadata: dict = field(default_factory=dict)
        def to_dict(self): return asdict(self)
        @classmethod
        def from_dict(cls, d): 
            d = dict(d)
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @dataclass
    class ComplianceRecord:
        address: str
        status: str = "pending"
        level: str = "basic"
        jurisdiction: str = ""
        verified_by: str = ""
        verified_at: float = 0.0
        expires_at: float = 0.0
        restrictions: list = field(default_factory=list)
        metadata: dict = field(default_factory=dict)
        def to_dict(self): return asdict(self)
        @classmethod
        def from_dict(cls, d): 
            d = dict(d)
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    class AssetRegistry:
        def __init__(self):
            self.assets: dict = {}
            self.compliance: dict = {}
            self.regulator: str = ""
        def add_asset(self, a): self.assets[a.asset_id] = a
        def get_asset(self, aid): return self.assets.get(aid)
        def update_asset(self, aid, md):
            a = self.assets.get(aid)
            if a:
                for k, v in md.items():
                    if hasattr(a, k): setattr(a, k, v)
        def set_compliance(self, rec): self.compliance[rec.address] = rec
        def get_compliance(self, addr): return self.compliance.get(addr)
        def can_issue(self, aid, addr):
            a = self.assets.get(aid)
            return bool(a and a.issuer == addr)
        def can_freeze(self, aid, addr):
            a = self.assets.get(aid)
            return bool(a and (a.issuer == addr or a.transfer_agent == addr 
                              or self.regulator == addr))
        def can_manage_kyc(self, aid, addr):
            a = self.assets.get(aid)
            return bool(a and (a.issuer == addr or a.transfer_agent == addr
                              or self.regulator == addr))
        def validate_transfer(self, aid, sender, receiver, amount, recv_after):
            a = self.assets.get(aid)
            if not a: return False, "asset inexistente"
            if a.frozen: return False, "asset congelado"
            if a.transfer_restricted:
                recv_rec = self.compliance.get(receiver)
                if not recv_rec or recv_rec.status != "approved":
                    return False, "destinatario sem KYC"
            return True, "ok"
        def to_dict(self):
            return {"assets": {k: v.to_dict() for k, v in self.assets.items()},
                    "compliance": {k: v.to_dict() for k, v in self.compliance.items()},
                    "regulator": self.regulator}
        @classmethod
        def from_dict(cls, d):
            r = cls()
            for k, v in d.get("assets", {}).items():
                r.assets[k] = AssetDefinition.from_dict(v)
            for k, v in d.get("compliance", {}).items():
                r.compliance[k] = ComplianceRecord.from_dict(v)
            r.regulator = d.get("regulator", "")
            return r

# se tiver TransferRule, mantém compat
try:
    from assets import TransferRule
except ImportError:
    TransferRule = None


# =====================================================================
# CONFIG
# =====================================================================
NETWORK_ID       = os.environ.get("BRN_NETWORK_ID", "brn-rwa-1")
BLOCK_REWARD     = float(os.environ.get("BRN_BLOCK_REWARD", "1"))
DB_PATH          = os.environ.get("BRN_DB_PATH", "blockchain.db")
FINALITY_INTERVAL= int(os.environ.get("BRN_FINALITY_INTERVAL", "5"))
FINALITY_THRESHOLD = float(os.environ.get("BRN_FINALITY_THRESHOLD", "0.67"))
FAUCET_ADDRESS   = os.environ.get("BRN_FAUCET_ADDRESS", "").strip()
FAUCET_AMOUNT    = float(os.environ.get("BRN_FAUCET_AMOUNT", "100"))
FAUCET_COOLDOWN  = int(os.environ.get("BRN_FAUCET_COOLDOWN", "3600"))
REGULATOR_ADDRESS= os.environ.get("BRN_REGULATOR_ADDRESS", "").strip()
NATIVE_ASSET     = "BRN"
NATIVE_ASSET_ISSUER = "brn1" + "0" * 40

# PoW
POW_INITIAL_DIFFICULTY = int(os.environ.get("BRN_POW_DIFFICULTY", "16"))
POW_ADJUST_INTERVAL    = int(os.environ.get("BRN_POW_ADJUST_INTERVAL", "10"))
POW_TARGET_TIME        = float(os.environ.get("BRN_POW_TARGET_TIME", "10"))
POW_MAX_ADJUST_FACTOR  = 4
POW_MIN_DIFFICULTY     = 1

# MEV
MEV_COMMIT_MIN_BLOCKS   = int(os.environ.get("BRN_MEV_MIN_BLOCKS", "2"))
MEV_COMMIT_MAX_BLOCKS   = int(os.environ.get("BRN_MEV_MAX_BLOCKS", "20"))
MEV_MAX_PENDING_COMMITS = int(os.environ.get("BRN_MEV_MAX_COMMITS", "20"))

# P2P
P2P_PORT          = int(os.environ.get("BRN_P2P_PORT", "6001"))
MAX_PEERS         = 16
MAX_KNOWN         = 1000
SYNC_BATCH        = 2000
HANDSHAKE_TIMEOUT = 5
CONNECT_TIMEOUT   = 8
HEADERS_TIMEOUT   = 15
BLOCK_TIMEOUT     = 20
PING_INTERVAL     = 30
PEER_TIMEOUT      = 90
RECONNECT_BACKOFF = 60
INV_CACHE_TTL     = 300
MAX_SYNC_ATTEMPTS = 3
PROTOCOL_VERSION  = 3

# CLOB
BASE_ASSET  = os.environ.get("BRN_BASE", "BRN")
QUOTE_ASSET = os.environ.get("BRN_QUOTE", "USDC")

# NGROK
NGROK_ENABLED = os.environ.get("BRN_NGROK", "0") == "1"
NGROK_TOKEN   = os.environ.get("NGROK_AUTHTOKEN", "")
NGROK_REGION  = os.environ.get("NGROK_REGION", "us")

# mineração
MINER_INTERVAL = float(os.environ.get("BRN_MINER_INTERVAL", "0.5"))
MINER_ENABLED  = os.environ.get("BRN_MINE", "1") == "1"

# report
REPORT_INTERVAL = int(os.environ.get("BRN_REPORT_INTERVAL", "30"))

# seeds
def _parse_seeds(raw: str):
    out = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry: continue
        h, p = entry.rsplit(":", 1)
        try: out.append((h.strip(), int(p.strip())))
        except ValueError: continue
    return out

HARDCODED_SEEDS = _parse_seeds(os.environ.get(
    "BRN_HARDCODED_SEEDS", "127.0.0.1:6001,127.0.0.1:6002,127.0.0.1:6003"))
DNS_SEEDS = [s.strip() for s in os.environ.get("BRN_DNS_SEEDS", "").split(",") if s.strip()]

# genesis
_raw = os.environ.get("BRN_GENESIS_ALLOC", "").strip()
GENESIS_ALLOCATIONS: Dict[str, Dict[str, float]] = {}
if _raw:
    for pair in _raw.split(","):
        parts = pair.split(":")
        if len(parts) == 3:
            try:
                GENESIS_ALLOCATIONS.setdefault(parts[0].strip(), {})[parts[1].strip()] = float(parts[2].strip())
            except ValueError: pass


# =====================================================================
# BLOCK
# =====================================================================
@dataclass
class Block:
    index: int
    timestamp: float
    previous_hash: str
    transactions: List[dict]
    miner: str
    difficulty: int = POW_INITIAL_DIFFICULTY
    nonce: int = 0
    miner_public_key: str = ""
    signature: str = ""
    hash: str = ""
    registry_snapshot: Optional[dict] = None

    def calculate_hash(self):
        body = {"index": self.index, "timestamp": self.timestamp,
                "previous_hash": self.previous_hash,
                "transactions": self.transactions,
                "miner": self.miner, "difficulty": self.difficulty,
                "nonce": self.nonce, "network_id": NETWORK_ID,
                "registry_hash": self._registry_hash()}
        return hashlib.sha3_256(json.dumps(body, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()

    def _registry_hash(self):
        if not self.registry_snapshot: return ""
        return hashlib.sha3_256(json.dumps(self.registry_snapshot,
                                sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def target(self): return "0" * self.difficulty
    def finalize(self): self.hash = self.calculate_hash()

    def mine(self):
        t = self.target()
        while True:
            h = self.calculate_hash()
            if h.startswith(t): self.hash = h; return self.nonce
            self.nonce += 1

    def sign(self, sk_hex):
        if not self.hash: self.finalize()
        self.signature = WalletManager.sign_transaction(
            sk_hex, {"block_hash": self.hash, "index": self.index})

    def verify_pow(self):
        return self.hash == self.calculate_hash() and self.hash.startswith(self.target())

    def verify(self):
        if not self.verify_pow(): return False
        if self.index == 0: return True
        if self.miner_public_key and self.signature:
            if WalletManager.address_from_public_key(self.miner_public_key) != self.miner:
                return False
            if not WalletManager.verify_signature(self.miner_public_key,
                    {"block_hash": self.hash, "index": self.index}, self.signature):
                return False
        return True

    def to_dict(self): return asdict(self)
    def to_wire_dict(self):
        d = self.to_dict(); d["height"] = d.pop("index"); return d
    def to_wire_header(self):
        return {"height": self.index, "hash": self.hash,
                "previous_hash": self.previous_hash, "timestamp": self.timestamp,
                "difficulty": self.difficulty, "nonce": self.nonce, "miner": self.miner}

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        if "height" in d and "index" not in d:
            d["index"] = d.pop("height")
        allowed = {"index","timestamp","previous_hash","transactions","miner",
                   "difficulty","nonce","miner_public_key","signature",
                   "hash","registry_snapshot"}
        return cls(**{k: v for k, v in d.items() if k in allowed})


# =====================================================================
# TRANSACTION
# =====================================================================
class Transaction:
    REQUIRED_FIELDS = {"type","asset_id","from","to","amount","nonce","public_key","signature"}
    VALID_TYPES = {
        "transfer","issue","redeem","freeze","unfreeze",
        "kyc_register","kyc_revoke","asset_create","asset_update","dividend",
        "order_place","order_cancel",
        "order_commit","order_reveal",
    }
    CANONICAL_FIELDS = {"type","asset_id","from","to","amount",
                        "nonce","public_key","timestamp","metadata"}

    @staticmethod
    def build(tx_type, asset_id, sender_address, receiver_address, amount,
              nonce, private_key_hex, public_key_hex, metadata=None):
        if tx_type not in Transaction.VALID_TYPES:
            raise ValueError(f"Tipo invalido: {tx_type}")
        if amount < 0: raise ValueError("Valor negativo.")
        tx = {"type": tx_type, "asset_id": asset_id, "from": sender_address,
              "to": receiver_address, "amount": float(amount),
              "nonce": int(nonce), "public_key": public_key_hex,
              "timestamp": time.time(), "metadata": metadata or {}}
        tx["signature"] = WalletManager.sign_transaction(private_key_hex, tx)
        return tx

    @staticmethod
    def sanitize(tx): 
        return {k: v for k, v in tx.items()
                if k in Transaction.CANONICAL_FIELDS or k == "signature"}

    @staticmethod
    def verify_signature(tx):
        if not Transaction.REQUIRED_FIELDS.issubset(tx.keys()): return False
        payload = {k: v for k, v in tx.items() if k != "signature"}
        return WalletManager.verify_signature(tx["public_key"], payload, tx["signature"])

    @staticmethod
    def derived_address(tx):
        return WalletManager.address_from_public_key(tx["public_key"])

    @staticmethod
    def hash(tx):
        clean = {k: v for k, v in tx.items()
                 if k in Transaction.CANONICAL_FIELDS or k == "signature"}
        return hashlib.sha3_256(json.dumps(clean, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()


# =====================================================================
# STATE
# =====================================================================
class State:
    def __init__(self):
        self.balances: Dict[str, Dict[str, float]] = {}
        self.nonces: Dict[str, int] = {}
        self.frozen: Dict[str, Dict[str, float]] = {}
        self.total_supply: Dict[str, float] = {}
        self.orders: Dict[str, dict] = {}
        self.order_books: Dict[str, dict] = {}
        self.trades: List[dict] = []
        self.commits: Dict[str, dict] = {}

    def balance(self, addr, asset_id=NATIVE_ASSET):
        return self.balances.get(addr, {}).get(asset_id, 0.0)
    def available(self, addr, asset_id):
        return self.balance(addr, asset_id) - self.frozen.get(addr, {}).get(asset_id, 0.0)
    def nonce(self, addr): return self.nonces.get(addr, 0)

    def credit(self, addr, asset_id, amount):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) + amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) + amount

    def debit(self, addr, asset_id, amount):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) - amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) - amount

    def freeze(self, addr, asset_id, amount):
        self.frozen.setdefault(addr, {})
        self.frozen[addr][asset_id] = self.frozen[addr].get(asset_id, 0.0) + amount

    def unfreeze(self, addr, asset_id, amount):
        self.frozen.setdefault(addr, {})
        self.frozen[addr][asset_id] = max(0.0, self.frozen[addr].get(asset_id, 0.0) - amount)

    def copy(self):
        s = State()
        s.balances = {a: dict(v) for a, v in self.balances.items()}
        s.nonces = dict(self.nonces)
        s.frozen = {a: dict(v) for a, v in self.frozen.items()}
        s.total_supply = dict(self.total_supply)
        s.orders = {k: dict(v) for k, v in self.orders.items()}
        s.order_books = {k: {"bids": list(v["bids"]), "asks": list(v["asks"])}
                         for k, v in self.order_books.items()}
        s.trades = [dict(t) for t in self.trades]
        s.commits = {k: dict(v) for k, v in self.commits.items()}
        return s


# =====================================================================
# FINALITY
# =====================================================================
class Finality:
    def __init__(self, interval=FINALITY_INTERVAL, threshold=FINALITY_THRESHOLD):
        self.interval = interval; self.threshold = threshold
        self.finalized_height = -1; self.votes: Dict[int, Set[str]] = {}
    def update_by_depth(self, tip, confirmations=6):
        nf = max(-1, tip - confirmations)
        if nf > self.finalized_height:
            self.finalized_height = nf; return True
        return False
    def to_dict(self):
        return {"interval": self.interval, "threshold": self.threshold,
                "finalized_height": self.finalized_height,
                "votes": {str(k): list(v) for k, v in self.votes.items()}}
    @classmethod
    def from_dict(cls, d):
        f = cls(d.get("interval", FINALITY_INTERVAL), d.get("threshold", FINALITY_THRESHOLD))
        f.finalized_height = d.get("finalized_height", -1)
        f.votes = {int(k): set(v) for k, v in d.get("votes", {}).items()}
        return f


# =====================================================================
# SLASHING
# =====================================================================
@dataclass
class SlashingEvidence:
    validator: str; reason: str; block_index: int; invalid_block: dict
    reporter: str; reporter_public_key: str; reporter_signature: str
    def canonical(self):
        return {"validator": self.validator, "reason": self.reason,
                "block_index": self.block_index,
                "block_hash": self.invalid_block.get("hash", ""),
                "reporter": self.reporter}
    def verify(self):
        try: blk = Block.from_dict(self.invalid_block)
        except Exception: return False
        if blk.verify(): return False
        if self.validator != blk.miner: return False
        if self.block_index != blk.index: return False
        if WalletManager.address_from_public_key(self.reporter_public_key) != self.reporter:
            return False
        return WalletManager.verify_signature(self.reporter_public_key,
            self.canonical(), self.reporter_signature)
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**d)


# =====================================================================
# BLOCKCHAIN
# =====================================================================
class Blockchain:
    def __init__(self, db_path=DB_PATH, node_identity=None,
                 genesis_allocations=None, regulator_address=""):
        self.db_path = db_path
        self.node_identity = node_identity
        self.genesis_allocations = dict(genesis_allocations or GENESIS_ALLOCATIONS)
        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.registry = AssetRegistry()
        if regulator_address: self.registry.regulator = regulator_address
        self.slashed: Set[str] = set()
        self.finality = Finality()
        self.lock = threading.RLock()
        self._init_db()
        if not self._load_from_db():
            self._create_genesis()
            self._persist_block(self.chain[0])
            self._rebuild_state()
            self._bootstrap_native_asset()
            print("[chain] genese criada.")
        else:
            print(f"[chain] {len(self.chain)} blocos carregados.")

    # ---------- DB ----------
    def _bootstrap_native_asset(self):
        if NATIVE_ASSET in self.registry.assets: return
        self.registry.add_asset(AssetDefinition(
            asset_id=NATIVE_ASSET, name="BRN", symbol="BRN",
            asset_type="currency", decimals=8,
            issuer=NATIVE_ASSET_ISSUER, transfer_agent=NATIVE_ASSET_ISSUER,
            transfer_restricted=False, max_supply=0))
        self._persist_registry()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS blocks (
                idx INTEGER PRIMARY KEY, hash TEXT NOT NULL,
                validator TEXT, timestamp REAL, data TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(hash)")
            conn.execute("""CREATE TABLE IF NOT EXISTS mempool (
                tx_hash TEXT PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS slashing (
                validator TEXT PRIMARY KEY, reason TEXT,
                block_idx INTEGER, ts REAL, evidence TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS faucet_claims (
                address TEXT PRIMARY KEY, last_claim REAL NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS finality (
                id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS registry (
                id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)""")
            conn.commit()

    def _persist_block(self, block):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO blocks VALUES (?,?,?,?,?)",
                         (block.index, block.hash, block.miner,
                          block.timestamp, json.dumps(block.to_dict())))
            conn.commit()

    def _persist_registry(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO registry VALUES (1, ?)",
                         (json.dumps(self.registry.to_dict()),))
            conn.commit()

    def _persist_finality(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO finality VALUES (1, ?)",
                         (json.dumps(self.finality.to_dict()),))
            conn.commit()

    def _load_registry(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT data FROM registry WHERE id=1").fetchone()
        if row:
            try: self.registry = AssetRegistry.from_dict(json.loads(row[0]))
            except Exception: pass

    def _load_finality(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT data FROM finality WHERE id=1").fetchone()
        if row:
            try: self.finality = Finality.from_dict(json.loads(row[0]))
            except Exception: pass

    def _load_slashed(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT validator FROM slashing").fetchall()
        self.slashed = {r[0] for r in rows}

    def _mempool_add(self, tx):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO mempool VALUES (?,?,?)",
                         (Transaction.hash(tx), time.time(), json.dumps(tx)))
            conn.commit()

    def _mempool_remove(self, tx):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mempool WHERE tx_hash=?", (Transaction.hash(tx),))
            conn.commit()

    def _mempool_clear(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mempool"); conn.commit()

    def _mempool_load(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT data FROM mempool ORDER BY ts ASC").fetchall()
        self.pending = []
        for (data,) in rows:
            try:
                tx = Transaction.sanitize(json.loads(data))
                if Transaction.verify_signature(tx): self.pending.append(tx)
            except Exception: continue

    def _load_from_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
        except sqlite3.Error: return False
        if not rows: return False
        self.chain = [Block.from_dict(json.loads(r[0])) for r in rows]
        self._load_slashed(); self._load_finality(); self._load_registry()
        self._rebuild_state(); self._mempool_load()
        return True

    def _create_genesis(self):
        g = Block(index=0, timestamp=time.time(), previous_hash="0"*64,
                  transactions=[], miner="genesis", difficulty=POW_INITIAL_DIFFICULTY)
        g.finalize(); self.chain.append(g)

    @property
    def last_block(self): return self.chain[-1]
    @property
    def height(self): return self.last_block.index
    @property
    def tip_hash(self): return self.last_block.hash
    @property
    def mempool(self): return self.pending

    # ---------- difficulty ----------
    def _next_difficulty(self):
        c = self.chain
        if len(c) < POW_ADJUST_INTERVAL or len(c) % POW_ADJUST_INTERVAL != 0:
            return c[-1].difficulty
        w = c[-POW_ADJUST_INTERVAL:]
        elapsed = w[-1].timestamp - w[0].timestamp
        if elapsed <= 0: return c[-1].difficulty + 1
        expected = POW_TARGET_TIME * (POW_ADJUST_INTERVAL - 1)
        ratio = expected / elapsed
        ratio = max(1/POW_MAX_ADJUST_FACTOR, min(POW_MAX_ADJUST_FACTOR, ratio))
        return max(POW_MIN_DIFFICULTY, int(c[-1].difficulty * ratio))

    # ---------- rebuild ----------
    def _rebuild_state(self):
        self.state = State()
        for addr, alloc in self.genesis_allocations.items():
            for aid, amt in alloc.items():
                self.state.credit(addr, aid, amt)
        for blk in self.chain[1:]:
            for tx in blk.transactions:
                self._apply_tx_to_state(self.state, tx)
            self.state.credit(blk.miner, NATIVE_ASSET, BLOCK_REWARD)
            self._expire_commits(self.state)

    # ==================================================================
    # CLOB — helpers
    # ==================================================================
    @staticmethod
    def _pair_id(a, b):
        x, y = sorted([a, b]); return f"{x}-{y}"
    @staticmethod
    def _order_id(h):
        return hashlib.sha3_256(f"order:{h}".encode()).hexdigest()

    def _get_or_create_book(self, state, pair):
        if pair not in state.order_books:
            state.order_books[pair] = {"bids": [], "asks": []}
        return state.order_books[pair]

    def _insert_order_sorted(self, state, book, order, side):
        lst = book["bids"] if side == "buy" else book["asks"]
        if order["order_id"] in lst: lst.remove(order["order_id"])
        idx = 0
        for i, oid in enumerate(lst):
            existing = state.orders.get(oid)
            if not existing: continue
            if side == "buy":
                if (existing["price"] < order["price"] or
                    (existing["price"] == order["price"] and
                     existing["created_at"] > order["created_at"])):
                    idx = i; break
            else:
                if (existing["price"] > order["price"] or
                    (existing["price"] == order["price"] and
                     existing["created_at"] > order["created_at"])):
                    idx = i; break
            idx = i + 1
        lst.insert(idx, order["order_id"])

    def _remove_from_book(self, state, book, order):
        lst = book["bids"] if order["side"] == "buy" else book["asks"]
        if order["order_id"] in lst: lst.remove(order["order_id"])

    def _clean_book(self, state, book):
        for side in ("bids", "asks"):
            lst = book[side]
            while lst:
                o = state.orders.get(lst[0])
                if not o or o["status"] not in ("open","partial") or o["filled"] >= o["amount"]:
                    lst.pop(0)
                else: break

    def _execute_fill(self, state, buy, sell, fill, price):
        cost = price * fill
        buyer = buy["owner"]; seller = sell["owner"]
        base = buy["base"]; quote = buy["quote"]
        bp = buy["price"]
        state.unfreeze(buyer, quote, bp * fill)
        state.debit(buyer, quote, cost)
        state.credit(buyer, base, fill)
        state.unfreeze(seller, base, fill)
        state.debit(seller, base, fill)
        state.credit(seller, quote, cost)
        buy["filled"] += fill
        sell["filled"] += fill
        tid = hashlib.sha3_256(
            f"{buy['order_id']}:{sell['order_id']}:{fill}:{time.time()}".encode()
        ).hexdigest()[:32]
        state.trades.append({
            "trade_id": tid, "pair": buy["pair"],
            "base": base, "quote": quote,
            "price": price, "amount": fill, "cost": cost,
            "buyer": buyer, "seller": seller,
            "buy_order_id": buy["order_id"], "sell_order_id": sell["order_id"],
            "timestamp": time.time(),
        })

    def _run_matching(self, state, new_order):
        book = self._get_or_create_book(state, new_order["pair"])
        self._clean_book(state, book)
        if new_order["side"] == "buy":
            while new_order["filled"] < new_order["amount"] and book["asks"]:
                best_id = book["asks"][0]
                best = state.orders.get(best_id)
                if not best or best["status"] not in ("open","partial"):
                    book["asks"].pop(0); continue
                if best["price"] > new_order["price"]: break
                fill = min(new_order["amount"] - new_order["filled"],
                           best["amount"] - best["filled"])
                if fill <= 0: book["asks"].pop(0); continue
                self._execute_fill(state, new_order, best, fill, best["price"])
                if best["filled"] >= best["amount"]:
                    best["status"] = "filled"; book["asks"].pop(0)
                else: best["status"] = "partial"
            if new_order["filled"] < new_order["amount"]:
                new_order["status"] = "partial" if new_order["filled"] > 0 else "open"
                self._insert_order_sorted(state, book, new_order, "buy")
            else: new_order["status"] = "filled"
        else:
            while new_order["filled"] < new_order["amount"] and book["bids"]:
                best_id = book["bids"][0]
                best = state.orders.get(best_id)
                if not best or best["status"] not in ("open","partial"):
                    book["bids"].pop(0); continue
                if best["price"] < new_order["price"]: break
                fill = min(new_order["amount"] - new_order["filled"],
                           best["amount"] - best["filled"])
                if fill <= 0: book["bids"].pop(0); continue
                self._execute_fill(state, best, new_order, fill, best["price"])
                if best["filled"] >= best["amount"]:
                    best["status"] = "filled"; book["bids"].pop(0)
                else: best["status"] = "partial"
            if new_order["filled"] < new_order["amount"]:
                new_order["status"] = "partial" if new_order["filled"] > 0 else "open"
                self._insert_order_sorted(state, book, new_order, "sell")
            else: new_order["status"] = "filled"

    # ---------- CLOB ops ----------
    def _op_order_place(self, state, tx):
        md = tx.get("metadata", {})
        owner = tx["from"]; base = tx["asset_id"]
        quote = md.get("quote", ""); side = md.get("side", "").lower()
        price = float(md.get("price", 0)); amount = float(tx["amount"])
        if side not in ("buy","sell"): return False
        if not quote or quote == base: return False
        if price <= 0 or amount <= 0: return False
        if base not in self.registry.assets: return False
        if quote not in self.registry.assets: return False
        pair = self._pair_id(base, quote)
        if side == "buy":
            if state.available(owner, quote) < price * amount: return False
            state.freeze(owner, quote, price * amount)
        else:
            if state.available(owner, base) < amount: return False
            state.freeze(owner, base, amount)
       "])
 oid = self._order_id(Transaction.hash(tx))
               order = {"order_id": o if bookid,: "owner": owner, "pair": self pair,
                 "base": base, "quote": quote, "side": side,
                 "price": price, "amount": amount, "filled": 0.0,
                 "created_at": tx["timestamp"], "status": "open"}
        state.orders[oid] = order
        self._run_matching(state, order)
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    def _op_order_cancel(self, state, tx):
        md = tx.get("metadata", {})
        oid = md.get("order_id", ""); owner = tx["from"]
        order = state.orders.get(oid)
        if not order or order["owner"] != owner: return False
        if order["status"] not in ("open","partial"): return False
        remaining = order["amount"] - order["filled"]
        if remaining <= 0: order["status"] = "filled"; return False
        if order["side"] == "buy":
            state.unfreeze(owner, order["quote"], order["price"] * remaining)
        else:
            state.unfreeze(owner, order["base"], remaining)
        order["status"] = "cancelled"
        book = state.order_books.get(order["pair._remove_from_book(state, book, order)
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    # ---------- MEV ----------
    def _commit_hash(self, side, base, quote, price, amount, salt, owner):
        return hashlib.sha3_256(
            f"{side}:{base}:{quote}:{price}:{amount}:{salt}:{owner}".encode()
        ).hexdigest()

    def _op_order_commit(self, state, tx):
        md = tx.get("metadata", {})
        owner = tx["from"]; ch = md.get("commit_hash", "")
        side = md.get("side", "").lower()
        base = md.get("base", ""); quote = md.get("quote", "")
        frozen_amount = float(tx["amount"])
        if side not in ("buy","sell"): return False
        if len(ch) != 64: return False
        if not base or not quote or base == quote: return False
        if base not in self.registry.assets: return False
        if quote not in self.registry.assets: return False
        if ch in state.commits: return False
        if frozen_amount <= 0: return False
        pending = [c for c in state.commits.values()
                   if c["owner"] == owner and c["status"] == "pending"]
        if len(pending) >= MEV_MAX_PENDING_COMMITS: return False
        if side == "buy":
            if state.available(owner, quote) < frozen_amount: return False
            state.freeze(owner, quote, frozen_amount)
            fa = quote
        else:
            if state.available(owner, base) < frozen_amount: return False
            state.freeze(owner, base, frozen_amount)
            fa = base
        cur = self.last_block.index + 1
        state.commits[ch] = {
            "commit_hash": ch, "owner": owner, "side": side,
            "base": base, "quote": quote,
            "frozen_asset": fa, "frozen_amount": frozen_amount,
            "created_block": cur, "created_at": tx["timestamp"],
            "status": "pending", "order_id": None}
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    def _op_order_reveal(self, state, tx):
        md = tx.get("metadata", {})
        owner = tx["from"]; ch = md.get("commit_hash", "")
        salt = md.get("salt", "")
        base = tx["asset_id"]; quote = md.get("quote", "")
        side = md.get("side", "").lower()
        price = float(md.get("price", 0)); amount = float(tx["amount"])
        c = state.commits.get(ch)
        if not c or c["status"] != "pending": return False
        if c["owner"] != owner or c["side"] != side: return False
        if c["base"] != base or c["quote"] != quote: return False
        if price <= 0 or amount <= 0: return False
        cur = self.last_block.index + 1
        delta = cur - c["created_block"]
        if delta < MEV_COMMIT_MIN_BLOCKS or delta > MEV_COMMIT_MAX_BLOCKS: return False
        if self._commit_hash(side, base, quote, price, amount, salt, owner) != ch:
            return False
        required = price * amount if side == "buy" else amount
        if required > c["frozen_amount"]: return False
        state.unfreeze(owner, c["frozen_asset"], c["frozen_amount"])
        if side == "buy":
            if state.available(owner, quote) < price * amount: return False
            state.freeze(owner, quote, price * amount)
        else:
            if state.available(owner, base) < amount: return False
            state.freeze(owner, base, amount)
        oid = self._order_id(Transaction.hash(tx))
        order = {"order_id": oid, "owner": owner, "pair": self._pair_id(base, quote),
                 "base": base, "quote": quote, "side": side,
                 "price": price, "amount": amount, "filled": 0.0,
                 "created_at": tx["timestamp"], "status": "open",
                 "commit_hash": ch}
        state.orders[oid] = order
        self._run_matching(state, order)
        c["status"] = "revealed"; c["order_id"] = oid
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    def _expire_commits(self, state):
        cur = self.last_block.index + 1
        for ch, c in list(state.commits.items()):
            if c["status"] != "pending": continue
            if cur - c["created_block"] > MEV_COMMIT_MAX_BLOCKS:
                state.unfreeze(c["owner"], c["frozen_asset"], c["frozen_amount"])
                c["status"] = "expired"

    # ---------- apply tx ----------
    def _apply_tx_to_state(self, state, tx):
        t = tx["type"]
        s_, r_ = tx["from"], tx["to"]
        aid = tx["asset_id"]; amt = float(tx["amount"])
        if t == "transfer":
            if state.available(s_, aid) < amt: return False
            state.debit(s_, aid, amt); state.credit(r_, aid, amt)
            state.nonces[s_] = state.nonce(s_) + 1; return True
        if t == "issue":
            a = self.registry.get_asset(aid)
            if not a: return False
            if a.max_supply > 0 and state.total_supply.get(aid, 0) + amt > a.max_supply:
                return False
            state.credit(r_, aid, amt); state.nonces[s_] = state.nonce(s_) + 1; return True
        if t == "redeem":
            if state.available(s_, aid) < amt: return False
            state.debit(s_, aid, amt); state.nonces[s_] = state.nonce(s_) + 1; return True
        if t in ("freeze","unfreeze"):
            if t == "freeze":
                if state.available(r_, aid) < amt: return False
                state.freeze(r_, aid, amt)
            else: state.unfreeze(r_, aid, amt)
            state.nonces[s_] = state.nonce(s_) + 1; return True
        if t in ("kyc_register","kyc_revoke","asset_create","asset_update","dividend"):
            state.nonces[s_] = state.nonce(s_) + 1; return True
        if t == "order_place":  return self._op_order_place(state, tx)
        if t == "order_cancel": return self._op_order_cancel(state, tx)
        if t == "order_commit": return self._op_order_commit(state, tx)
        if t == "order_reveal": return self._op_order_reveal(state, tx)
        return False

    def _apply_registry_ops(self, tx, persist=True):
        t = tx["type"]; md = tx.get("metadata", {})
        if t == "asset_create":
            a = AssetDefinition(
                asset_id=md["asset_id"], name=md.get("name", md["asset_id"]),
                symbol=md.get("symbol", md["asset_id"]),
                asset_type=md["asset_type"], decimals=int(md.get("decimals", 2)),
                issuer=md["issuer"],
                transfer_agent=md.get("transfer_agent", md["issuer"]),
                custodian=md.get("custodian", ""), isin=md.get("isin", ""),
                max_supply=float(md.get("max_supply", 0)),
                transfer_restricted=bool(md.get("transfer_restricted", True)),
                legal_doc_hash=md.get("legal_doc_hash", ""),
                metadata=md.get("metadata", {}))
            self.registry.add_asset(a)
            if persist: self._persist_registry()
        elif t == "asset_update":
            self.registry.update_asset(tx["asset_id"], md)
            if persist: self._persist_registry()
        elif t == "kyc_register":
            rec = ComplianceRecord(
                address=tx["to"], status=md.get("status", "approved"),
                level=md.get("level", "basic"),
                jurisdiction=md.get("jurisdiction", ""),
                verified_by=tx["from"], verified_at=time.time(),
                expires_at=float(md.get("expires_at", 0)),
                restrictions=list(md.get("restrictions", [])),
                metadata=md.get("extra", {}))
            self.registry.set_compliance(rec)
            if persist: self._persist_registry()
        elif t == "kyc_revoke":
            rec = self.registry.get_compliance(tx["to"])
            if rec:
                rec.status = "revoked"
                if persist: self._persist_registry()

    # ---------- validate ----------
    def _validate_tx(self, tx, state=None):
        state = state or self.state
        t = tx["type"]; aid = tx["asset_id"]
        s_, r_ = tx["from"], tx["to"]
        amt = float(tx["amount"]); md = tx.get("metadata", {})
        if t == "transfer":
            if aid not in self.registry.assets:
                return False, f"ativo '{aid}' nao registrado"
            recv_after = state.balance(r_, aid) + amt
            return self.registry.validate_transfer(aid, s_, r_, amt, recv_after)
        if t == "issue":
            if not self.registry.can_issue(aid, s_): return False, "nao e emissor"
            a = self.registry.get_asset(aid)
            if a.max_supply > 0 and state.total_supply.get(aid, 0) + amt > a.max_supply:
                return False, "max_supply excedido"
            return True, "ok"
        if t == "redeem":
            a = self.registry.get_asset(aid)
            if not a: return False, "ativo inexistente"
            if s_ not in (a.issuer, a.transfer_agent): return False, "sem permissao"
            if state.available(r_, aid) < amt: return False, "saldo insuficiente"
            return True, "ok"
        if t in ("freeze","unfreeze"):
            if not self.registry.can_freeze(aid, s_): return False, "sem permissao"
            return True, "ok"
        if t == "kyc_register":
            if not self.registry.can_manage_kyc(aid, s_): return False, "sem permissao KYC"
            return True, "ok"
        if t == "kyc_revoke":
            if not self.registry.can_manage_kyc(aid, s_): return False, "sem permissao KYC"
            return True, "ok"
        if t == "asset_create":
            nid = md.get("asset_id", "")
            if not nid or nid in self.registry.assets: return False, "asset_id invalido"
            if md.get("asset_type") not in ASSET_TYPES: return False, "asset_type invalido"
            if md.get("issuer") != s_: return False, "issuer != sender"
            return True, "ok"
        if t == "asset_update":
            if not self.registry.can_issue(aid, s_): return False, "so emissor atualiza"
            return True, "ok"
        if t == "dividend":
            if not self.registry.can_issue(aid, s_): return False, "so emissor paga dividendos"
            if state.available(s_, NATIVE_ASSET) < amt: return False, "sem saldo BRN"
            return True, "ok"
        if t == "order_place":
            quote = md.get("quote", ""); side = md.get("side", "").lower()
            price = float(md.get("price", 0))
            if side not in ("buy","sell"): return False, "side invalido"
            if not quote or quote == aid: return False, "quote invalido"
            if price <= 0: return False, "price invalido"
            if amt <= 0: return False, "amount invalido"
            if aid not in self.registry.assets: return False, f"'{aid}' nao registrado"
            if quote not in self.registry.assets: return False, f"'{quote}' nao registrado"
            if side == "buy":
                if state.available(s_, quote) < price * amt: return False, "saldo quote insuficiente"
            else:
                if state.available(s_, aid) < amt: return False, "saldo base insuficiente"
            return True, "ok"
        if t == "order_cancel":
            oid = md.get("order_id", "")
            o = state.orders.get(oid)
            if not o: return False, "ordem inexistente"
            if o["owner"] != s_: return False, "ordem nao e sua"
            if o["status"] not in ("open","partial"): return False, f"ordem {o['status']}"
            return True, "ok"
        if t == "order_commit":
            ch = md.get("commit_hash", ""); side = md.get("side", "").lower()
            base = md.get("base", ""); quote = md.get("quote", "")
            if side not in ("buy","sell"): return False, "side invalido"
            if len(ch) != 64: return False, "commit_hash invalido"
            if not base or not quote or base == quote: return False, "par invalido"
            if base not in self.registry.assets: return False, f"'{base}' nao registrado"
            if quote not in self.registry.assets: return False, f"'{quote}' nao registrado"
            if ch in state.commits: return False, "commit duplicado"
            frozen = float(tx["amount"])
            if frozen <= 0: return False, "valor invalido"
            if side == "buy":
                if state.available(s_, quote) < frozen: return False, "saldo quote insuficiente"
            else:
                if state.available(s_, base) < frozen: return False, "saldo base insuficiente"
            return True, "ok"
        if t == "order_reveal":
            ch = md.get("commit_hash", "")
            c = state.commits.get(ch)
            if not c: return False, "commit inexistente"
            if c["owner"] != s_: return False, "commit nao e seu"
            if c["status"] != "pending": return False, f"commit {c['status']}"
            delta = (self.last_block.index + 1) - c["created_block"]
            if delta < MEV_COMMIT_MIN_BLOCKS:
                return False, f"aguarde {MEV_COMMIT_MIN_BLOCKS - delta} blocos"
            if delta > MEV_COMMIT_MAX_BLOCKS: return False, "commit expirado"
            side = md.get("side", "").lower()
            if side != c["side"]: return False, "side nao bate"
            if tx["asset_id"] != c["base"]: return False, "base nao bate"
            if md.get("quote", "") != c["quote"]: return False, "quote nao bate"
            price = float(md.get("price", 0)); a2 = float(tx["amount"])
            if price <= 0 or a2 <= 0: return False, "valores invalidos"
            required = price * a2 if side == "buy" else a2
            if required > c["frozen_amount"]: return False, "valor maior que o commitado"
            if self._commit_hash(side, c["base"], c["quote"], price, a2,
                                  md.get("salt", ""), s_) != ch:
                return False, "hash nao corresponde"
            return True, "ok"
        return False, "tipo nao suportado"

    def add_transaction(self, tx):
        tx = Transaction.sanitize(tx)
        with self.lock:
            if not Transaction.verify_signature(tx):
                return {"ok": False, "msg": "Assinatura invalida."}
            if Transaction.derived_address(tx) != tx["from"]:
                return {"ok": False, "msg": "Endereco != chave publica."}
            if tx["from"] in self.slashed:
                return {"ok": False, "msg": "Remetente banido."}
            if tx["nonce"] != self.state.nonce(tx["from"]):
                return {"ok": False, "msg": f"Nonce invalido (esperado {self.state.nonce(tx['from'])})."}
            if tx["type"] not in Transaction.VALID_TYPES:
                return {"ok": False, "msg": "Tipo desconhecido."}
            ok, why = self._validate_tx(tx)
            if not ok: return {"ok": False, "msg": why}
            for p in self.pending:
                if p["from"] == tx["from"] and p["nonce"] == tx["nonce"]:
                    return {"ok": False, "msg": "Duplicada."}
            self.pending.append(tx); self._mempool_add(tx)
            return {"ok": True, "msg": "Aceita.", "tx_hash": Transaction.hash(tx)}

    def submit_tx(self, tx):
        r = self.add_transaction(tx); return r.get("ok", False), r.get("msg", "")

    # ---------- mining ----------
    def produce_block(self):
        with self.lock:
            if not self.node_identity: return None
            if self.node_identity["address"] in self.slashed: return None
            temp = self.state.copy()
            self._expire_commits(temp)
            real_reg = self.registry
            self.registry = AssetRegistry.from_dict(self.registry.to_dict())
            chosen = []
            try:
                for tx in sorted(self.pending,
                                 key=lambda t: (t["timestamp"], Transaction.hash(t))):
                    if not Transaction.verify_signature(tx): continue
                    if tx["nonce"] != temp.nonce(tx["from"]): continue
                    ok, _ = self._validate_tx(tx, state=temp)
                    if not ok: continue
                    if not self._apply_tx_to_state(temp, tx): continue
                    self._apply_registry_ops(tx, persist=False)
                    chosen.append(tx)
            finally:
                self.registry = real_reg
            diff = self._next_difficulty()
            block = Block(index=self.last_block.index + 1, timestamp=time.time(),
                          previous_hash=self.last_block.hash, transactions=chosen,
                          miner=self.node_identity["address"], difficulty=diff,
                          miner_public_key=self.node_identity["public_key"],
                          registry_snapshot=self.registry.to_dict())
            print(f"[miner] bloco #{block.index} diff={diff} txs={len(chosen)}")
            block.mine()
            print(f"[miner] OK nonce={block.nonce} hash={block.hash[:16]}...")
            try: block.sign(self.node_identity["spend_secret_key"])
            except Exception: pass
            if not block.verify(): return None
            for tx in chosen:
                self._apply_tx_to_state(self.state, tx)
                self._apply_registry_ops(tx, persist=True)
                self._mempool_remove(tx)
            self.state.credit(block.miner, NATIVE_ASSET, BLOCK_REWARD)
            self.pending = [t for t in self.pending if t not in chosen]
            self._expire_commits(self.state)
            self.chain.append(block); self._persist_block(block)
            if self.finality.update_by_depth(block.index): self._persist_finality()
            return block

    def accept_block(self, block):
        if isinstance(block, Block): block = block.to_dict()
        try: blk = Block.from_dict(block)
        except Exception as e: return False, f"desserializar: {e}"
        with self.lock:
            prev = self.last_block
            if blk.hash == prev.hash: return True, "ja temos"
            if blk.index <= prev.index: return False, "altura antiga"
            if blk.previous_hash != prev.hash or blk.index != prev.index + 1:
                return False, "fora de sequencia"
            if not blk.verify():
                self._slash(blk.miner, f"PoW invalida #{blk.index}", block_idx=blk.index)
                return False, "PoW invalida"
            if blk.difficulty != self._next_difficulty():
                return False, f"dificuldade errada ({blk.difficulty})"
            sanitized = [Transaction.sanitize(tx) for tx in blk.transactions]
            temp = self.state.copy(); self._expire_commits(temp)
            real_reg = self.registry
            self.registry = AssetRegistry.from_dict(self.registry.to_dict())
            try:
                for tx in sanitized:
                    if not Transaction.verify_signature(tx): return False, "assinatura tx"
                    if tx["nonce"] != temp.nonce(tx["from"]): return False, "nonce tx"
                    ok, why = self._validate_tx(tx, state=temp)
                    if not ok: return False, why
                    if not self._apply_tx_to_state(temp, tx): return False, "apply tx"
                    self._apply_registry_ops(tx, persist=False)
                committed_reg = self.registry
            finally:
                self.registry = real_reg
            for tx in sanitized:
                self._apply_tx_to_state(self.state, tx)
                self._apply_registry_ops(tx, persist=True)
                self._mempool_remove(tx)
            self.state.credit(blk.miner, NATIVE_ASSET, BLOCK_REWARD)
            hashes = {Transaction.hash(tx) for tx in sanitized}
            self.pending = [t for t in self.pending if Transaction.hash(t) not in hashes]
            self._expire_commits(self.state)
            self.chain.append(blk); self._persist_block(blk)
            self.registry = committed_reg; self._persist_registry()
            if self.finality.update_by_depth(blk.index): self._persist_finality()
            print(f"[p2p] bloco #{blk.index} aceito ({blk.hash[:12]}...)")
            return True, "ok"

    # ---------- chain checks ----------
    def _find_invalid_block(self, chain):
        for i, blk in enumerate(chain):
            if not blk.verify(): return blk
            if i == 0: continue
            prev = chain[i-1]
            if blk.previous_hash != prev.hash or blk.index != prev.index + 1:
                return blk
        return None

    def _chainwork(self, chain):
        return sum(2 ** max(1, b.difficulty) for b in chain)
    def _fork_score(self, chain): return self._chainwork(chain)

    def _replace_all_in_db(self, blocks):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM blocks")
            for b in blocks:
                conn.execute("INSERT INTO blocks VALUES (?,?,?,?,?)",
                             (b.index, b.hash, b.miner, b.timestamp,
                              json.dumps(b.to_dict())))
            conn.commit()

    def replace_chain(self, new_chain):
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b for b in new_chain]
        except Exception as e:
            print(f"[chain] erro desserializar: {e}"); return False
        with self.lock:
            if len(blocks) <= self.finality.finalized_height: return False
            for i in range(min(self.finality.finalized_height + 1, len(self.chain))):
                if i >= len(blocks) or blocks[i].hash != self.chain[i].hash:
                    return False
            invalid = self._find_invalid_block(blocks)
            if invalid:
                self._slash(invalid.miner, f"bloco invalido #{invalid.index}",
                            block_idx=invalid.index); return False
            if self._fork_score(blocks) < self._fork_score(self.chain): return False
            if self._fork_score(blocks) == self._fork_score(self.chain) and \
               len(blocks) <= len(self.chain): return False
            new_state = State()
            new_reg = AssetRegistry()
            new_reg.regulator = self.registry.regulator
            new_reg.add_asset(AssetDefinition(
                asset_id=NATIVE_ASSET, name="BRN", symbol="BRN",
                asset_type="currency", decimals=8,
                issuer=NATIVE_ASSET_ISSUER, transfer_agent=NATIVE_ASSET_ISSUER,
                transfer_restricted=False))
            for addr, alloc in self.genesis_allocations.items():
                for aid, amt in alloc.items():
                    new_state.credit(addr, aid, amt)
            real_reg = self.registry; self.registry = new_reg
            try:
                for blk in blocks[1:]:
                    for raw in blk.transactions:
                        tx = Transaction.sanitize(raw)
                        if not Transaction.verify_signature(tx): return False
                        if not self._apply_tx_to_state(new_state, tx): return False
                        self._apply_registry_ops(tx, persist=False)
                    new_state.credit(blk.miner, NATIVE_ASSET, BLOCK_REWARD)
                    cur = blk.index
                    for ch, c in list(new_state.commits.items()):
                        if c["status"] != "pending": continue
                        if cur - c["created_block"] > MEV_COMMIT_MAX_BLOCKS:
                            new_state.unfreeze(c["owner"], c["frozen_asset"],
                                                c["frozen_amount"])
                            c["status"] = "expired"
                committed_reg = self.registry
            except Exception as e:
                print(f"[chain] erro replay: {e}"); return False
            finally: self.registry = real_reg
            self.chain = blocks; self.state = new_state
            self.registry = committed_reg; self._persist_registry()
            self._replace_all_in_db(blocks)
            confirmed = {Transaction.hash(tx) for blk in blocks for tx in blk.transactions}
            self.pending = [t for t in self.pending if Transaction.hash(t) not in confirmed]
            self._mempool_clear()
            for t in self.pending: self._mempool_add(t)
            print(f"[chain] substituida -> altura {len(blocks)}")
            return True

    # ---------- slashing / faucet ----------
    def _slash(self, validator, reason, block_idx=-1, evidence=None):
        if not validator or validator == "genesis": return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO slashing VALUES (?,?,?,?,?)",
                         (validator, reason, block_idx, time.time(),
                          json.dumps(evidence.to_dict()) if evidence else None))
            conn.commit()
        self.slashed.add(validator)
        if validator in self.state.balances:
            self.state.balances[validator] = {}
        self.state.nonces[validator] = self.state.nonce(validator) + 1
        print(f"[slash] {validator[:16]}... banido ({reason})")
        return True

    def faucet(self, to_address, private_key_hex, public_key_hex):
        if not FAUCET_ADDRESS: return {"ok": False, "msg": "Faucet desativado."}
        if FAUCET_ADDRESS != WalletManager.address_from_public_key(public_key_hex):
            return {"ok": False, "msg": "Chave nao corresponde."}
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT last_claim FROM faucet_claims WHERE address=?",
                               (to_address,)).fetchone()
        if row and (now - row[0]) < FAUCET_COOLDOWN:
            return {"ok": False, "msg": f"Aguarde {int(FAUCET_COOLDOWN-(now-row[0]))}s."}
        tx = Transaction.build(
            tx_type="transfer", asset_id=NATIVE_ASSET,
            sender_address=FAUCET_ADDRESS, receiver_address=to_address,
            amount=FAUCET_AMOUNT, nonce=self.state.nonce(FAUCET_ADDRESS),
            private_key_hex=private_key_hex, public_key_hex=public_key_hex)
        r = self.add_transaction(tx)
        if not r.get("ok"): return r
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO faucet_claims VALUES (?,?)",
                         (to_address, now))
            conn.commit()
        return {"ok": True, "msg": f"{FAUCET_AMOUNT} BRN enfileirados.", "tx": tx}

    # ---------- views ----------
    def order_book(self, base, quote, depth=10):
        pair = self._pair_id(base, quote)
        book = self.state.order_books.get(pair, {"bids": [], "asks": []})
        bids, asks = [], []
        for oid in book["bids"][:depth]:
            o = self.state.orders.get(oid)
            if o and o["status"] in ("open","partial"):
                bids.append({"price": o["price"], "amount": o["amount"]-o["filled"],
                             "owner": o["owner"][:12]+"..."})
        for oid in book["asks"][:depth]:
            o = self.state.orders.get(oid)
            if o and o["status"] in ("open","partial"):
                asks.append({"price": o["price"], "amount": o["amount"]-o["filled"],
                             "owner": o["owner"][:12]+"..."})
        bb = bids[0]["price"] if bids else 0.0
        ba = asks[0]["price"] if asks else 0.0
        return {"pair": pair, "bids": bids, "asks": asks,
                "best_bid": bb, "best_ask": ba,
                "spread": (ba-bb) if (bb and ba) else 0.0,
                "mid": (bb+ba)/2 if (bb and ba) else 0.0}

    def my_orders(self, addr, status_in=None):
        status_in = status_in or ("open","partial")
        out = []
        for o in self.state.orders.values():
            if o["owner"] != addr or o["status"] not in status_in: continue
            out.append({"order_id": o["order_id"], "pair": o["pair"],
                        "side": o["side"], "price": o["price"],
                        "amount": o["amount"], "filled": o["filled"],
                        "remaining": o["amount"]-o["filled"],
                        "status": o["status"], "created_at": o["created_at"]})
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

    def my_trades(self, addr, limit=50):
        out = []
        for t in self.state.trades:
            if t["buyer"] == addr or t["seller"] == addr:
                role = "buy" if t["buyer"] == addr else "sell"
                out.append({**t, "role": role})
        out.sort(key=lambda x: x["timestamp"], reverse=True)
        return out[:limit]

    def my_commits(self, addr, status_in=None):
        status_in = status_in or ("pending","revealed","expired")
        cur = self.last_block.index
        out = []
        for c in self.state.commits.values():
            if c["owner"] != addr or c["status"] not in status_in: continue
            el = cur - c["created_block"]
            out.append({"commit_hash": c["commit_hash"][:16]+"...",
                        "commit_hash_full": c["commit_hash"],
                        "side": c["side"], "base": c["base"], "quote": c["quote"],
                        "frozen_asset": c["frozen_asset"],
                        "frozen_amount": c["frozen_amount"],
                        "created_block": c["created_block"],
                        "blocks_elapsed": el,
                        "blocks_until_ready": max(0, MEV_COMMIT_MIN_BLOCKS-el),
                        "blocks_until_expire": max(0, MEV_COMMIT_MAX_BLOCKS-el),
                        "status": c["status"], "order_id": c["order_id"]})
        out.sort(key=lambda x: x["created_block"], reverse=True)
        return out

    def portfolio(self, addr):
        out = {}
        for aid, amt in self.state.balances.get(addr, {}).items():
            if amt == 0: continue
            a = self.registry.get_asset(aid)
            out[aid] = {"amount": amt,
                        "available": self.state.available(addr, aid),
                        "frozen": self.state.frozen.get(addr, {}).get(aid, 0.0),
                        "asset": a.to_dict() if a else None}
        return out

    def slashing_report(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT validator, reason, block_idx, ts, evidence "
                                "FROM slashing ORDER BY ts DESC").fetchall()
        return [{"validator": r[0], "reason": r[1], "block_index": r[2],
                 "timestamp": r[3],
                 "evidence": json.loads(r[4]) if r[4] else None} for r in rows]

    def finality_report(self):
        return {"finalized_height": self.finality.finalized_height,
                "interval": self.finality.interval,
                "threshold": self.finality.threshold}

    def to_dict(self):
        return {"network_id": NETWORK_ID, "length": len(self.chain),
                "chain": [b.to_dict() for b in self.chain]}

    # ---------- high-level API ----------
    def place_order(self, identity, base, quote, side, price, amount):
        tx = Transaction.build("order_place", base, identity["address"],
                               identity["address"], amount,
                               self.state.nonce(identity["address"]),
                               identity["spend_secret_key"],
                               identity["public_key"],
                               {"quote": quote, "side": side, "price": float(price)})
        return self.add_transaction(tx)

    def cancel_order(self, identity, order_id):
        tx = Transaction.build("order_cancel", NATIVE_ASSET,
                               identity["address"], identity["address"], 0,
                               self.state.nonce(identity["address"]),
                               identity["spend_secret_key"],
                               identity["public_key"],
                               {"order_id": order_id})
        return self.add_transaction(tx)

    def commit_order(self, identity, base, quote, side, price, amount, salt=None):
        if salt is None: salt = secrets.token_hex(16)
        addr = identity["address"]
        frozen = (price * amount) if side == "buy" else amount
        ch = self._commit_hash(side, base, quote, price, amount, salt, addr)
        tx = Transaction.build("order_commit", base, addr, addr, frozen,
                               self.state.nonce(addr),
                               identity["spend_secret_key"],
                               identity["public_key"],
                               {"commit_hash": ch, "side": side,
                                "base": base, "quote": quote})
        r = self.add_transaction(tx)
        if r.get("ok"):
            r.update({"commit_hash": ch, "salt": salt, "price": price,
                      "amount": amount, "side": side, "base": base, "quote": quote})
        return r

    def reveal_order(self, identity, ch, salt, base, quote, side, price, amount):
        addr = identity["address"]
        tx = Transaction.build("order_reveal", base, addr, addr, amount,
                               self.state.nonce(addr),
                               identity["spend_secret_key"],
                               identity["public_key"],
                               {"commit_hash": ch, "salt": salt,
                                "side": side, "quote": quote, "price": price})
        return self.add_transaction(tx)


# =====================================================================
# BLOCKCHAIN DB (adapter p2p)
# =====================================================================
class BlockchainDB:
    def __init__(self, bc): self.bc = bc
    def height(self): return self.bc.height
    def tip_hash(self): return self.bc.tip_hash
    def headers(self, start, count):
        return [b.to_wire_header() for b in self.bc.chain[start:start+count]]
    def get_block(self, height):
        c = self.bc.chain
        return c[height].to_wire_dict() if 0 <= height < len(c) else None
    def has_mempool(self, txid):
        return any(Transaction.hash(tx) == txid for tx in self.bc.pending)
    def get_mempool_tx(self, txid):
        for tx in self.bc.pending:
            if Transaction.hash(tx) == txid: return dict(tx)
        return None
    def tx_id(self, tx): return Transaction.hash(tx)


# =====================================================================
# P2P
# =====================================================================
class Peer:
    __slots__ = ("host","port","reader","writer","outbound",
                 "last_seen","height","protocol","send_lock","via_ngrok")
    def __init__(self, host, port, reader, writer, outbound, via_ngrok=False):
        self.host = host; self.port = port
        self.reader = reader; self.writer = writer
        self.outbound = outbound; self.via_ngrok = via_ngrok
        self.last_seen = time.time(); self.height = 0; self.protocol = 0
        self.send_lock = asyncio.Lock()
    @property
    def key(self): return (self.host, self.port)
    def mark_alive(self): self.last_seen = time.time()
    async def send(self, msg):
        async with self.send_lock:
            self.writer.write(orjson.dumps(msg) + b"\n")
            await self.writer.drain()
    async def close(self):
        try: self.writer.close(); await self.writer.wait_closed()
        except Exception: pass


class PeerManager:
    def __init__(self, bc, port: int):
        self.bc = bc; self.port = port
        self.peers: dict = {}
        self.known: dict = {}
        self.connecting: set = set()
        self.syncing: set = set()
        self.inv_cache: dict = {}
        self._stop = asyncio.Event()
        self._server = None
        self._tasks = []
        self.public_endpoint = None
        self._ngrok_tunnel = None

    def _remember_known(self, host, port):
        self.known[(host, port)] = time.time()
        if len(self.known) > MAX_KNOWN:
            oldest = min(self.known.items(), key=lambda kv: kv[1])[0]
            self.known.pop(oldest, None)

    def _is_self(self, host, port):
        if self.public_endpoint and (host, port) == self.public_endpoint:
            return True
        if port != self.port: return False
        return host in ("127.0.0.1","0.0.0.0","localhost","::1")

    def _gc(self):
        cutoff = time.time() - INV_CACHE_TTL
        self.inv_cache = {k: v for k, v in self.inv_cache.items() if v > cutoff}

    def _my_advertised_endpoint(self):
        return self.public_endpoint if self.public_endpoint else ("", self.port)

    # NGROK
    def start_ngrok(self):
        if not NGROK_ENABLED:
            print("[ngrok] desativado (BRN_NGROK=0)"); return
        try:
            from pyngrok import ngrok, conf
        except ImportError:
            print("[ngrok] pyngrok nao instalado — pip install pyngrok"); return
        try:
            if NGROK_TOKEN: conf.get_default().auth_token = NGROK_TOKEN
            conf.get_default().region = NGROK_REGION
            tunnel = ngrok.connect(self.port, "tcp")
            m = re.match(r"tcp://([^:]+):(\d+)", tunnel.public_url)
            if m:
                self.public_endpoint = (m.group(1), int(m.group(2)))
                self._ngrok_tunnel = tunnel
                print(f"[ngrok] túnel TCP ativo em {self.public_endpoint}")
                print(f"[ngrok] BRN_HARDCODED_SEEDS={self.public_endpoint[0]}:{self.public_endpoint[1]}")
        except Exception as e:
            print(f"[ngrok] erro: {e}")

    def stop_ngrok(self):
        if self._ngrok_tunnel:
            try:
                from pyngrok import ngrok
                ngrok.disconnect(self._ngrok_tunnel.public_url)
            except Exception: pass

    # server
    async def serve(self):
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self.port)
        print(f"[P2P] escutando em 0.0.0.0:{self.port}")
        async with self._server:
            await self._server.serve_forever()

    async def _handle(self, reader, writer):
        peername = writer.get_extra_info("peername")
        ip = peername[0] if peername else "?"
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=HANDSHAKE_TIMEOUT)
            msg = orjson.loads(line) if line else None
        except Exception:
            writer.close(); return
        if not msg or msg.get("type") != "ping":
            writer.close(); return
        remote_port = int(msg.get("port", 0)) or self.port
        remote_host = msg.get("host", "") or ip
        mh, mp = self._my_advertised_endpoint()
        try:
            writer.write(orjson.dumps({"type": "pong", "version": PROTOCOL_VERSION,
                "port": mp, "host": mh, "height": self.bc.height,
                "tip": self.bc.tip_hash}) + b"\n")
            await writer.drain()
        except Exception:
            writer.close(); return
        if self._is_self(remote_host, remote_port): writer.close(); return
        key = (remote_host, remote_port)
        if key in self.peers or len(self.peers) >= MAX_PEERS:
            writer.close(); return
        peer = Peer(remote_host, remote_port, reader, writer, outbound=False,
                    via_ngrok=("ngrok" in remote_host))
        peer.height = int(msg.get("height", 0))
        peer.protocol = msg.get("version", 0)
        self.peers[key] = peer
        self._remember_known(remote_host, remote_port)
        print(f"[P2P] +peer in  {remote_host}:{remote_port} h={peer.height}")
        try: await self._peer_loop(peer)
        finally:
            self.peers.pop(key, None); await peer.close()
            print(f"[P2P] -peer in  {remote_host}:{remote_port}")

    async def _peer_loop(self, peer):
        while not self._stop.is_set():
            try:
                line = await peer.reader.readline()
                if not line: break
                msg = orjson.loads(line)
            except Exception: break
            peer.mark_alive()
            try: reply = await self._dispatch(msg, peer)
            except Exception as e:
                print(f"[P2P] dispatch erro {peer.key}: {e}"); break
            if reply is not None:
                try: await peer.send(reply)
                except Exception: break

    async def _dispatch(self, msg, peer):
        t = msg.get("type")
        if t == "ping":
            mh, mp = self._my_advertised_endpoint()
            return {"type": "pong", "version": PROTOCOL_VERSION,
                    "port": mp, "host": mh, "height": self.bc.height,
                    "tip": self.bc.tip_hash}
        if t == "pong":
            peer.height = int(msg.get("height", peer.height))
            peer.protocol = msg.get("version", peer.protocol)
            peer.mark_alive(); return None
        if t == "get_headers":
            start = int(msg.get("start", 0))
            return {"type": "headers",
                    "items": self.bc.db.headers(start, SYNC_BATCH)}
        if t == "get_block":
            h = int(msg.get("height", -1))
            b = self.bc.db.get_block(h)
            return {"type": "block", "block": b} if b else None
        if t == "get_addr":
            peers = []
            if self.public_endpoint:
                peers.append([self.public_endpoint[0], self.public_endpoint[1]])
            for k, p in self.peers.items():
                if k != peer.key: peers.append([p.host, p.port])
            return {"type": "addr", "peers": peers[:50]}
        if t == "addr":
            for entry in msg.get("peers", []):
                try:
                    host = str(entry[0]); port = int(entry[1])
                    if self._is_self(host, port): continue
                    self._remember_known(host, port)
                except Exception: continue
            return None
        if t == "inv_tx":
            txid = msg.get("txid")
            if not txid or self.inv_cache.get(txid): return None
            self.inv_cache[txid] = time.time()
            if self.bc.db.has_mempool(txid): return None
            return {"type": "get_tx", "txid": txid}
        if t == "get_tx":
            tx = self.bc.db.get_mempool_tx(msg.get("txid", ""))
            return {"type": "tx", "tx": tx} if tx else None
        if t == "tx":
            tx = msg.get("tx")
            if not tx: return None
            ok, _ = self.bc.submit_tx(tx)
            if ok:
                txid = self.bc.db.tx_id(tx)
                self.inv_cache[txid] = time.time()
                asyncio.create_task(self.broadcast(
                    {"type": "inv_tx", "txid": txid}, exclude=peer.key))
            return None
        if t == "inv_block":
            h = int(msg.get("height", -1)); bh = msg.get("hash", "")
            if h < 0 or (bh and self.inv_cache.get(bh)): return None
            if bh: self.inv_cache[bh] = time.time()
            if h <= self.bc.height: return None
            return {"type": "get_block", "height": h}
        if t == "block":
            block = msg.get("block")
            if not block: return None
            ok, _ = self.bc.accept_block(block)
            if ok:
                h = block.get("height", -1); bh = block.get("hash", "")
                if bh: self.inv_cache[bh] = time.time()
                asyncio.create_task(self.broadcast(
                    {"type": "inv_block", "height": h, "hash": bh},
                    exclude=peer.key))
            return None
        if t == "error": return None
        return {"type": "error", "message": "desconhecido"}

    async def connect(self, host, port, for_sync=True):
        if self._stop.is_set(): return False
        key = (host, port)
        if self._is_self(host, port): return False
        if key in self.connecting or key in self.peers: return False
        last = self.known.get(key, 0)
        if last > 0 and time.time() - last < RECONNECT_BACKOFF: return False
        self.connecting.add(key)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT)
        except Exception:
            self._remember_known(host, port); return False
        finally: self.connecting.discard(key)
        mh, mp = self._my_advertised_endpoint()
        try:
            writer.write(orjson.dumps({"type": "ping", "version": PROTOCOL_VERSION,
                "port": mp, "host": mh, "height": self.bc.height,
                "tip": self.bc.tip_hash}) + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=HANDSHAKE_TIMEOUT)
            pong = orjson.loads(line) if line else None
        except Exception:
            try: writer.close()
            except Exception: pass
            self._remember_known(host, port); return False
        if not pong or pong.get("type") != "pong":
            try: writer.close()
            except Exception: pass
            return False
        rh = int(pong.get("height", 0)); rv = pong.get("version", 0)
        lh = self.bc.height
        print(f"[P2P] conectado {host}:{port} (remoto h={rh}, local={lh}, v={rv})")
        if key not in self.peers and len(self.peers) < MAX_PEERS:
            peer = Peer(host, port, reader, writer, outbound=True,
                        via_ngrok=("ngrok" in host))
            peer.height = rh; peer.protocol = rv
            self.peers[key] = peer
            self._remember_known(host, port)
            asyncio.create_task(self._outbound_peer_loop(peer))
        else:
            try: writer.close()
            except Exception: pass
        if for_sync and rh > lh:
            asyncio.create_task(self.sync_from(host, port))
        return True

    async def _outbound_peer_loop(self, peer):
        try: await self._peer_loop(peer)
        finally:
            self.peers.pop(peer.key, None); await peer.close()
            print(f"[P2P] -peer out {peer.host}:{peer.port}")

    async def sync_from(self, host, port):
        key = (host, port)
        if key in self.syncing: return
        self.syncing.add(key)
        try: await self._do_sync(host, port)
        finally: self.syncing.discard(key)

    async def _do_sync(self, host, port):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT)
        except Exception: return
        try:
            start = 0; all_h = []; attempts = 0
            while attempts < MAX_SYNC_ATTEMPTS:
                try:
                    writer.write(orjson.dumps({"type": "get_headers",
                        "start": start}) + b"\n")
                    await writer.drain()
                    line = await asyncio.wait_for(reader.readline(),
                                                   timeout=HEADERS_TIMEOUT)
                    resp = orjson.loads(line) if line else None
                except Exception:
                    attempts += 1; continue
                if not resp or resp.get("type") != "headers": break
                items = resp.get("items", [])
                if not items: break
                all_h.extend(items)
                start = int(items[-1].get("height", start)) + 1
                if len(items) < SYNC_BATCH: break
                attempts = 0
            print(f"[SYNC] {len(all_h)} headers de {host}:{port}")
            fork = 0
            for h in all_h:
                hh = int(h.get("height", 0))
                local = self.bc.db.get_block(hh)
                if local and local.get("hash") == h.get("hash"): fork = hh
                else: break
            acc = 0
            for h in all_h:
                hh = int(h.get("height", -1))
                if hh <= fork: continue
                try:
                    writer.write(orjson.dumps({"type": "get_block",
                        "height": hh}) + b"\n")
                    await writer.drain()
                    line = await asyncio.wait_for(reader.readline(),
                                                   timeout=BLOCK_TIMEOUT)
                    resp = orjson.loads(line) if line else None
                except Exception: break
                if not resp or resp.get("type") != "block": break
                block = resp.get("block")
                if not block: break
                ok, why = self.bc.accept_block(block)
                if not ok:
                    print(f"[SYNC] bloco {hh} rejeitado: {why}"); break
                acc += 1
            print(f"[SYNC] +{acc} blocos, altura {self.bc.height}")
        finally:
            try: writer.close(); await writer.wait_closed()
            except Exception: pass

    async def broadcast(self, msg, exclude=None):
        if not self.peers: return
        dead = []
        for key, peer in list(self.peers.items()):
            if exclude is not None and key == exclude: continue
            try: await peer.send(msg)
            except Exception: dead.append(key)
        for k in dead:
            p = self.peers.pop(k, None)
            if p: await p.close()

    async def resolve_seeds(self):
        for h, p in HARDCODED_SEEDS: self._remember_known(h, p)
        loop = asyncio.get_event_loop()
        for seed in DNS_SEEDS:
            try:
                infos = await loop.getaddrinfo(seed, None, type=0)
                for info in infos: self._remember_known(info[4][0], self.port)
            except Exception: continue
        for peer in list(self.peers.values()):
            try: await peer.send({"type": "get_addr"})
            except Exception: pass

    async def maintenance_loop(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=PING_INTERVAL); break
            except asyncio.TimeoutError: pass
            self._gc()
            now = time.time()
            for key, peer in list(self.peers.items()):
                if now - peer.last_seen > PEER_TIMEOUT:
                    print(f"[P2P] timeout {key}")
                    self.peers.pop(key, None); await peer.close(); continue
                try:
                    mh, mp = self._my_advertised_endpoint()
                    await peer.send({"type": "ping", "version": PROTOCOL_VERSION,
                                     "port": mp, "host": mh,
                                     "height": self.bc.height})
                except Exception:
                    self.peers.pop(key, None); await peer.close()
            if len(self.peers) < MAX_PEERS:
                cands = sorted(self.known.items(), key=lambda kv: kv[1])
                for (h, p), _ts in cands[:MAX_PEERS]:
                    if len(self.peers) >= MAX_PEERS: break
                    if self._is_self(h, p): continue
                    if (h, p) in self.peers or (h, p) in self.connecting: continue
                    asyncio.create_task(self.connect(h, p))

    async def start(self):
        self.start_ngrok()
        await self.resolve_seeds()
        self._tasks.append(asyncio.create_task(self.serve()))
        self._tasks.append(asyncio.create_task(self.maintenance_loop()))
        for h, p in list(self.known.keys())[:5]:
            if self._is_self(h, p): continue
            asyncio.create_task(self.connect(h, p))
        await self._stop.wait()

    async def stop(self):
        print("[P2P] parando...")
        self._stop.set()
        for p in list(self.peers.values()): await p.close()
        self.peers.clear()
        for t in self._tasks: t.cancel()
        if self._server:
            self._server.close()
            try: await self._server.wait_closed()
            except Exception: pass
        self.stop_ngrok()


# =====================================================================
# NODE (wrapper thread)
# =====================================================================
def load_identity():
    if WalletManager is None:
        raise RuntimeError("cripto_wallet indisponivel.")
    if hasattr(WalletManager, "load_node_identity"):
        return WalletManager.load_node_identity()
    sk = os.environ.get("BRN_NODE_SK"); pk = os.environ.get("BRN_NODE_PK")
    if not sk or not pk:
        if hasattr(WalletManager, "generate_keypair"):
            sk, pk = WalletManager.generate_keypair()
        else: raise RuntimeError("WalletManager sem generate_keypair.")
    return {"address": WalletManager.address_from_public_key(pk),
            "public_key": pk, "spend_secret_key": sk}


class Node:
    def __init__(self, identity=None, enable_miner=None, enable_auto_reveal=True):
        self.identity = identity or load_identity()
        self.chain = Blockchain(node_identity=self.identity)
        self.chain.db = BlockchainDB(self.chain)   # adapter p2p
        self.pm = PeerManager(self.chain, port=P2P_PORT)
        self.enable_miner = MINER_ENABLED if enable_miner is None else enable_miner
        self.enable_auto_reveal = enable_auto_reveal
        self.running = False
        self._loop = None; self._thread = None; self._stop = None
        self.pending_commits = {}
        self.public_endpoint = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="brn-node")
        self._thread.start()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try: self._loop.run_until_complete(self._main())
        except Exception as e: print(f"[node] erro: {e}")
        finally:
            try: self._loop.close()
            except Exception: pass

    async def _main(self):
        self.running = True
        self._stop = asyncio.Event()

        async def sync_endpoint():
            while not self._stop.is_set():
                if self.pm.public_endpoint and not self.public_endpoint:
                    self.public_endpoint = self.pm.public_endpoint
                await asyncio.sleep(0.5)

        async def miner():
            if not self.enable_miner: return
            loop = asyncio.get_event_loop()
            while not self._stop.is_set():
                block = await loop.run_in_executor(None, self.chain.produce_block)
                if block is None:
                    await asyncio.sleep(1.height); continue
                try:
                    await self.pm.b
roadcast({"type": "inv       _block",
                        "height": block.index, "hash": block.hash})
                except Exception as e: print(f for"[node] broadcast ch falhou: {e}")
                await self._maybe_reveal()
                await asyncio.sleep(MINER_INTERVAL)

        async def auto_reveal():
            if not self.enable_auto_reveal: return
            while not self._stop.is_set():
                await self._maybe_reveal()
                await asyncio.sleep(1.0)

        try:
            await asyncio.gather(self.pm.start(), miner(),
                                 auto_reveal(), sync_endpoint())
        except asyncio.CancelledError: pass

    def stop(self):
        self.running = False
        if self._stop and self._loop:
            try:
                self._stop.set()
                asyncio.run_coroutine_threadsafe(self.pm.stop(), self._loop)
            except Exception: pass

    async def _maybe_reveal(self):
        cur = self.chain, info in list(self.pending_commits.items()):
            if cur - info["block"] >= MEV_COMMIT_MIN_BLOCKS:
                r = self.chain.reveal_order(self.identity, ch, info["salt"],
                                             info["base"], info["quote"],
                                             info["side"], info["price"], info["amount"])
                if r.get("ok"):
                    print(f"[reveal] {r.get('tx_hash','')[:16]}... ok")
                    del self.pending_commits[ch]
                elif ("aguarde" not in r.get("msg", "") and
                      "duplicada" not in r.get("msg", "")):
                    print(f"[reveal] falhou: {r.get('msg')}")
                    del self.pending_commits[ch]

    def commit_order(self, side, price, amount, base=None, quote=None):
        base = base or BASE_ASSET; quote = quote or QUOTE_ASSET
        r = self.chain.commit_order(self.identity, base, quote, side, price, amount)
        if r.get("ok"):
            self.pending_commits[r["commit_hash"]] = {
                "salt": r["salt"], "side": side, "base": base, "quote": quote,
                "price": price, "amount": amount, "block": self.chain.height}
        return r

    def reveal_order(self, ch, salt, side, price, amount, base=None, quote=None):
        base = base or BASE_ASSET; quote = quote or QUOTE_ASSET
        return self.chain.reveal_order(self.identity, ch, salt, base, quote,
                                        side, price, amount)

    def place_order(self, side, price, amount, base=None, quote=None):
        base = base or BASE_ASSET; quote = quote or QUOTE_ASSET
        return self.chain.place_order(self.identity, base, quote, side, price, amount)

    def cancel_order(self, order_id):
        return self.chain.cancel_order(self.identity, order_id)

    def order_book(self, depth=8, base=None, quote=None):
        base = base or BASE_ASSET; quote = quote or QUOTE_ASSET
        return self.chain.order_book(base, quote, depth=depth)

    def my_orders(self): return self.chain.my_orders(self.identity["address"])
    def my_trades(self, limit=20): return self.chain.my_trades(self.identity["address"], limit)
    def my_commits(self): return self.chain.my_commits(self.identity["address"])
    def portfolio(self): return self.chain.portfolio(self.identity["address"])
    @property
    def height(self): return self.chain.height
    @property
    def tip_hash(self): return self.chain.tip_hash


# =====================================================================
# CLI
# =====================================================================
HELP = """
Comandos:
  commit <side> <price> <amount>         cria commit (auto-reveal)
  reveal <hash> <salt> <side> <p> <amt>  reveal manual
  commits                                meus commits
  place  <side> <price> <amount>         ordem direta (SEM MEV)
  cancel <order_id>                      cancela ordem
  book [depth]                           order book
  orders                                 minhas ordens abertas
  trades                                 meus trades
  portfolio                              saldos
  height                                 altura atual
  ngrok                                  endpoint NGROK
  peers                                  peers conectados
  help | quit
"""


def cli_loop(node: Node):
    print(HELP)
    while node.running:
        try:
            line = input("brn> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line: continue
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd in ("quit","exit"): break
            elif cmd == "help": print(HELP)
            elif cmd == "commit" and len(parts) == 4:
                r = node.commit_order(parts[1], float(parts[2]), float(parts[3]))
                if r.get("ok"):
                    print(f"[commit] ok | hash={r['commit_hash']}")
                    print(f"  salt: {r['salt']}")
                else:
                    print(f"[commit] erro: {r.get('msg')}")
            elif cmd == "reveal" and len(parts) == 6:
                r = node.reveal_order(parts[1], parts[2], parts[3],
                                       float(parts[4]), float(parts[5]))
                print(f"[reveal] {r}")
            elif cmd == "commits":
                for c in node.my_commits():
                    print(f"  {c['status']:>9} | {c['side']:>4} | "
                          f"pronto em {c['blocks_until_ready']}b | "
                          f"expira em {c['blocks_until_expire']}b | {c['commit_hash']}")
            elif cmd == "place" and len(parts) == 4:
                r = node.place_order(parts[1], float(parts[2]), float(parts[3]))
                print(f"[place] {r}")
            elif cmd == "cancel" and len(parts) == 2:
                print(f"[cancel] {node.cancel_order(parts[1])}")
            elif cmd == "book":
                depth = int(parts[1]) if len(parts) > 1 else 8
                b = node.order_book(depth=depth)
                print(f"  --- ASKS ({QUOTE_ASSET} → {BASE_ASSET}) ---")
                for a in reversed(b["asks"]):
                    print(f"   {a['price']:>12.6f}  {a['amount']:>12.4f}  {a['owner']}")
                print(f"   mid={b['mid']:.6f}  spread={b['spread']:.6f}")
                print(f"  --- BIDS ({QUOTE_ASSET} → {BASE_ASSET}) ---")
                for x in b["bids"]:
                    print(f"   {x['price']:>12.6f}  {x['amount']:>12.4f}  {x['owner']}")
            elif cmd == "orders":
                for o in node.my_orders():
                    print(f"  {o['side']:>4} {o['price']:.6f} rem={o['remaining']:.4f} "
                          f"| {o['order_id'][:16]}... [{o['status']}]")
            elif cmd == "trades":
                for t in node.my_trades(10):
                    print(f"  {t['role']:>4} {t['price']:.6f} {t['amount']:.4f} "
                          f"= {t['cost']:.4f} {t['quote']}")
            elif cmd == "portfolio":
                print(json.dumps(node.portfolio(), indent=2, default=str))
            elif cmd == "height":
                print(f"  altura={node.height}  tip={node.tip_hash[:16]}")
            elif cmd == "ngrok":
                if node.public_endpoint:
                    h, p = node.public_endpoint
                    print(f"  NGROK ativo: {h}:{p}")
                    print(f"  Peers devem usar: BRN_HARDCODED_SEEDS={h}:{p}")
                else: print("  NGROK inativo")
            elif cmd == "peers":
                if not node.pm.peers: print("  (sem peers)")
                for k, peer in node.pm.peers.items():
                    ago = int(time.time() - peer.last_seen)
                    d = "out" if peer.outbound else "in "
                    print(f"  {d} {k[0]}:{k[1]} h={peer.height} last={ago}s")
            else: print(f"[cli] desconhecido: {cmd}")
        except Exception as e:
            print(f"[cli] erro: {e}")


# =====================================================================
# EXPLORER
# =====================================================================
def short(addr):
    if not addr: return "—"
    return addr[:14] + "…" + addr[-8:] if len(addr) > 24 else addr

def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def tx_summary(tx):
    t = tx.get("type", "?"); md = tx.get("metadata", {})
    if t == "transfer":
        return f"transfer {tx.get('amount',0):.4f} {tx.get('asset_id','?')} | {short(tx['from'])} → {short(tx['to'])}"
    if t == "issue":
        return f"issue    {tx.get('amount',0):.4f} {tx.get('asset_id','?')} → {short(tx['to'])}"
    if t == "redeem":
        return f"redeem   {tx.get('amount',0):.4f} {tx.get('asset_id','?')} de {short(tx['from'])}"
    if t == "order_place":
        return f"order    {md.get('side','?').upper():>4} {tx.get('amount',0):.4f} {tx.get('asset_id','?')} @ {md.get('price',0):.6f} {md.get('quote','?')}"
    if t == "order_commit":
        return f"commit   {md.get('side','?').upper():>4} hash={md.get('commit_hash','')[:12]}…"
    if t == "order_reveal":
        return f"reveal   {md.get('side','?').upper():>4} {tx.get('amount',0):.4f} {tx.get('asset_id','?')} @ {md.get('price',0):.6f} {md.get('quote','?')}"
    if t == "order_cancel":
        return f"cancel   order={md.get('order_id','')[:12]}…"
    if t == "asset_create":
        return f"asset    create {md.get('asset_id','?')}"
    return f"{t} | {short(tx.get('from',''))} → {short(tx.get('to',''))}"

def print_report():
    if not Path(DB_PATH).exists():
        print(f"[explorer] {DB_PATH} ainda não existe."); return
    try: conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:
        print(f"[explorer] erro: {e}"); return
    print("\n" + "=" * 100)
    print(f" EXPLORER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
    total_tx = 0; total_vol = 0.0
    print(f"\n BLOCOS ({len(rows)}):")
    for (data,) in rows:
        blk = json.loads(data); txs = blk.get("transactions", [])
        total_tx += len(txs)
        miner = blk.get("miner") or blk.get("validator") or ""
        print(f"  #{blk['index']:>4} | {fmt_time(blk['timestamp'])} | "
              f"miner={short(miner):22} | diff={blk.get('difficulty','-')} "
              f"nonce={blk.get('nonce','-')} | {len(txs)} tx | "
              f"{blk.get('hash','')[:16]}…")
        for tx in txs:
            total_vol += tx.get("amount", 0.0) or 0.0
            print(f"          → {tx_summary(tx)}")
    mrows = conn.execute("SELECT data FROM mempool ORDER BY ts ASC").fetchall()
    print(f"\n MEMPOOL ({len(mrows)} pendentes):")
    if not mrows: print("   (vazia)")
    for (data,) in mrows:
        tx = json.loads(data)
        print(f"   {tx_summary(tx)} | nonce={tx.get('nonce','?')}")
    srows = conn.execute("SELECT validator, reason, block_idx, ts FROM slashing "
                         "ORDER BY ts DESC").fetchall()
    print(f"\n SLASHING ({len(srows)} banidos):")
    if not srows: print("   (nenhum)")
    for r in srows:
        print(f"   {short(r[0])} | bloco#{r[2]} | {r[1]} | {fmt_time(r[3])}")
    print("\n" + "-" * 100)
    print(f" Total de blocos ....: {len(rows)}")
    print(f" Total de tx ........: {total_tx}")
    print(f" Volume confirmado ..: {total_vol:.4f}")
    print(f" Pendentes ..........: {len(mrows)}")
    print(f" Slashed ............: {len(srows)}")
    print("-" * 100)

def report_loop():
    time.sleep(5)
    while True:
        try: print_report()
        except Exception as e: print(f"[explorer] erro: {e}")
        time.sleep(REPORT_INTERVAL)


# =====================================================================
# ENTRY POINTS
# =====================================================================
def wait_ngrok(node, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if node.public_endpoint: return True
        time.sleep(0.2)
    return False

def print_header(node):
    print("=" * 72)
    print(" BRN Node — CLI (CLOB + MEV + PoW + P2P)")
    print("=" * 72)
    print(f" Endereço:       {node.identity['address']}")
    print(f" Porta P2P:      {node.pm.port}")
    print(f" Par padrão:     {BASE_ASSET}/{QUOTE_ASSET}")
    print(f" Minerando:      {'sim' if node.enable_miner else 'não'}")
    if node.public_endpoint:
        h, p = node.public_endpoint
        print(f" Endpoint NGROK: {h}:{p}")
        print(f"   BRN_HARDCODED_SEEDS={h}:{p}")
    elif NGROK_ENABLED:
        print(" Endpoint NGROK: aguardando...")
    else:
        print(" Endpoint NGROK: desativado (BRN_NGROK=0)")
    print("=" * 72)


def run_node():
    node = Node()
    print("[main] subindo nó...")
    node.start()
    if NGROK_ENABLED: wait_ngrok(node, 10.0)
    print_header(node)
    try: cli_loop(node)
    finally:
        print("\n[main] encerrando...")
        node.stop(); time.sleep(0.5)
        print("[main] finalizado.")


def run_explorer():
    print("=" * 72)
    print(" BRN Explorer — nó + dashboard")
    print("=" * 72)
    node = Node()
    threading.Thread(target=node.start, daemon=True, name="node").start()
    threading.Thread(target=report_loop, daemon=True, name="report").start()
    if NGROK_ENABLED:
        for _ in range(50):
            if node.public_endpoint: break
            time.sleep(0.2)
    time.sleep(1)
    if node.public_endpoint:
        h, p = node.public_endpoint
        print(f"\n NGROK público: {h}:{p}")
        print(f"   BRN_HARDCODED_SEEDS={h}:{p}")
    print(f" Banco SQLite: {os.path.abspath(DB_PATH)}")
    print(f" Relatório a cada {REPORT_INTERVAL}s.")
    print(" Ctrl+C para encerrar.\n")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\n[explorer] encerrando…")
        node.stop(); sys.exit(0)


def run_both():
    node = Node()
    print("[both] subindo nó + dashboard...")
    node.start()
    threading.Thread(target=report_loop, daemon=True, name="report").start()
    if NGROK_ENABLED: wait_ngrok(node, 10.0)
    print_header(node)
    try: cli_loop(node)
    finally:
        print("\n[both] encerrando...")
        node.stop(); time.sleep(0.5)
        print("[both] finalizado.")


def main():
    ap = argparse.ArgumentParser(description="BRN Node")
    ap.add_argument("mode", nargs="?", default="node",
                    choices=["node","explorer","both"])
    args = ap.parse_args()
    if args.mode == "node": run_node()
    elif args.mode == "explorer": run_explorer()
    elif args.mode == "both": run_both()


if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print()