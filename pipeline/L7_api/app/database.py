"""
SQLite database setup.

Single file  data/store.db  relative to the project root.
Override with  DATABASE_URL  env var (path to .db file).

Usage:
    with get_db() as db:
        rows = db.execute("SELECT ...").fetchall()
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB = str(Path(__file__).parent.parent / "data" / "store.db")
DB_PATH = os.getenv("DATABASE_URL", _DEFAULT_DB)

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    store_id    TEXT NOT NULL,
    camera_id   TEXT,
    visitor_id  TEXT,
    event_type  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    zone_id     TEXT,
    dwell_ms    INTEGER DEFAULT 0,
    is_staff    INTEGER DEFAULT 0,
    confidence  REAL    DEFAULT 0.0,
    converted   INTEGER DEFAULT 0,
    metadata    TEXT,
    ingested_at TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_store_ts    ON events(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_visitor     ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_type_store  ON events(event_type, store_id);
CREATE INDEX IF NOT EXISTS idx_store_date  ON events(store_id, date(timestamp));

CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id  TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    basket_value_inr REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_pos_store_ts ON pos_transactions(store_id, timestamp);
"""


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_DDL)


@contextmanager
def get_db():
    """Yield a connected sqlite3.Connection with auto-commit/rollback."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping() -> float:
    """Return DB round-trip latency in ms, or raise if unavailable."""
    import time
    t0 = time.perf_counter()
    with get_db() as db:
        db.execute("SELECT 1")
    return round((time.perf_counter() - t0) * 1000, 2)
