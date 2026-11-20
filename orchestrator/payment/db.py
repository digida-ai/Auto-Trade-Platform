"""Payment database: one SQLite file per system, written only through the
payment managers. Schema mirrors the musashaban payment tables.

Locations:
  * ``orchestrator/state/payment.db`` — the platform payment DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

PAYMENT_DB = (
    Path(__file__).resolve().parent.parent.parent / "orchestrator" / "state" / "payment.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rudipay_routers (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  country_code TEXT NOT NULL DEFAULT '',
  region TEXT NOT NULL DEFAULT '',
  city TEXT NOT NULL DEFAULT '',
  location_id TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  router_type TEXT NOT NULL DEFAULT 'admin',
  status TEXT NOT NULL DEFAULT 'active',
  wallet_balance REAL NOT NULL DEFAULT 0,
  total_routed REAL NOT NULL DEFAULT 0,
  total_fees_earned REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT,
  router_reserved_at TEXT,
  router_reserved_by TEXT
);
CREATE TABLE IF NOT EXISTS rudipay_faucets (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  phone TEXT NOT NULL,
  country_code TEXT NOT NULL DEFAULT '',
  display_name TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  wallet_balance REAL NOT NULL DEFAULT 0,
  total_routed REAL NOT NULL DEFAULT 0,
  total_fees_earned REAL NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS rudipay_router_selections (
  id TEXT PRIMARY KEY,
  buyer_id TEXT NOT NULL,
  router_id TEXT NOT NULL,
  selection_method TEXT NOT NULL DEFAULT '',
  buyer_location TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rudipay_transactions (
  id TEXT PRIMARY KEY,
  buyer_id TEXT NOT NULL,
  seller_id TEXT NOT NULL,
  router_id TEXT NOT NULL,
  amount REAL NOT NULL,
  currency TEXT NOT NULL DEFAULT '',
  router_fee REAL NOT NULL DEFAULT 0,
  seller_amount REAL NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  buyer_committed_at TEXT,
  seller_notified_at TEXT,
  completed_at TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  routing_phone TEXT,
  routing_display_name TEXT,
  confirmation_code TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rudipay_settlements (
  id TEXT PRIMARY KEY,
  transaction_id TEXT NOT NULL,
  router_id TEXT NOT NULL,
  buyer_id TEXT NOT NULL,
  seller_id TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT '',
  router_fee REAL NOT NULL DEFAULT 0,
  seller_amount REAL NOT NULL DEFAULT 0,
  confirmation_code TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'completed',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rudipay_disputes (
  id TEXT PRIMARY KEY,
  transaction_id TEXT NOT NULL,
  raised_by TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT,
  resolved_by TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS funding_request (
  id TEXT PRIMARY KEY,
  purpose TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  amount_minor REAL NOT NULL,
  collected_minor REAL NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  reference TEXT NOT NULL DEFAULT '',
  requested_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS funding_collections (
  id TEXT PRIMARY KEY,
  funding_id TEXT NOT NULL,
  amount_minor REAL NOT NULL DEFAULT 0,
  rail TEXT NOT NULL DEFAULT '',
  reference TEXT NOT NULL DEFAULT '',
  recorded_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS development_needs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT 'normal',
  status TEXT NOT NULL DEFAULT 'open',
  estimated_minor REAL,
  currency TEXT,
  funding_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sms_otp_challenge (
  id TEXT PRIMARY KEY,
  purpose TEXT NOT NULL DEFAULT '',
  subject_id TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  sender TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  otp_hash TEXT,
  otp_cipher TEXT,
  otp_iv TEXT,
  otp_tag TEXT,
  device_id TEXT,
  expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  received_at TEXT,
  claimed_at TEXT,
  used_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

# Shared tables reused from the orchestrator's payment DB namespace.
PAYMENT_TABLES_IN_STATE = ("payment_tx",)

_tables_ensured = False


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def cursor(db: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    if db is None:
        db = connect_db(PAYMENT_DB)
    return db


def _cols(table: str, conn) -> List[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _add_missing(table: str, cols: Dict[str, str], conn) -> None:
    have = set(_cols(table, conn))
    for name, typ in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def record_payment(conn: sqlite3.Connection, *, rail: str, kind: str, amount_minor: float,
                   currency: str, counterparty: str, account: str,
                   reference: str, status: str = "completed") -> int:
    """Append a payment tx to the platform payment ledger (payment_tx)."""
    from orchestrator.store import record_payment as _rp
    return _rp(conn, rail=rail, kind=kind, amount_minor=amount_minor, currency=currency,
               counterparty=counterparty, account=account, reference=reference, status=status)


def update_payment_status(conn: sqlite3.Connection, row_id, status: str, note: str = "") -> None:
    from orchestrator.store import update_payment_status as _ups
    _ups(conn, row_id, status, note=note)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "pay_") -> str:
    import uuid
    return prefix + uuid.uuid4().hex[:10]


def ensure_payment_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    conn = conn or connect_db(PAYMENT_DB)
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payment_tx'").fetchone():
        return
    _add_missing("payment_tx", {
        "id": "TEXT PRIMARY KEY",
        "rail": "TEXT NOT NULL DEFAULT ''",
        "kind": "TEXT NOT NULL DEFAULT ''",
        "amount_minor": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT NOT NULL DEFAULT ''",
        "counterparty": "TEXT NOT NULL DEFAULT ''",
        "account": "TEXT NOT NULL DEFAULT ''",
        "reference": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'completed'",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT ''",
        "occurred_at": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    }, conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payment_tx (
            id TEXT PRIMARY KEY,
            rail TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT '',
            amount_minor REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT '',
            counterparty TEXT NOT NULL DEFAULT '',
            account TEXT NOT NULL DEFAULT '',
            reference TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        )""")
    conn.commit()
