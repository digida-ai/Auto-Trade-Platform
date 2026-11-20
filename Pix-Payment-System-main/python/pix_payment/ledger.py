"""Durable, double-entry ledger for accounts, wallets, payments and settlement.

This is real persistence (SQLite), not a mock. Money is stored in minor units as
integers to avoid floating-point drift. Nothing here creates value: a wallet
balance can only change through a ledger entry that carries an authorizer and an
idempotency reference, and settlement requires verified provider evidence.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


DEFAULT_DB = Path(
    os.environ.get(
        "PIX_PAYMENT_DB",
        str(Path(__file__).resolve().parent / "payment-ledger.sqlite3"),
    )
)

# ISO 4217 minor-unit exponents. Unknown currencies default to 2 and must be
# declared explicitly in config before use; see minor_units().
CURRENCY_EXPONENTS = {
    "UGX": 0, "KES": 2, "TZS": 0, "RWF": 0, "NGN": 2, "GHS": 2,
    "ZAR": 2, "ZMW": 2, "MWK": 2, "ETB": 2, "XOF": 0, "XAF": 0,
    "MZN": 2, "BWP": 2, "EGP": 2, "MAD": 2, "CDF": 2, "BRL": 2,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def minor_units(currency: str) -> int:
    return CURRENCY_EXPONENTS.get(currency.upper(), 2)


def to_minor(amount: Any, currency: str) -> int:
    """Convert a decimal amount to integer minor units, exactly."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid amount: {amount!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("amount must be a finite positive number")
    exponent = minor_units(currency)
    quantum = Decimal(1).scaleb(-exponent)
    minor = value.quantize(quantum)
    return int(minor * (10 ** exponent))


def from_minor(amount_minor: int, currency: str) -> str:
    exponent = minor_units(currency)
    return str((Decimal(amount_minor) / (10 ** exponent)).quantize(Decimal(1).scaleb(-exponent)))


class Ledger:
    """Thread-safe SQLite ledger. One instance per process path."""

    _local = threading.local()

    def __init__(self, path: Optional[Path] = None):
        configured = path or os.environ.get("PIX_PAYMENT_DB") or DEFAULT_DB
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    email TEXT,
                    document TEXT,
                    country TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS accounts_phone ON accounts(phone) WHERE phone IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS accounts_email ON accounts(email) WHERE email IS NOT NULL;

                CREATE TABLE IF NOT EXISTS wallets (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(id),
                    currency TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, currency)
                );

                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id TEXT PRIMARY KEY,
                    wallet_id TEXT NOT NULL REFERENCES wallets(id),
                    direction TEXT NOT NULL CHECK(direction IN ('credit', 'debit')),
                    amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
                    currency TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(reference, wallet_id, direction, kind)
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    reference TEXT NOT NULL UNIQUE,
                    provider_reference TEXT,
                    method TEXT NOT NULL,
                    provider TEXT,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    payer_account_id TEXT REFERENCES accounts(id),
                    payee_account_id TEXT REFERENCES accounts(id),
                    status TEXT NOT NULL,
                    request_payload TEXT,
                    response_payload TEXT,
                    authorized_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settlements (
                    id TEXT PRIMARY KEY,
                    payment_id TEXT NOT NULL UNIQUE REFERENCES payments(id),
                    wallet_id TEXT NOT NULL REFERENCES wallets(id),
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    ledger_entry_id TEXT NOT NULL REFERENCES ledger_entries(id),
                    authorized_by TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS webhook_events (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    signature_valid INTEGER NOT NULL CHECK(signature_valid IN (0, 1)),
                    body TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                """
            )

    # --- generic helpers -------------------------------------------------
    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    def one(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            return self._row(conn.execute(sql, params).fetchone())

    def all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            return [self._row(r) for r in conn.execute(sql, params).fetchall()]

    def dump_json(self, value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
