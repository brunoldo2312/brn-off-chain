"""
Camada de persistência SQLite da blockchain Bruno.
"""
import sqlite3
import json
import threading


class BlockchainDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_schema()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    id_index      INTEGER PRIMARY KEY,
                    timestamp     REAL,
                    previous_hash TEXT,
                    transactions  TEXT,
                    difficulty    INTEGER,
                    nonce         INTEGER,
                    hash          TEXT
                )
            """)
            conn.commit()

    def insert_block(self, block):
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO blocks VALUES (?,?,?,?,?,?,?)",
                (
                    block.index,
                    block.timestamp,
                    block.previous_hash,
                    json.dumps(block.transactions),
                    block.difficulty,
                    block.nonce,
                    block.hash,
                ),
            )
            conn.commit()

    def get_raw_chain(self):
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM blocks ORDER BY id_index ASC"
            ).fetchall()

        return [
            {
                "index":         r["id_index"],
                "timestamp":     r["timestamp"],
                "previous_hash": r["previous_hash"],
                "transactions":  json.loads(r["transactions"]),
                "difficulty":    r["difficulty"],
                "nonce":         r["nonce"],
                "hash":          r["hash"],
            }
            for r in rows
        ]

    def replace_chain(self, raw_chain):
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM blocks")
            for b in raw_chain:
                conn.execute(
                    "INSERT INTO blocks VALUES (?,?,?,?,?,?,?)",
                    (
                        b["index"], b["timestamp"], b["previous_hash"],
                        json.dumps(b["transactions"]), b["difficulty"],
                        b["nonce"], b["hash"],
                    ),
                )
            conn.commit()

    @property
    def height(self):
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(id_index) AS h FROM blocks"
            ).fetchone()
        return (row["h"] or 0) + 1 if row["h"] is not None else 0

    @property
    def tip_hash(self):
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT hash FROM blocks ORDER BY id_index DESC LIMIT 1"
            ).fetchone()
        return row["hash"] if row else "0" * 64