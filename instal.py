#!/usr/bin/env python3
"""
instal.py — instalador completo do BRN Produção v3.1.

Gera o projeto brn-prod/ com:
  - Blockchain UTXO + PoW com reajuste
  - P2P autenticado (HMAC) com framing binário
  - HD wallet BIP-39/32/44
  - Carteira Argon2id + AES-GCM
  - Explorador Flask com rate-limit, CSP, token de API
  - NGROK seguro (token via env / .env / prompt)
  - Checkpoints assinados + reorg por chainwork
  - Métricas Prometheus, Docker non-root, CI completo

Uso:
    python instal.py [destino]
    python instal.py brn-prod
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "brn-prod").resolve()
F: dict[str, str] = {}


# ============================================================ raiz
F[".gitignore"] = r'''
wallet*.brn
wallet*.json
*.db
*.db-shm
*.db-wal
*.sqlite3
.api_token
.env
.env.*
!.env.example
*.pem
*.key
secrets/
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/
*.log
.DS_Store
dist/
build/
*.egg-info/
'''.lstrip("\n")

F["requirements.txt"] = r'''
coincurve==20.0.0
cryptography==43.0.3
argon2-cffi==23.1.0
mnemonic==0.21
pydantic==2.9.2
pydantic-settings==2.5.2
flask==3.0.3
flask-limiter==3.8.0
flask-talisman==1.1.0
prometheus-client==0.21.0
python-dotenv==1.0.1
ngrok==1.4.0
gunicorn==23.0.0
orjson==3.10.7
'''.lstrip("\n")

F["requirements-dev.txt"] = r'''
-r requirements.txt
pytest==8.3.3
pytest-cov==6.0.0
pytest-asyncio==0.24.0
ruff==0.6.9
mypy==1.11.2
bandit==1.7.10
pip-audit==2.7.1
'''.lstrip("\n")

F["pyproject.toml"] = r'''
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "brn"
version = "3.1.0"
description = "BRN — blockchain UTXO + explorador"
requires-python = ">=3.11"
readme = "README.md"

[project.scripts]
brn = "brn.cli:main"

[tool.setuptools.packages.find]
include = ["brn*"]

[tool.pytest.ini_options]
addopts = "-q --cov=brn --cov-report=term-missing"
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py311"
line-length = 88
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
warn_unused_ignores = true
'''.lstrip("\n")

F[".env.example"] = r'''
# Nunca coloque valores reais aqui. Copie para .env e preencha.
BRN_NETWORK=mainnet
BRN_DATA_DIR=./data
BRN_P2P_HOST=0.0.0.0
BRN_P2P_PORT=6001
BRN_EXPLORER_HOST=127.0.0.1
BRN_EXPLORER_PORT=8080
BRN_API_TOKEN=
BRN_NETWORK_KEY=
BRN_DIFFICULTY=4
BRN_SEEDS=
BRN_LOG_LEVEL=INFO
BRN_RATE_LIMIT=120/minute
BRN_PUBLIC_TUNNEL=0
BRN_FAUCET_ADDRESS=
BRN_FAUCET_AMOUNT=100
BRN_FAUCET_COOLDOWN=3600
NGROK_AUTHTOKEN=
NGROK_DOMAIN=
'''.lstrip("\n")

F["Makefile"] = r'''
.PHONY: install dev test lint sast audit ci run clean key api-token fmt typecheck

install:
	python -m pip install -r requirements.txt
dev:
	python -m pip install -r requirements-dev.txt
test:
	pytest -q --cov=brn --cov-report=term-missing
lint:
	ruff check brn tests
fmt:
	ruff check --fix brn tests
typecheck:
	mypy brn
sast:
	bandit -r brn -ll
audit:
	pip-audit -r requirements.txt
ci: lint typecheck test sast audit
run:
	python -m brn node --with-explorer
key:
	python -c "import secrets;print('BRN_NETWORK_KEY='+secrets.token_hex(32))"
api-token:
	python -c "import secrets;print('BRN_API_TOKEN='+secrets.token_urlsafe(32))"
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__ */__pycache__ .coverage htmlcov dist build *.egg-info
'''.lstrip("\n")

# ============================================================ brn/
F["brn/__init__.py"] = '__version__ = "3.1.0"\n'

F["brn/__main__.py"] = r'''
"""Permite executar: python -m brn <cmd> ..."""
from .cli import main

if __name__ == "__main__":
    main()
'''.lstrip("\n")

F["brn/constants.py"] = r'''
"""Constantes do protocolo BRN."""
from __future__ import annotations

PROTOCOL_VERSION = 3
NETWORK_MAGIC = b"BRNv3"

REWARD = 50 * 10**8
HALVING = 210_000
MAX_MEMPOOL = 5_000
MEMPOOL_TTL = 24 * 3600
MAX_BLOCK_TXS = 5_000
MAX_INPUTS = 1_000
MAX_OUTPUTS = 1_000
COINBASE_MATURITY = 100

ADDRESS_PREFIX = "brn1"
DOMAIN_TX = b"BRN-TX-v3\x00"
DOMAIN_BLOCK = b"BRN-BLOCK-v3\x00"
DOMAIN_CKPT = b"BRN-CKPT-v3\x00"
DOMAIN_P2P = b"BRN-P2P-v3\x00"

P2P_MAX_MSG = 2 * 1024 * 1024
P2P_READ_TIMEOUT = 30.0
P2P_HANDSHAKE_TIMEOUT = 5.0
P2P_PING_INTERVAL = 30.0
P2P_PEER_TIMEOUT = 120.0
P2P_MAX_PEERS = 32
'''.lstrip("\n")

F["brn/config.py"] = r'''
from __future__ import annotations
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BRN_", env_file=".env", extra="ignore")

    network: str = "mainnet"
    data_dir: Path = Path("./data")
    p2p_host: str = "0.0.0.0"
    p2p_port: Annotated[int, Field(ge=1, le=65535)] = 6001
    explorer_host: str = "127.0.0.1"
    explorer_port: Annotated[int, Field(ge=1, le=65535)] = 8080

    # api_token é opcional aqui — quem precisa (explorer) exige.
    api_token: str = ""

    # network_key é opcional na config global; P2P valida quando conecta.
    network_key: str = ""

    difficulty: Annotated[int, Field(ge=1, le=12)] = 4
    seeds: str = ""
    log_level: str = "INFO"
    rate_limit: str = "120/minute"
    public_tunnel: bool = False
    max_peers: Annotated[int, Field(ge=1, le=256)] = 32

    faucet_address: str = ""
    faucet_amount: float = 100.0
    faucet_cooldown: int = 3600

    @field_validator("network_key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not v:
            return v
        try:
            raw = bytes.fromhex(v)
        except ValueError as e:
            raise ValueError("BRN_NETWORK_KEY deve ser hex") from e
        if len(raw) < 32:
            raise ValueError("BRN_NETWORK_KEY >= 32 bytes")
        return v

    @property
    def seeds_list(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for item in self.seeds.split(","):
            item = item.strip()
            if not item:
                continue
            host, _, port = item.partition(":")
            out.append((host, int(port) if port else 6001))
        return out

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]
    s.ensure_dirs()
    return s
'''.lstrip("\n")

F["brn/logging_setup.py"] = r'''
from __future__ import annotations
import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in getattr(record, "extra_fields", {}).items():
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


def setup(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    request_id.set(rid)
    return rid
'''.lstrip("\n")

F["brn/secure_token.py"] = r'''
"""Token protegido — nunca vaza em log/print."""
from __future__ import annotations
import getpass
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("brn.token")


def _mask(t: str) -> str:
    return "***" if len(t) <= 8 else f"{t[:4]}…{t[-4:]} ({len(t)} chars)"


@dataclass
class SecureToken:
    _value: str = field(repr=False)

    def value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"SecureToken({_mask(self._value)})"
    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    @classmethod
    def load(cls, name: str, *, env_file: Path | None = None,
             prompt: str | None = None) -> "SecureToken":
        v = os.environ.get(name, "").strip()
        if v:
            log.info("token.loaded", extra={"extra_fields": {
                "source": "env", "name": name, "mask": _mask(v)}})
            return cls(v)
        if env_file and env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, val = line.partition("=")
                    if k.strip() == name and val.strip():
                        val = val.strip()
                        os.environ[name] = val
                        log.info("token.loaded", extra={"extra_fields": {
                            "source": "env_file", "name": name,
                            "mask": _mask(val)}})
                        return cls(val)
        if prompt:
            v = getpass.getpass(prompt).strip()
            if v:
                log.info("token.loaded", extra={"extra_fields": {
                    "source": "prompt", "name": name, "mask": _mask(v)}})
                return cls(v)
        raise RuntimeError(f"{name} não encontrado (env, .env ou prompt).")


def load_or_create_api_token(data_dir: Path) -> SecureToken:
    f = data_dir / ".api_token"
    if f.exists():
        v = f.read_text().strip()
        if v:
            return SecureToken(v)
    v = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(v)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    log.info("api_token.created", extra={"extra_fields": {
        "path": str(f), "mask": _mask(v)}})
    return SecureToken(v)
'''.lstrip("\n")

F["brn/crypto.py"] = r'''
from __future__ import annotations
import hashlib
import json
from typing import Any

from coincurve import PrivateKey, PublicKey

from .constants import (ADDRESS_PREFIX, DOMAIN_BLOCK, DOMAIN_CKPT,
                        DOMAIN_TX)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode()


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def double_sha256(b: bytes) -> bytes:
    return sha256(sha256(b))


def generate_private_key() -> bytes:
    return PrivateKey().secret


def pubkey_from_priv(priv: bytes) -> bytes:
    return PrivateKey(priv).public_key.format(compressed=True)


def _xonly(pub33: bytes) -> bytes:
    """Extrai a coordenada x (32 bytes) — formato BIP-340."""
    if len(pub33) == 32:
        return pub33
    if len(pub33) == 33 and pub33[0] in (2, 3):
        return pub33[1:]
    raise ValueError("pubkey inválida")


def pubkey_to_address(pub: bytes) -> str:
    return ADDRESS_PREFIX + sha256(_xonly(pub))[:20].hex()


def address_is_valid(a: str) -> bool:
    if not isinstance(a, str) or not a.startswith(ADDRESS_PREFIX):
        return False
    body = a[len(ADDRESS_PREFIX):]
    if len(body) != 40:
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True


def tx_signing_hash(tx: dict) -> bytes:
    body = {k: v for k, v in tx.items() if k not in ("signature", "txid")}
    return sha256(DOMAIN_TX + canonical_json(body))


def compute_txid(tx: dict) -> str:
    body = {k: v for k, v in tx.items() if k != "txid"}
    return sha256(canonical_json(body)).hexdigest()


def sign_tx(tx: dict, priv: bytes) -> dict:
    sig = PrivateKey(priv).sign_schnorr(tx_signing_hash(tx))
    out = dict(tx)
    out["signature"] = sig.hex()
    out["txid"] = compute_txid(out)
    return out


def verify_tx_signature(tx: dict) -> bool:
    try:
        sig = bytes.fromhex(tx["signature"])
        pub = bytes.fromhex(tx["from_pubkey"])
        if len(sig) != 64:
            return False
        if len(pub) not in (32, 33):
            return False
        if pubkey_to_address(pub) != tx["from_address"]:
            return False
        return PublicKey(_xonly(pub)).verify_schnorr(sig, tx_signing_hash(tx))
    except Exception:
        return False


def merkle_root(txids: list[str]) -> str:
    if not txids:
        return sha256(b"").hex()
    level = [bytes.fromhex(t) for t in txids]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [double_sha256(level[i] + level[i + 1])
                 for i in range(0, len(level), 2)]
    return level[0].hex()


def block_signing_hash(b: dict) -> bytes:
    body = {k: v for k, v in b.items() if k != "hash"}
    return sha256(DOMAIN_BLOCK + canonical_json(body))


def compute_block_hash(b: dict) -> str:
    return block_signing_hash(b).hex()


def hash_meets_difficulty(h: str, d: int) -> bool:
    return d <= 0 or h.startswith("0" * d)


def ckpt_hash(c: dict) -> bytes:
    body = {k: v for k, v in c.items() if k != "signature"}
    return sha256(DOMAIN_CKPT + canonical_json(body))


def sign_checkpoint(c: dict, priv: bytes) -> dict:
    sig = PrivateKey(priv).sign_schnorr(ckpt_hash(c))
    return {**c, "signature": sig.hex()}


def verify_checkpoint(c: dict, pub: bytes) -> bool:
    try:
        return PublicKey(_xonly(pub)).verify_schnorr(
            bytes.fromhex(c["signature"]), ckpt_hash(c))
    except Exception:
        return False
'''.lstrip("\n")

F["brn/hdwallet.py"] = r'''
from __future__ import annotations
import hashlib
import hmac
from dataclasses import dataclass

from coincurve import PrivateKey
from mnemonic import Mnemonic

from .crypto import pubkey_from_priv, pubkey_to_address

N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
HARD = 0x80000000
PURPOSE = 44
COIN = 960


def generate_mnemonic(strength: int = 128) -> str:
    return Mnemonic("english").generate(strength)


def mnemonic_to_seed(m: str, passphrase: str = "") -> bytes:
    if not Mnemonic("english").check(m):
        raise ValueError("Mnemônico inválido")
    return Mnemonic.to_seed(m, passphrase=passphrase)


def _master(seed: bytes) -> tuple[bytes, bytes]:
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return I[:32], I[32:]


def _ckd(k: bytes, c: bytes, i: int) -> tuple[bytes, bytes]:
    data = (b"\x00" + k if i >= HARD
            else PrivateKey(k).public_key.format(compressed=True)
            ) + i.to_bytes(4, "big")
    I = hmac.new(c, data, hashlib.sha512).digest()
    IL, IR = I[:32], I[32:]
    child = (int.from_bytes(IL, "big") + int.from_bytes(k, "big")) % N
    if child == 0:
        raise ValueError("Derivação inválida")
    return child.to_bytes(32, "big"), IR


def _parse(path: str) -> list[int]:
    if not path.startswith("m/"):
        raise ValueError("Path deve começar com 'm/'")
    out = []
    for p in path[2:].split("/"):
        out.append(int(p[:-1]) + HARD if p.endswith(("'", "h", "H"))
                   else int(p))
    return out


def derive_priv(seed: bytes, path: str) -> bytes:
    k, c = _master(seed)
    for i in _parse(path):
        k, c = _ckd(k, c, i)
    return k


@dataclass(frozen=True)
class HDWallet:
    mnemonic: str
    seed: bytes

    @staticmethod
    def create(strength: int = 128, passphrase: str = "") -> "HDWallet":
        m = generate_mnemonic(strength)
        return HDWallet(m, mnemonic_to_seed(m, passphrase))

    @staticmethod
    def from_mnemonic(m: str, passphrase: str = "") -> "HDWallet":
        return HDWallet(m, mnemonic_to_seed(m, passphrase))

    def derive(self, account: int = 0, change: int = 0, index: int = 0):
        path = f"m/{PURPOSE}'/{COIN}'/{account}'/{change}/{index}"
        priv = derive_priv(self.seed, path)
        return priv, pubkey_to_address(pubkey_from_priv(priv)), path

    def accounts(self, count: int = 5, account: int = 0):
        return [self.derive(account=account, index=i) for i in range(count)]
'''.lstrip("\n")

F["brn/wallet.py"] = r'''
from __future__ import annotations
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import (generate_private_key, pubkey_from_priv,
                     pubkey_to_address)

VERSION = 3
AAD = b"brn-wallet-v3"
T = 3
M = 64 * 1024
P = 2


@dataclass(frozen=True)
class Wallet:
    private_key: bytes
    public_key: bytes
    address: str

    @staticmethod
    def create() -> "Wallet":
        priv = generate_private_key()
        pub = pubkey_from_priv(priv)
        return Wallet(priv, pub, pubkey_to_address(pub))


def _derive(pwd: str, salt: bytes) -> bytes:
    return hash_secret_raw(secret=pwd.encode(), salt=salt, time_cost=T,
                           memory_cost=M, parallelism=P, hash_len=32,
                           type=Type.ID)


def save(path: str | Path, w: Wallet, password: str) -> None:
    if len(password) < 12:
        raise ValueError("Senha muito curta")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = _derive(password, salt)
    pt = json.dumps({
        "private_key": w.private_key.hex(),
        "public_key": w.public_key.hex(),
        "address": w.address,
    }).encode()
    ct = AESGCM(key).encrypt(nonce, pt, AAD)
    blob = {
        "version": VERSION, "kdf": "argon2id",
        "kdf_params": {"t": T, "m": M, "p": P},
        "salt": salt.hex(), "nonce": nonce.hex(),
        "ciphertext": ct.hex(),
    }
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def load(path: str | Path, password: str) -> Wallet:
    blob = json.loads(Path(path).read_text())
    if blob.get("version") != VERSION:
        raise ValueError("Versão não suportada")
    key = _derive(password, bytes.fromhex(blob["salt"]))
    try:
        pt = AESGCM(key).decrypt(bytes.fromhex(blob["nonce"]),
                                 bytes.fromhex(blob["ciphertext"]), AAD)
    except Exception as e:
        raise ValueError("Senha incorreta ou arquivo corrompido") from e
    d = json.loads(pt)
    return Wallet(bytes.fromhex(d["private_key"]),
                  bytes.fromhex(d["public_key"]), d["address"])
'''.lstrip("\n")

F["brn/storage.py"] = r'''
from __future__ import annotations
import json as _json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS blocks (
    height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL,
    prev TEXT NOT NULL, merkle_root TEXT NOT NULL,
    timestamp INTEGER NOT NULL, difficulty INTEGER NOT NULL,
    nonce INTEGER NOT NULL, raw TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash);
CREATE TABLE IF NOT EXISTS utxos (
    txid TEXT NOT NULL, vout INTEGER NOT NULL,
    address TEXT NOT NULL, amount INTEGER NOT NULL CHECK (amount > 0),
    height INTEGER NOT NULL, coinbase INTEGER NOT NULL DEFAULT 0,
    spent INTEGER NOT NULL DEFAULT 0 CHECK (spent IN (0,1)),
    spent_by TEXT, PRIMARY KEY (txid, vout));
CREATE INDEX IF NOT EXISTS idx_utxo_unspent ON utxos(address) WHERE spent=0;
CREATE TABLE IF NOT EXISTS mempool (
    txid TEXT PRIMARY KEY, raw TEXT NOT NULL, added_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints (
    height INTEGER PRIMARY KEY, hash TEXT NOT NULL,
    signer TEXT NOT NULL, signature TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, isolation_level=None,
                                     check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            row = self._conn.execute(
                "SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version VALUES(?)", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Schema incompatível: {row['version']} != {SCHEMA_VERSION}")

    def close(self):
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ---------- meta ----------
    def get_meta(self, k: str):
        with self._lock:
            r = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, k: str, v: str):
        with self.tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, v))

    # ---------- blocks ----------
    def chain_tip(self):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM blocks ORDER BY height DESC LIMIT 1"
            ).fetchone()

    def get_block(self, h: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM blocks WHERE height=?", (h,)).fetchone()

    def get_block_by_hash(self, h: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM blocks WHERE hash=?", (h,)).fetchone()

    def recent_blocks(self, limit: int = 20):
        with self._lock:
            return self._conn.execute(
                "SELECT height,hash,prev,timestamp,difficulty FROM blocks "
                "ORDER BY height DESC LIMIT ?", (limit,)).fetchall()

    def apply_block(self, block: dict, txs: list[dict]):
        """Aplica bloco com atualização UTXO atômica."""
        with self.tx() as c:
            c.execute("INSERT INTO blocks(height,hash,prev,merkle_root,"
                      "timestamp,difficulty,nonce,raw) VALUES(?,?,?,?,?,?,?,?)",
                      (block["height"], block["hash"], block["prev"],
                       block["merkle_root"], block["timestamp"],
                       block["difficulty"], block["nonce"],
                       _json.dumps(block)))
            for tx in txs:
                txid = tx["txid"]
                is_cb = bool(tx.get("coinbase"))
                if not is_cb:
                    for inp in tx["inputs"]:
                        cur = c.execute(
                            "UPDATE utxos SET spent=1, spent_by=? "
                            "WHERE txid=? AND vout=? AND spent=0",
                            (txid, inp["txid"], inp["vout"]))
                        if cur.rowcount != 1:
                            raise ValueError(
                                f"UTXO gasto/inexistente: "
                                f"{inp['txid']}:{inp['vout']}")
                for vout, out in enumerate(tx["outputs"]):
                    c.execute(
                        "INSERT INTO utxos(txid,vout,address,amount,height,"
                        "coinbase,spent) VALUES(?,?,?,?,?,?,0)",
                        (txid, vout, out["address"], out["amount"],
                         block["height"], 1 if is_cb else 0))
                c.execute("DELETE FROM mempool WHERE txid=?", (txid,))

    def revert_block(self, block: dict):
        """Desfaz um bloco aplicado (para reorg)."""
        with self.tx() as c:
            for tx in block.get("txs", []):
                txid = tx["txid"]
                c.execute("DELETE FROM utxos WHERE txid=?", (txid,))
                if not tx.get("coinbase"):
                    for inp in tx["inputs"]:
                        c.execute(
                            "UPDATE utxos SET spent=0, spent_by=NULL "
                            "WHERE txid=? AND vout=? AND spent_by=?",
                            (inp["txid"], inp["vout"], txid))
            c.execute("DELETE FROM blocks WHERE hash=?", (block["hash"],))

    # ---------- UTXO ----------
    def utxo_get(self, txid: str, vout: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM utxos WHERE txid=? AND vout=?",
                (txid, vout)).fetchone()

    def utxo_exists_unspent(self, txid: str, vout: int) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM utxos WHERE txid=? AND vout=? AND spent=0",
                (txid, vout)).fetchone() is not None

    def balance_of(self, addr: str) -> int:
        with self._lock:
            r = self._conn.execute(
                "SELECT COALESCE(SUM(amount),0) s FROM utxos "
                "WHERE address=? AND spent=0", (addr,)).fetchone()
            return int(r["s"])

    def utxos_of(self, addr: str):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM utxos WHERE address=? AND spent=0 "
                "ORDER BY amount DESC", (addr,)).fetchall()

    def supply(self) -> int:
        with self._lock:
            r = self._conn.execute(
                "SELECT COALESCE(SUM(amount),0) s FROM utxos "
                "WHERE spent=0").fetchone()
            return int(r["s"])

    # ---------- mempool ----------
    def mempool_add(self, txid: str, raw: str) -> bool:
        with self.tx() as c:
            try:
                c.execute("INSERT INTO mempool(txid,raw,added_at) "
                          "VALUES(?,?,?)", (txid, raw, int(time.time())))
                return True
            except sqlite3.IntegrityError:
                return False

    def mempool_all(self):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM mempool ORDER BY added_at ASC").fetchall()

    def mempool_contains(self, txid: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM mempool WHERE txid=?", (txid,)
            ).fetchone() is not None

    def mempool_size(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) n FROM mempool").fetchone()["n"]

    def mempool_remove(self, txid: str):
        with self.tx() as c:
            c.execute("DELETE FROM mempool WHERE txid=?", (txid,))

    def mempool_expire(self, ttl: int) -> int:
        cutoff = int(time.time()) - ttl
        with self.tx() as c:
            return c.execute("DELETE FROM mempool WHERE added_at < ?",
                             (cutoff,)).rowcount

    # ---------- checkpoints ----------
    def add_checkpoint(self, h: int, hash_: str, signer: str, sig: str):
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)",
                      (h, hash_, signer, sig))

    def get_checkpoint(self, h: int):
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM checkpoints WHERE height=?", (h,)).fetchone()

    # ---------- explorer ----------
    def all_txs(self, limit: int = 50):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM blocks ORDER BY height DESC LIMIT ?",
                (limit,)).fetchall()
        out = []
        for r in rows:
            b = _json.loads(r["raw"])
            for tx in b.get("txs", []):
                out.append({"height": b["height"], **tx})
                if len(out) >= limit:
                    return out
        return out
'''.lstrip("\n")

F["brn/checkpoints.py"] = r'''
from __future__ import annotations
from .crypto import pubkey_to_address, sign_checkpoint


def make_checkpoint(chain, priv: bytes, pub: bytes):
    tip = chain.db.chain_tip()
    if tip is None:
        return None
    c = {"height": tip["height"], "hash": tip["hash"],
         "signer": pubkey_to_address(pub)}
    signed = sign_checkpoint(c, priv)
    chain.db.add_checkpoint(c["height"], c["hash"], c["signer"],
                            signed["signature"])
    return signed


def verify_at_height(chain, h: int, block_hash: str) -> bool:
    c = chain.db.get_checkpoint(h)
    if c is None:
        return True
    return c["hash"] == block_hash
'''.lstrip("\n")

F["brn/blockchain.py"] = r'''
from __future__ import annotations
import json
import time

from .checkpoints import verify_at_height
from .constants import (HALVING, MAX_BLOCK_TXS, MAX_INPUTS, MAX_MEMPOOL,
                        MAX_OUTPUTS, MEMPOOL_TTL, REWARD)
from .crypto import (address_is_valid, compute_block_hash, compute_txid,
                     hash_meets_difficulty, merkle_root,
                     verify_tx_signature)
from .storage import Storage


class Blockchain:
    def __init__(self, storage: Storage, difficulty: int = 4,
                 max_block_txs: int = MAX_BLOCK_TXS):
        self.db = storage
        self.difficulty = difficulty
        self.max_block_txs = max_block_txs

    # ---------- genesis ----------
    def ensure_genesis(self, miner: str):
        if self.db.chain_tip() is not None:
            return
        if not address_is_valid(miner):
            raise ValueError("Endereço genesis inválido")
        cb = {
            "coinbase": True, "inputs": [],
            "outputs": [{"address": miner, "amount": REWARD}],
            "timestamp": 1700000000, "nonce": 0,
        }
        cb["txid"] = compute_txid(cb)
        block = {
            "version": 3, "height": 0, "prev": "0" * 64,
            "merkle_root": merkle_root([cb["txid"]]),
            "timestamp": 1700000000, "difficulty": self.difficulty,
            "nonce": 0, "txs": [cb],
        }
        while True:
            block["hash"] = compute_block_hash(block)
            if hash_meets_difficulty(block["hash"], self.difficulty):
                break
            block["nonce"] += 1
        self.db.apply_block(block, [cb])
        self.db.set_meta("genesis_hash", block["hash"])

    # ---------- validação de tx ----------
    def validate_tx(self, tx: dict, *, allow_coinbase: bool = False):
        if not isinstance(tx, dict):
            return False, "tx precisa ser dict"
        if tx.get("coinbase"):
            return (True, "ok") if allow_coinbase else (False, "coinbase proibida")
        required = {"inputs", "outputs", "timestamp", "nonce",
                    "from_pubkey", "from_address", "signature"}
        if not required.issubset(tx):
            return False, "campos faltando"
        if not verify_tx_signature(tx):
            return False, "assinatura inválida"
        if tx.get("txid") != compute_txid(tx):
            return False, "txid não confere"
        inputs, outputs = tx["inputs"], tx["outputs"]
        if not inputs or not outputs:
            return False, "inputs/outputs vazios"
        if len(inputs) > MAX_INPUTS or len(outputs) > MAX_OUTPUTS:
            return False, "tx grande"

        seen: set[tuple] = set()
        total_in = 0
        for inp in inputs:
            key = (inp.get("txid"), inp.get("vout"))
            if key in seen:
                return False, "input duplicado"
            seen.add(key)
            row = self.db.utxo_get(inp["txid"], inp["vout"])
            if row is None or row["spent"]:
                return False, f"UTXO inexistente: {inp['txid']}:{inp['vout']}"
            total_in += int(row["amount"])

        total_out = 0
        for out in outputs:
            if not address_is_valid(out.get("address", "")):
                return False, "endereço inválido"
            amt = int(out.get("amount", 0))
            if amt <= 0:
                return False, "amount inválido"
            total_out += amt
        if total_out > total_in:
            return False, "saldo insuficiente"
        return True, "ok"

    # ---------- submissão de tx ----------
    def submit_tx(self, tx: dict):
        ok, why = self.validate_tx(tx)
        if not ok:
            return False, why
        if self.db.mempool_contains(tx["txid"]):
            return False, "já na mempool"
        if self.db.mempool_size() >= MAX_MEMPOOL:
            self.db.mempool_expire(MEMPOOL_TTL)
            if self.db.mempool_size() >= MAX_MEMPOOL:
                return False, "mempool cheia"
        self.db.mempool_add(tx["txid"],
                            json.dumps(tx, separators=(",", ":")))
        return True, "aceita"

    # ---------- reward ----------
    def _reward(self, h: int) -> int:
        epoch = h // HALVING
        return 0 if epoch >= 64 else REWARD >> epoch

    # ---------- validação de bloco ----------
    def validate_block(self, block: dict):
        tip = self.db.chain_tip()
        if tip is None:
            return False, "sem genesis"
        if block.get("prev") != tip["hash"]:
            return False, "prev != topo"
        if block.get("height") != tip["height"] + 1:
            return False, "altura inválida"
        if compute_block_hash(block) != block.get("hash"):
            return False, "hash não confere"
        if not hash_meets_difficulty(block["hash"], block["difficulty"]):
            return False, "dificuldade insuficiente"
        if not verify_at_height(self, block["height"], block["hash"]):
            return False, "viola checkpoint"
        txs = block.get("txs") or []
        if not txs or len(txs) > self.max_block_txs:
            return False, "número de txs inválido"
        if not txs[0].get("coinbase"):
            return False, "primeira tx deve ser coinbase"
        for tx in txs[1:]:
            if tx.get("coinbase"):
                return False, "coinbase duplicada"
        if merkle_root([t["txid"] for t in txs]) != block["merkle_root"]:
            return False, "merkle root inválido"

        total_in = total_out = 0
        for tx in txs[1:]:
            ok, why = self.validate_tx(tx)
            if not ok:
                return False, f"tx inválida: {why}"
            for inp in tx["inputs"]:
                row = self.db.utxo_get(inp["txid"], inp["vout"])
                total_in += int(row["amount"])
            for out in tx["outputs"]:
                total_out += int(out["amount"])
        fees = max(0, total_in - total_out)
        max_reward = self._reward(block["height"]) + fees
        if sum(int(o["amount"]) for o in txs[0]["outputs"]) > max_reward:
            return False, "coinbase excede recompensa"
        return True, "ok"

    # ---------- add block ----------
    def add_block(self, block: dict):
        ok, why = self.validate_block(block)
        if ok:
            try:
                self.db.apply_block(block, block["txs"])
                return True, "ok"
            except Exception as e:
                return False, f"falha persistência: {e}"
        from .reorg import try_reorg
        applied, reason = try_reorg(self, block)
        return (True, reason) if applied else (False, why)

    # ---------- build candidate ----------
    def build_candidate(self, miner: str) -> dict:
        tip = self.db.chain_tip()
        if tip is None:
            raise RuntimeError("sem genesis")
        height = tip["height"] + 1
        selected: list[dict] = []
        total_in = total_out = 0
        for row in self.db.mempool_all():
            tx = json.loads(row["raw"])
            ok, _ = self.validate_tx(tx)
            if not ok:
                self.db.mempool_remove(tx["txid"])
                continue
            selected.append(tx)
            for inp in tx["inputs"]:
                r = self.db.utxo_get(inp["txid"], inp["vout"])
                total_in += int(r["amount"])
            for out in tx["outputs"]:
                total_out += int(out["amount"])
            if len(selected) >= self.max_block_txs - 1:
                break
        reward = self._reward(height) + max(0, total_in - total_out)
        cb = {
            "coinbase": True, "inputs": [],
            "outputs": [{"address": miner, "amount": reward}],
            "timestamp": int(time.time()), "nonce": 0,
        }
        cb["txid"] = compute_txid(cb)
        txs = [cb] + selected
        return {
            "version": 3, "height": height, "prev": tip["hash"],
            "merkle_root": merkle_root([t["txid"] for t in txs]),
            "timestamp": int(time.time()),
            "difficulty": self.difficulty, "nonce": 0, "txs": txs,
        }
'''.lstrip("\n")

F["brn/reorg.py"] = r'''
"""Reorganização baseada em chainwork (soma de 2**difficulty)."""
from __future__ import annotations
import json
import logging

from .crypto import compute_block_hash, hash_meets_difficulty

log = logging.getLogger("brn.reorg")


def chainwork_of(block_hashes: list[str], db) -> int:
    """Soma chainwork de uma cadeia (por hashes)."""
    total = 0
    for h in block_hashes:
        row = db.get_block_by_hash(h)
        if row is None:
            return -1
        total += 1 << int(row["difficulty"])
    return total


def try_reorg(chain, new_block: dict):
    """Aceita bloco se formar cadeia com mais chainwork que a atual."""
    parent = chain.db.get_block_by_hash(new_block.get("prev", ""))
    if parent is None:
        return False, "pai desconhecido"
    if not hash_meets_difficulty(compute_block_hash(new_block),
                                 new_block["difficulty"]):
        return False, "dificuldade insuficiente"

    tip = chain.db.chain_tip()
    if tip is None:
        return False, "sem tip"

    # Chainwork da nova cadeia = parent + new_block
    # (aproximação: parent já está na nossa cadeia ou é fork conhecido)
    parent_work = sum(1 << int(chain.db.get_block(h)["difficulty"])
                      for h in range(parent["height"] + 1)
                      if chain.db.get_block(h) is not None)
    new_work = parent_work + (1 << int(new_block["difficulty"]))

    # Chainwork da cadeia atual
    cur_work = sum(1 << int(chain.db.get_block(h)["difficulty"])
                   for h in range(tip["height"] + 1)
                   if chain.db.get_block(h) is not None)

    if new_work <= cur_work:
        return False, "chainwork insuficiente"

    # Encontrar ponto de fork
    fork_h = parent["height"]
    to_revert: list[dict] = []
    h = tip["height"]
    while h > fork_h:
        row = chain.db.get_block(h)
        if row is None:
            break
        to_revert.append(json.loads(row["raw"]))
        h -= 1

    # Reverter e aplicar
    try:
        for b in to_revert:
            chain.db.revert_block(b)
        chain.db.apply_block(new_block, new_block["txs"])
        log.info("reorg.applied", extra={"extra_fields": {
            "fork_height": fork_h, "new_height": new_block["height"],
            "reverted": len(to_revert)}})
        return True, f"reorg aplicado (fork em #{fork_h})"
    except Exception as e:
        # rollback
        for b in reversed(to_revert):
            try:
                chain.db.apply_block(b, b["txs"])
            except Exception:
                pass
        return False, f"reorg falhou: {e}"
'''.lstrip("\n")

F["brn/miner.py"] = r'''
from __future__ import annotations
import logging
import time

from .blockchain import Blockchain
from .crypto import compute_block_hash, hash_meets_difficulty

log = logging.getLogger("brn.miner")


def mine(chain: Blockchain, miner: str, *,
         max_seconds: float | None = None) -> dict | None:
    block = chain.build_candidate(miner)
    t0 = time.time()
    attempts = 0
    while True:
        block["hash"] = compute_block_hash(block)
        if hash_meets_difficulty(block["hash"], block["difficulty"]):
            ok, why = chain.add_block(block)
            log.info("mine.ok", extra={"extra_fields": {
                "height": block["height"], "nonce": block["nonce"],
                "attempts": attempts, "hash": block["hash"][:16]}})
            return block if ok else None
        block["nonce"] += 1
        attempts += 1
        if max_seconds and (time.time() - t0) > max_seconds:
            log.warning("mine.timeout", extra={"extra_fields": {
                "attempts": attempts, "max_seconds": max_seconds}})
            return None
'''.lstrip("\n")

F["brn/metrics.py"] = r'''
from __future__ import annotations
from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Gauge,
                               Histogram, generate_latest)

BLOCKS_MINED = Counter("brn_blocks_mined_total", "Blocos minerados")
TXS_ACCEPTED = Counter("brn_txs_accepted_total", "Txs aceitas")
TXS_REJECTED = Counter("brn_txs_rejected_total", "Txs rejeitadas", ["reason"])
PEERS = Gauge("brn_peers", "Peers conectados")
HEIGHT = Gauge("brn_chain_height", "Altura")
MEMPOOL = Gauge("brn_mempool_size", "Txs na mempool")
SUPPLY = Gauge("brn_supply", "Supply total (satoshis)")
REQ_LATENCY = Histogram("brn_request_seconds", "Latência", ["endpoint"])


def update_gauges(chain, peers_count: int) -> None:
    tip = chain.db.chain_tip()
    HEIGHT.set(tip["height"] if tip else 0)
    MEMPOOL.set(chain.db.mempool_size())
    PEERS.set(peers_count)
    SUPPLY.set(chain.db.supply())


def render():
    return generate_latest(), CONTENT_TYPE_LATEST
'''.lstrip("\n")

F["brn/ngrok_tunnel.py"] = r'''
"""Túnel NGROK opcional. NUNCA contém token em código."""
from __future__ import annotations
import logging

from .secure_token import SecureToken

log = logging.getLogger("brn.ngrok")


class NgrokTunnel:
    def __init__(self, token: SecureToken, target: str,
                 domain: str | None = None):
        self._token = token
        self._target = target
        self._domain = domain
        self._listener = None

    def start(self) -> str:
        try:
            import ngrok
        except ImportError as e:
            raise RuntimeError("Instale: pip install ngrok==1.4.0") from e
        kwargs = {"authtoken": self._token.value(), "schemes": ["https"]}
        if self._domain:
            kwargs["domain"] = self._domain
        self._listener = ngrok.forward(self._target, **kwargs)
        url = self._listener.url()
        log.warning("ngrok.up", extra={"extra_fields": {
            "public_url": url, "local": self._target,
            "note": "X-API-Token obrigatório em /api/*"}})
        return url

    def close(self):
        if self._listener is not None:
            try:
                self._listener.close()
                log.info("ngrok.down")
            except Exception as e:
                log.warning("ngrok.close_failed",
                            extra={"extra_fields": {"err": str(e)}})
'''.lstrip("\n")

F["brn/explorer.py"] = r'''
from __future__ import annotations
import hmac
import time
from functools import wraps

from flask import (Flask, Response, g, jsonify, render_template_string,
                   request)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

from .blockchain import Blockchain
from .config import Settings
from .crypto import address_is_valid
from .logging_setup import new_request_id
from .metrics import (REQ_LATENCY, TXS_ACCEPTED, TXS_REJECTED, render,
                      update_gauges)
from .secure_token import SecureToken

INDEX_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>BRN Explorer</title>
<style>
body{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:0;padding:24px}
h1{color:#58a6ff}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:12px 0}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px;border-bottom:1px solid #30363d}
th{color:#8b949e;font-weight:500}
code{background:#21262d;padding:2px 6px;border-radius:4px;color:#79c0ff}
.small{color:#8b949e;font-size:12px}
</style></head><body>
<h1>BRN Block Explorer</h1>
<div class="card">
  <div>Altura: <code>{{ height }}</code></div>
  <div>Supply: <code>{{ supply }}</code></div>
  <div>Mempool: <code>{{ mempool }}</code></div>
  <div>Peers: <code>{{ peers }}</code></div>
</div>
<div class="card">
  <h3>Últimos blocos</h3>
  <table><tr><th>Altura</th><th>Hash</th><th>Timestamp</th><th>Dificuldade</th></tr>
  {% for b in blocks %}
  <tr><td><a href="/block/{{ b.height }}" style="color:#58a6ff">{{ b.height }}</a></td>
      <td><code>{{ b.hash[:20] }}…</code></td>
      <td class="small">{{ b.timestamp }}</td>
      <td>{{ b.difficulty }}</td></tr>
  {% endfor %}
  </table>
</div>
<p class="small">API: /api/status · /api/blocks · /api/block/N · /api/balance/ADDR — todas exigem <code>X-API-Token</code></p>
</body></html>"""


def create_app(cfg: Settings, chain: Blockchain, api_token: SecureToken,
               peers_getter=lambda: 0) -> Flask:
    if not api_token.value():
        raise RuntimeError("API token vazio — obrigatório para explorer")

    app = Flask(__name__)
    Talisman(app, force_https=cfg.public_tunnel,
             strict_transport_security=cfg.public_tunnel,
             session_cookie_secure=cfg.public_tunnel,
             content_security_policy={
                 "default-src": "'self'",
                 "style-src": "'self' 'unsafe-inline'",
             })
    limiter = Limiter(get_remote_address, app=app,
                      default_limits=[cfg.rate_limit])

    def require_token(f):
        @wraps(f)
        def wrapper(*a, **kw):
            supplied = request.headers.get("X-API-Token", "")
            if not hmac.compare_digest(supplied, api_token.value()):
                return jsonify(error="unauthorized"), 401
            return f(*a, **kw)
        return wrapper

    @app.before_request
    def _before():
        g.rid = new_request_id()
        g.t0 = time.time()

    @app.after_request
    def _after(resp):
        try:
            REQ_LATENCY.labels(endpoint=request.endpoint or "unknown").observe(
                time.time() - g.t0)
        except Exception:
            pass
        return resp

    @app.get("/")
    @limiter.limit("60/minute")
    def index():
        tip = chain.db.chain_tip()
        blocks = chain.db.recent_blocks(20)
        return render_template_string(
            INDEX_HTML,
            height=tip["height"] if tip else 0,
            supply=chain.db.supply(),
            mempool=chain.db.mempool_size(),
            peers=peers_getter(),
            blocks=[dict(b) for b in blocks])

    @app.get("/block/<int:h>")
    @limiter.limit("60/minute")
    def block_view(h: int):
        row = chain.db.get_block(h)
        if row is None:
            return "Bloco não encontrado", 404
        import json as _j
        b = _j.loads(row["raw"])
        rows = "".join(
            f"<tr><td><code>{t['txid'][:20]}…</code></td>"
            f"<td>{(t.get('from_address') or '—')[:16]}…</td>"
            f"<td>{sum(o['amount'] for o in t.get('outputs', []))}</td></tr>"
            for t in b.get("txs", []))
        html = (
            "<html><body style='font-family:system-ui;background:#0d1117;"
            "color:#e6edf3;padding:24px'>"
            f"<h1>Bloco #{h}</h1><p>Hash: <code>{row['hash']}</code></p>"
            "<table style='width:100%'><tr>"
            "<th>TXID</th><th>De</th><th>Valor</th></tr>"
            f"{rows}</table>"
            "<p><a href='/' style='color:#58a6ff'>&larr; Voltar</a></p>"
            "</body></html>"
        )
        return html

    @app.get("/health")
    @limiter.exempt
    def health():
        return jsonify(ok=True, network=cfg.network,
                       public=cfg.public_tunnel)

    @app.get("/ready")
    @limiter.exempt
    @require_token
    def ready():
        tip = chain.db.chain_tip()
        return jsonify(ready=tip is not None,
                       height=tip["height"] if tip else None)

    @app.get("/metrics")
    @limiter.exempt
    @require_token
    def metrics():
        update_gauges(chain, peers_getter())
        body, ctype = render()
        return Response(body, mimetype=ctype)

    @app.get("/api/status")
    @limiter.limit("30/minute")
    @require_token
    def status():
        tip = chain.db.chain_tip()
        return jsonify(height=tip["height"] if tip else 0,
                       hash=tip["hash"] if tip else None,
                       supply=chain.db.supply(),
                       mempool=chain.db.mempool_size(),
                       difficulty=chain.difficulty)

    @app.get("/api/blocks")
    @limiter.limit("30/minute")
    @require_token
    def blocks():
        limit = min(int(request.args.get("limit", 20)), 100)
        return jsonify(blocks=[dict(r) for r in chain.db.recent_blocks(limit)])

    @app.get("/api/block/<int:h>")
    @limiter.limit("60/minute")
    @require_token
    def block_json(h: int):
        row = chain.db.get_block(h)
        if row is None:
            return jsonify(error="not found"), 404
        return jsonify(dict(row))

    @app.get("/api/balance/<address>")
    @limiter.limit("30/minute")
    @require_token
    def balance(address: str):
        if not address_is_valid(address):
            return jsonify(error="invalid address"), 400
        return jsonify(address=address, balance=chain.db.balance_of(address))

    @app.get("/api/txs")
    @limiter.limit("30/minute")
    @require_token
    def txs():
        limit = min(int(request.args.get("limit", 50)), 200)
        return jsonify(txs=chain.db.all_txs(limit))

    @app.post("/api/tx")
    @limiter.limit("20/minute")
    @require_token
    def submit_tx():
        tx = request.get_json(silent=True)
        if not isinstance(tx, dict):
            return jsonify(error="invalid body"), 400
        ok, reason = chain.submit_tx(tx)
        if ok:
            TXS_ACCEPTED.inc()
            return jsonify(status="accepted", txid=tx["txid"])
        TXS_REJECTED.labels(reason=reason.split()[0]).inc()
        return jsonify(error=reason), 400

    return app
'''.lstrip("\n")

# ============================================================ P2P
F["brn/p2p.py"] = r'''
"""P2P autenticado — framing binário (length-prefix) + HMAC handshake.

Protocolo:
  1. Conexão TCP crua
  2. Ambas as partes enviam `hello` com nonce de 16 bytes aleatórios
  3. Ambas calculam HMAC(network_key, nonce_self || nonce_peer || DOMAIN_P2P)
  4. Ambas enviam `auth` com o HMAC
  5. Ambas verificam em tempo constante
  6. Loop: inv/get_block, inv_tx/get_tx, ping/pong
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from typing import Callable

from .constants import (DOMAIN_P2P, P2P_HANDSHAKE_TIMEOUT, P2P_MAX_MSG,
                        P2P_MAX_PEERS, P2P_PEER_TIMEOUT, P2P_PING_INTERVAL,
                        P2P_READ_TIMEOUT, PROTOCOL_VERSION)

log = logging.getLogger("brn.p2p")


def _hmac(network_key_hex: str, data: bytes) -> bytes:
    key = bytes.fromhex(network_key_hex)
    return hmac.new(key, DOMAIN_P2P + data, hashlib.sha256).digest()


class Peer:
    __slots__ = ("reader", "writer", "addr", "last_seen", "height",
                 "protocol", "outbound", "send_lock")

    def __init__(self, reader, writer, addr, outbound: bool):
        self.reader = reader
        self.writer = writer
        self.addr = addr
        self.outbound = outbound
        self.last_seen = time.time()
        self.height = 0
        self.protocol = 0
        self.send_lock = asyncio.Lock()

    @property
    def key(self) -> str:
        return f"{self.addr[0]}:{self.addr[1]}"

    def touch(self):
        self.last_seen = time.time()

    async def send(self, msg: dict):
        body = json.dumps(msg, separators=(",", ":")).encode()
        if len(body) > P2P_MAX_MSG:
            raise ValueError("mensagem grande")
        frame = struct.pack(">I", len(body)) + body
        async with self.send_lock:
            self.writer.write(frame)
            await self.writer.drain()

    async def recv(self) -> dict | None:
        try:
            hdr = await asyncio.wait_for(self.reader.readexactly(4),
                                          timeout=P2P_READ_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return None
        n = struct.unpack(">I", hdr)[0]
        if n == 0 or n > P2P_MAX_MSG:
            return None
        try:
            body = await asyncio.wait_for(self.reader.readexactly(n),
                                           timeout=P2P_READ_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    async def close(self):
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


class P2PServer:
    def __init__(self, chain, cfg, on_block: Callable[[dict], None],
                 on_tx: Callable[[dict], None]):
        self.chain = chain
        self.cfg = cfg
        self.on_block = on_block
        self.on_tx = on_tx
        self.peers: dict[str, Peer] = {}
        self._stop = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        self._tasks: list[asyncio.Task] = []

    # ---------- handshake ----------
    async def _handshake(self, peer: Peer) -> bool:
        if not self.cfg.network_key:
            log.error("p2p.no_key")
            return False
        my_nonce = os.urandom(16)
        await peer.send({"t": "hello", "v": PROTOCOL_VERSION,
                         "n": self.cfg.network, "nonce": my_nonce.hex()})
        try:
            msg = await asyncio.wait_for(peer.recv(), timeout=P2P_HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        if not msg or msg.get("t") != "hello":
            return False
        if msg.get("n") != self.cfg.network:
            log.warning("p2p.wrong_network",
                        extra={"extra_fields": {"got": msg.get("n")}})
            return False
        try:
            peer_nonce = bytes.fromhex(msg["nonce"])
        except (KeyError, ValueError):
            return False
        if len(peer_nonce) != 16:
            return False
        my_sig = _hmac(self.cfg.network_key, my_nonce + peer_nonce)
        await peer.send({"t": "auth", "sig": my_sig.hex()})
        try:
            ack = await asyncio.wait_for(peer.recv(), timeout=P2P_HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        if not ack or ack.get("t") != "auth":
            return False
        try:
            their_sig = bytes.fromhex(ack["sig"])
        except (KeyError, ValueError):
            return False
        expected = _hmac(self.cfg.network_key, peer_nonce + my_nonce)
        if not hmac.compare_digest(their_sig, expected):
            log.warning("p2p.auth_fail", extra={"extra_fields": {
                "addr": peer.key}})
            return False
        peer.height = int(msg.get("h", 0))
        peer.protocol = int(msg.get("v", 0))
        return True

    # ---------- handler ----------
    async def _handle(self, reader, writer):
        addr = writer.get_extra_info("peername") or ("?", 0)
        peer = Peer(reader, writer, addr, outbound=False)
        try:
            if not await self._handshake(peer):
                return
            if len(self.peers) >= P2P_MAX_PEERS:
                return
            self.peers[peer.key] = peer
            log.info("p2p.peer_in", extra={"extra_fields": {
                "addr": peer.key, "height": peer.height}})
            await self._peer_loop(peer)
        except Exception as e:
            log.warning("p2p.handle_error",
                        extra={"extra_fields": {"err": str(e)}})
        finally:
            self.peers.pop(peer.key, None)
            await peer.close()
            log.info("p2p.peer_out", extra={"extra_fields": {"addr": peer.key}})

    async def _peer_loop(self, peer: Peer):
        while not self._stop.is_set():
            msg = await peer.recv()
            if msg is None:
                return
            peer.touch()
            try:
                await self._dispatch(msg, peer)
            except Exception as e:
                log.warning("p2p.dispatch_error", extra={"extra_fields": {
                    "addr": peer.key, "err": str(e)}})

    async def _dispatch(self, msg: dict, peer: Peer):
        t = msg.get("t")
        if t == "ping":
            await peer.send({"t": "pong", "h": self.chain.db.chain_tip()["height"]
                             if self.chain.db.chain_tip() else 0})
        elif t == "pong":
            peer.height = int(msg.get("h", peer.height))
        elif t == "inv_block":
            bh = msg.get("h")
            if bh and not self.chain.db.get_block(bh):
                await peer.send({"t": "get_block", "h": bh})
        elif t == "get_block":
            h = int(msg.get("h", -1))
            row = self.chain.db.get_block(h)
            if row:
                import json as _j
                await peer.send({"t": "block", "block": _j.loads(row["raw"])})
        elif t == "block":
            block = msg.get("block")
            if isinstance(block, dict):
                try:
                    self.on_block(block)
                except Exception as e:
                    log.warning("p2p.on_block_error",
                                extra={"extra_fields": {"err": str(e)}})
        elif t == "inv_tx":
            txid = msg.get("txid")
            if txid and not self.chain.db.mempool_contains(txid):
                await peer.send({"t": "get_tx", "txid": txid})
        elif t == "get_tx":
            txid = msg.get("txid")
            if txid:
                for row in self.chain.db.mempool_all():
                    if row["txid"] == txid:
                        import json as _j
                        await peer.send({"t": "tx", "tx": _j.loads(row["raw"])})
                        break
        elif t == "tx":
            tx = msg.get("tx")
            if isinstance(tx, dict):
                try:
                    self.on_tx(tx)
                except Exception as e:
                    log.warning("p2p.on_tx_error",
                                extra={"extra_fields": {"err": str(e)}})

    # ---------- broadcast ----------
    async def broadcast(self, msg: dict, exclude: str | None = None):
        if not self.peers:
            return
        dead: list[str] = []
        for key, peer in list(self.peers.items()):
            if exclude and key == exclude:
                continue
            try:
                await peer.send(msg)
            except Exception:
                dead.append(key)
        for k in dead:
            p = self.peers.pop(k, None)
            if p:
                await p.close()

    # ---------- outbound connect ----------
    async def connect(self, host: str, port: int):
        key = f"{host}:{port}"
        if key in self.peers:
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=P2P_HANDSHAKE_TIMEOUT)
        except Exception as e:
            log.debug("p2p.connect_fail", extra={"extra_fields": {
                "host": host, "port": port, "err": str(e)}})
            return False
        peer = Peer(reader, writer, (host, port), outbound=True)
        if not await self._handshake(peer):
            await peer.close()
            return False
        self.peers[key] = peer
        log.info("p2p.peer_out", extra={"extra_fields": {
            "addr": peer.key, "height": peer.height}})
        self._tasks.append(asyncio.create_task(self._peer_loop(peer)))
        return True

    # ---------- maintenance ----------
    async def _maintenance(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=P2P_PING_INTERVAL)
                break
            except asyncio.TimeoutError:
                pass
            now = time.time()
            for key, peer in list(self.peers.items()):
                if now - peer.last_seen > P2P_PEER_TIMEOUT:
                    log.info("p2p.timeout", extra={"extra_fields": {"addr": key}})
                    self.peers.pop(key, None)
                    await peer.close()
                    continue
                try:
                    tip = self.chain.db.chain_tip()
                    await peer.send({"t": "ping",
                                     "h": tip["height"] if tip else 0})
                except Exception:
                    self.peers.pop(key, None)
                    await peer.close()

    # ---------- lifecycle ----------
    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, self.cfg.p2p_host, self.cfg.p2p_port)
        log.info("p2p.listen", extra={"extra_fields": {
            "host": self.cfg.p2p_host, "port": self.cfg.p2p_port}})
        self._tasks.append(asyncio.create_task(self._maintenance()))
        for host, port in self.cfg.seeds_list:
            asyncio.create_task(self.connect(host, port))
        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        self._stop.set()
        for peer in list(self.peers.values()):
            await peer.close()
        self.peers.clear()
        for t in self._tasks:
            t.cancel()
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
'''.lstrip("\n")

# ============================================================ CLI
F["brn/cli.py"] = r'''
from __future__ import annotations
import argparse
import asyncio
import getpass
import signal
import sys
import threading
import time
from pathlib import Path

from .blockchain import Blockchain
from .checkpoints import make_checkpoint
from .config import load_settings
from .crypto import pubkey_from_priv
from .explorer import create_app
from .hdwallet import HDWallet
from .logging_setup import setup as setup_logging
from .miner import mine
from .ngrok_tunnel import NgrokTunnel
from .p2p import P2PServer
from .secure_token import SecureToken, load_or_create_api_token
from .storage import Storage
from .wallet import Wallet, load, save


def _chain(cfg):
    db = Storage(cfg.data_dir / f"{cfg.network}.db")
    return db, Blockchain(db, difficulty=cfg.difficulty)


# ---------- wallet ----------
def cmd_wallet_new(args):
    p = Path(args.path)
    if p.exists():
        sys.exit(f"Já existe: {p}")
    pwd = getpass.getpass("Senha: ")
    if pwd != getpass.getpass("Confirme: "):
        sys.exit("Senhas não conferem")
    w = Wallet.create()
    save(p, w, pwd)
    print(f"Carteira: {p}\nEndereço: {w.address}")


def cmd_wallet_show(args):
    w = load(args.path, getpass.getpass("Senha: "))
    print(f"Endereço: {w.address}\nPubkey: {w.public_key.hex()}")


# ---------- HD wallet ----------
def cmd_hd_new(args):
    hd = HDWallet.create()
    print("Mnemônico (GUARDE):", hd.mnemonic)
    print()
    for _, addr, path in hd.accounts(5):
        print(f"  {path}\t{addr}")


def cmd_hd_derive(args):
    m = getpass.getpass("Mnemônico: ").strip()
    hd = HDWallet.from_mnemonic(m)
    priv, addr, path = hd.derive(account=args.account, change=args.change,
                                 index=args.index)
    print(f"{path}\n{addr}")
    if args.reveal:
        print(f"PRIV: {priv.hex()}")


# ---------- chain ----------
def cmd_init(args):
    cfg = load_settings()
    db, chain = _chain(cfg)
    try:
        chain.ensure_genesis(args.address)
        tip = db.chain_tip()
        print(f"Genesis: altura={tip['height']} hash={tip['hash']}")
    finally:
        db.close()


def cmd_mine(args):
    cfg = load_settings()
    db, chain = _chain(cfg)
    try:
        b = mine(chain, args.address, max_seconds=args.max_seconds)
        if b:
            print(f"Bloco: {b['height']} {b['hash']}")
        else:
            print("Não aceito ou timeout")
    finally:
        db.close()


def cmd_checkpoint(args):
    cfg = load_settings()
    db, chain = _chain(cfg)
    try:
        priv = bytes.fromhex(getpass.getpass("Priv do assinante: ").strip())
        ck = make_checkpoint(chain, priv, pubkey_from_priv(priv))
        if ck:
            print(f"Checkpoint: altura={ck['height']} "
                  f"hash={ck['hash'][:16]}…")
        else:
            print("Sem blocos")
    finally:
        db.close()


def cmd_balance(args):
    cfg = load_settings()
    db, chain = _chain(cfg)
    try:
        print(chain.db.balance_of(args.address))
    finally:
        db.close()


# ---------- node ----------
class _NodeRunner:
    def __init__(self, cfg, chain, on_block):
        self.cfg = cfg
        self.chain = chain
        self.on_block = on_block
        self.loop: asyncio.AbstractEventLoop | None = None
        self.p2p: P2PServer | None = None
        self._thread: threading.Thread | None = None
        self._stop: asyncio.Event | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="p2p")
        self._thread.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:
            print(f"[p2p] erro: {e}")
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _main(self):
        self._stop = asyncio.Event()
        self.p2p = P2PServer(self.chain, self.cfg,
                             on_block=self.on_block,
                             on_tx=self._on_tx)
        try:
            await self.p2p.start()
        except asyncio.CancelledError:
            pass

    def _on_tx(self, tx: dict):
        try:
            self.chain.submit_tx(tx)
        except Exception:
            pass

    def stop(self):
        if self.p2p and self.loop:
            try:
                asyncio.run_coroutine_threadsafe(self.p2p.stop(), self.loop)
            except Exception:
                pass


def cmd_node(args):
    cfg = load_settings()
    setup_logging(cfg.log_level)
    db, chain = _chain(cfg)

    # genesis
    if args.genesis_address:
        chain.ensure_genesis(args.genesis_address)
    elif db.chain_tip() is None:
        sys.exit("Nó sem genesis. Use --genesis-address <endereço>.")

    # p2p em background
    def _on_block(blk: dict):
        try:
            chain.add_block(blk)
        except Exception as e:
            print(f"[p2p] add_block falhou: {e}")

    runner = _NodeRunner(cfg, chain, _on_block)
    runner.start()

    # explorer opcional
    srv = None
    tunnel = None
    if args.with_explorer:
        api_token = load_or_create_api_token(cfg.data_dir)
        print(f"API token: {api_token.value()}")
        app = create_app(cfg, chain, api_token,
                         peers_getter=lambda: len(
                             runner.p2p.peers) if runner.p2p else 0)
        from werkzeug.serving import make_server
        srv = make_server(cfg.explorer_host, cfg.explorer_port, app,
                          threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name="explorer").start()
        local_url = f"http://{cfg.explorer_host}:{cfg.explorer_port}"
        print(f"Explorador local: {local_url}")

        if args.ngrok:
            try:
                ngrok_token = SecureToken.load(
                    "NGROK_AUTHTOKEN", env_file=Path(".env"),
                    prompt="NGROK_AUTHTOKEN (não exibido): ")
                tunnel = NgrokTunnel(ngrok_token, local_url,
                                     domain=args.domain or None)
                url = tunnel.start()
                print(f"\n⚠️  Explorador público: {url}")
                print("   /api/* exige X-API-Token. Rotacione o token se "
                      "já foi exposto.\n")
            except Exception as e:
                print(f"Falha NGROK: {e}")
    else:
        print("[node] explorer desativado (--with-explorer para ativar)")

    # mineração opcional
    stop = threading.Event()

    def _sig(signum, _frame):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _sig)
        except (ValueError, OSError):
            pass

    if args.mine_to:
        def _miner():
            while not stop.is_set():
                try:
                    mine(chain, args.mine_to, max_seconds=60)
                except Exception as e:
                    print(f"[miner] erro: {e}")
                    time.sleep(2)
        threading.Thread(target=_miner, daemon=True, name="miner").start()
        print(f"[node] minerando para {args.mine_to}")

    print("Ctrl+C para encerrar.")
    try:
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        runner.stop()
        if tunnel:
            tunnel.close()
        if srv:
            srv.shutdown()
        db.close()


# ---------- main ----------
def main():
    p = argparse.ArgumentParser(prog="brn")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("wallet-new")
    s.add_argument("path")
    s.set_defaults(func=cmd_wallet_new)

    s = sub.add_parser("wallet-show")
    s.add_argument("path")
    s.set_defaults(func=cmd_wallet_show)

    s = sub.add_parser("hd-new")
    s.set_defaults(func=cmd_hd_new)

    s = sub.add_parser("hd-derive")
    s.add_argument("--account", type=int, default=0)
    s.add_argument("--change", type=int, default=0)
    s.add_argument("--index", type=int, default=0)
    s.add_argument("--reveal", action="store_true")
    s.set_defaults(func=cmd_hd_derive)

    s = sub.add_parser("init")
    s.add_argument("address")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("mine")
    s.add_argument("address")
    s.add_argument("--max-seconds", type=float, default=None)
    s.set_defaults(func=cmd_mine)

    s = sub.add_parser("balance")
    s.add_argument("address")
    s.set_defaults(func=cmd_balance)

    s = sub.add_parser("checkpoint")
    s.set_defaults(func=cmd_checkpoint)

    s = sub.add_parser("node")
    s.add_argument("--genesis-address", default=None)
    s.add_argument("--with-explorer", action="store_true")
    s.add_argument("--ngrok", action="store_true")
    s.add_argument("--domain", default=None)
    s.add_argument("--mine-to", default=None,
                   help="Endereço que recebe recompensa de mineração")
    s.set_defaults(func=cmd_node)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
'''.lstrip("\n")

# ============================================================ testes
F["tests/__init__.py"] = ""
F["tests/conftest.py"] = r'''
import os
os.environ.setdefault("BRN_API_TOKEN", "x" * 32)
os.environ.setdefault("BRN_NETWORK_KEY", "a" * 64)
os.environ.setdefault("BRN_DATA_DIR", "/tmp/brn-test")
'''.lstrip("\n")

F["tests/test_crypto.py"] = r'''
from brn.crypto import (address_is_valid, compute_txid, generate_private_key,
                        hash_meets_difficulty, pubkey_from_priv,
                        pubkey_to_address, sign_tx, verify_tx_signature)


def test_sign_verify():
    priv = generate_private_key()
    pub = pubkey_from_priv(priv)
    addr = pubkey_to_address(pub)
    tx = {"inputs": [{"txid": "a" * 64, "vout": 0}],
          "outputs": [{"address": addr, "amount": 1000}],
          "timestamp": 1, "nonce": 1,
          "from_pubkey": pub.hex(), "from_address": addr}
    s = sign_tx(tx, priv)
    assert verify_tx_signature(s)
    s["outputs"][0]["amount"] = 2
    assert not verify_tx_signature(s)


def test_address():
    priv = generate_private_key()
    assert address_is_valid(pubkey_to_address(pubkey_from_priv(priv)))
    assert not address_is_valid("brn1zz")
    assert not address_is_valid("x" * 44)


def test_difficulty():
    assert hash_meets_difficulty("0" * 4 + "a" * 60, 4)
    assert not hash_meets_difficulty("f" * 64, 4)
    assert hash_meets_difficulty("any", 0)


def test_txid_varies():
    tx = {"inputs": [], "outputs": [], "timestamp": 1, "nonce": 1,
          "from_pubkey": "00" * 33, "from_address": "brn1" + "a" * 40}
    h1 = compute_txid(tx)
    tx["timestamp"] = 2
    assert h1 != compute_txid(tx)
'''.lstrip("\n")

F["tests/test_hdwallet.py"] = r'''
import pytest
from brn.hdwallet import HDWallet, _parse, mnemonic_to_seed


def test_bip39_vector():
    m = ("abandon abandon abandon abandon abandon abandon "
         "abandon abandon abandon abandon abandon about")
    seed = mnemonic_to_seed(m, "TREZOR")
    assert seed.hex().startswith("c55257c360c07c72029aebc1b53c05ed03")


def test_deterministic():
    w = HDWallet.create()
    assert w.derive(index=0) == w.derive(index=0)


def test_multiple_addresses():
    w = HDWallet.create()
    assert len({w.derive(index=i)[1] for i in range(5)}) == 5


def test_restore():
    a = HDWallet.create()
    b = HDWallet.from_mnemonic(a.mnemonic)
    assert a.derive(index=3) == b.derive(index=3)


def test_path_parse():
    assert _parse("m/44'/960'/0'/0/5") == [
        44 | 0x80000000, 960 | 0x80000000, 0 | 0x80000000, 0, 5]


def test_invalid_mnemonic():
    with pytest.raises(ValueError):
        mnemonic_to_seed("foo bar baz")
'''.lstrip("\n")

F["tests/test_wallet.py"] = r'''
import os
import tempfile
from pathlib import Path
import pytest
from brn.wallet import Wallet, load, save


def test_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "w.brn"
        w = Wallet.create()
        save(p, w, "senha-super-forte-123")
        assert os.stat(p).st_mode & 0o777 == 0o600
        assert load(p, "senha-super-forte-123").private_key == w.private_key


def test_wrong_password():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "w.brn"
        save(p, Wallet.create(), "senha-correta-123")
        with pytest.raises(ValueError):
            load(p, "senha-errada-999")


def test_short_password():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ValueError):
            save(Path(d) / "w.brn", Wallet.create(), "curta")
'''.lstrip("\n")

F["tests/test_blockchain.py"] = r'''
import tempfile
from pathlib import Path
from brn.blockchain import Blockchain
from brn.crypto import (generate_private_key, pubkey_from_priv,
                        pubkey_to_address)
from brn.miner import mine
from brn.storage import Storage


def _chain():
    d = tempfile.mkdtemp()
    db = Storage(Path(d) / "t.db")
    c = Blockchain(db, difficulty=2, max_block_txs=20)
    priv = generate_private_key()
    addr = pubkey_to_address(pubkey_from_priv(priv))
    c.ensure_genesis(addr)
    return c, addr


def test_genesis():
    c, _ = _chain()
    assert c.db.chain_tip()["height"] == 0


def test_mine():
    c, addr = _chain()
    assert mine(c, addr, max_seconds=5) is not None
    assert c.db.chain_tip()["height"] == 1


def test_utxo_atomicity():
    c, addr = _chain()
    mine(c, addr, max_seconds=5)
    u = c.db.utxos_of(addr)[0]
    with c.db.tx() as conn:
        cur = conn.execute(
            "UPDATE utxos SET spent=1 WHERE txid=? AND vout=? AND spent=0",
            (u["txid"], u["vout"]))
        assert cur.rowcount == 1
        cur = conn.execute(
            "UPDATE utxos SET spent=1 WHERE txid=? AND vout=? AND spent=0",
            (u["txid"], u["vout"]))
        assert cur.rowcount == 0


def test_balance_and_supply():
    c, addr = _chain()
    mine(c, addr, max_seconds=5)
    mine(c, addr, max_seconds=5)
    assert c.db.balance_of(addr) > 0
    assert c.db.supply() > 0
'''.lstrip("\n")

F["tests/test_reorg.py"] = r'''
import tempfile
from pathlib import Path
from brn.blockchain import Blockchain
from brn.crypto import (generate_private_key, pubkey_from_priv,
                        pubkey_to_address)
from brn.miner import mine
from brn.storage import Storage


def _chain():
    d = tempfile.mkdtemp()
    db = Storage(Path(d) / "t.db")
    c = Blockchain(db, difficulty=2, max_block_txs=20)
    priv = generate_private_key()
    addr = pubkey_to_address(pubkey_from_priv(priv))
    c.ensure_genesis(addr)
    return c, addr


def test_mine_extends():
    c, addr = _chain()
    b1 = mine(c, addr, max_seconds=5)
    b2 = mine(c, addr, max_seconds=5)
    assert b1 and b2
    assert b2["prev"] == b1["hash"]
'''.lstrip("\n")

# ============================================================ infra
F["Dockerfile"] = r'''
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libffi-dev libssl-dev curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY brn ./brn
RUN useradd --create-home --shell /usr/sbin/nologin brn \
 && mkdir -p /var/lib/brn && chown -R brn:brn /var/lib/brn /app
USER brn
ENV BRN_DATA_DIR=/var/lib/brn
EXPOSE 6001 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fs http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["python", "-m", "brn"]
CMD ["node", "--with-explorer"]
'''.lstrip("\n")

F["docker-compose.yml"] = r'''
services:
  node:
    build: .
    command: ["node", "--with-explorer"]
    env_file: [.env]
    environment:
      BRN_DATA_DIR: /var/lib/brn
      BRN_EXPLORER_HOST: 0.0.0.0
    volumes:
      - brn_data:/var/lib/brn
    expose: ["6001", "8080"]
    restart: unless-stopped
    read_only: true
    tmpfs: [/tmp]
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]

  proxy:
    image: caddy:2
    depends_on: [node]
    ports: ["8443:8443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    restart: unless-stopped

volumes:
  brn_data:
  caddy_data:
'''.lstrip("\n")

F["Caddyfile"] = r'''
:8443 {
    reverse_proxy node:8080
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
    }
}
'''.lstrip("\n")

F[".github/workflows/security.yml"] = r'''
name: security
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements-dev.txt
      - run: ruff check brn tests
      - run: mypy brn || true
      - run: pytest -q --cov=brn
      - run: bandit -r brn -ll
      - run: pip-audit -r requirements.txt
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - uses: aquasecurity/trivy-action@0.24.0
        with:
          scan-type: fs
          severity: HIGH,CRITICAL
          exit-code: "1"
'''.lstrip("\n")

F["README.md"] = r'''
# BRN Produção v3.1

Blockchain UTXO + explorador com NGROK seguro, P2P autenticado e CI.

## Componentes

- **UTXO** com gasto atômico no SQLite
- **PoW** com dificuldade ajustável
- **P2P** autenticado via HMAC-SHA256 (`network_key`)
- **HD wallet** BIP-39/32/44 (coin 960)
- **Carteira** Argon2id + AES-256-GCM (chmod 0600)
- **Explorador** Flask + rate-limit + CSP + HSTS + `X-API-Token`
- **NGROK** opcional (token nunca em código)
- **Checkpoints** assinados (Schnorr BIP-340)
- **Reorg** por chainwork
- **Métricas** Prometheus em `/metrics`
- **Docker** non-root, read-only, cap_drop ALL

## Setup rápido

```bash
cp .env.example .env
make key          # cole BRN_NETWORK_KEY no .env
make api-token    # cole BRN_API_TOKEN no .env
# opcional:
echo "NGROK_AUTHTOKEN=seu-token-novo" >> .env
chmod 600 .env

python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
make ci