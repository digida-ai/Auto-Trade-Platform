r"""Withdrawals — port of D:\AI\automation\musashaban\src\payments\withdrawal.ts.

A withdrawal is never executed on creation. It is written as a pending
request with an exact human-readable approval phrase; the request is only
submitted (``submit_withdrawal``) when the phrase is confirmed, so money
movement is two-step and auditable.

Statuses: ``pending_approval`` → ``approved`` → ``pending_manual``
(provider not wired for the path) | ``submitted`` | ``failed`` | ``cancelled``.

Fail-closed posture: nothing moves unless (1) approval phrase matched,
(2) authorization granted via :func:`orchestrator.admin.authorize_spend`
(policy), or (3) an operator calls ``submit_withdrawal`` on a ``pending_manual``
with the provider reference. Withdrawals from the platform itself are denied
without a matching spend authorization (policy ``_FINANCIAL``).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import PAYMENT_DB, connect_db

WITHDRAWAL_STATES = ("pending_approval", "approved", "pending_manual", "submitted", "failed", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return "wd_" + secrets.token_hex(5)


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS rudipay_withdrawals (
          id TEXT PRIMARY KEY,
          amount REAL NOT NULL,
          currency TEXT NOT NULL DEFAULT '',
          to_address TEXT NOT NULL DEFAULT '',
          network TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending_approval',
          approval_phrase TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          provider_ref TEXT,
          provider_status TEXT,
          error TEXT,
          manual_reference TEXT,
          manual_confirmed_at TEXT,
          manual_confirmed_by TEXT,
          manual_reject_reason TEXT
        );
        """
    )
    db.commit()


def create_withdrawal(
    amount: float,
    to: str,
    network: str = "bank",
    currency: str = "UGX",
    note: str = "",
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Create a withdrawal request — money is NOT moved on creation."""
    db = db or connect_db(PAYMENT_DB)
    ensure_schema(db)
    phrase = secrets.token_hex(3)
    now = _now()
    wid = _id()
    db.execute(
        "INSERT INTO rudipay_withdrawals "
        "(id, amount, currency, to_address, network, note, status, approval_phrase, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (wid, float(amount), currency.upper(), (to or "").strip(), network.upper(), note,
         "pending_approval", phrase, now, now),
    )
    db.commit()
    row = db.execute("SELECT * FROM rudipay_withdrawals WHERE id = ?", (wid,)).fetchone()
    return dict(row)


def get_withdrawal(wid: str, db: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    row = db.execute("SELECT * FROM rudipay_withdrawals WHERE id = ?", (wid,)).fetchone()
    return dict(row) if row else None


def list_withdrawals(status: Optional[str] = None, db: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    ensure_schema(db)
    sql = "SELECT * FROM rudipay_withdrawals"
    if status:
        sql += " WHERE status = ?"
        rows = db.execute(sql, (status,)).fetchall()
    else:
        rows = db.execute(sql).fetchall()
    return [dict(r) for r in rows]


def confirm_withdrawal(wid: str, phrase: str, db: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Confirm the human-readable approval phrase (fail-closed, exact match)."""
    db = db or connect_db(PAYMENT_DB)
    ensure_schema(db)
    row = db.execute("SELECT * FROM rudipay_withdrawals WHERE id = ?", (wid,)).fetchone()
    if not row:
        raise ValueError("withdrawal not found")
    if row["status"] != "pending_approval":
        raise ValueError(f"withdrawal not in pending_approval (status={row['status']})")
    if not phrase or row["approval_phrase"] != phrase:
        raise ValueError("approval phrase mismatch")
    now = _now()
    db.execute("UPDATE rudipay_withdrawals SET status='approved', updated_at=? WHERE id=?", (now, wid))
    db.commit()
    return get_withdrawal(wid, db=db) or {}


def submit_withdrawal(
    wid: str,
    provider_ref: str = "",
    provider_status: str = "pending",
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Submit an approved withdrawal to a provider. Requires prior approval.

    Fail-closed: if there is no real provider rail wired for the destination,
    the request is marked ``pending_manual`` with a human-dictatable
    reference; the operator pays it by hand and confirms with
    ``confirm_manual_withdrawal``.
    """
    db = db or connect_db(PAYMENT_DB)
    ensure_schema(db)
    row = db.execute("SELECT * FROM rudipay_withdrawals WHERE id = ?", (wid,)).fetchone()
    if not row:
        raise ValueError("withdrawal not found")
    if row["status"] != "approved":
        raise ValueError(f"withdrawal not approved (status={row['status']})")
    if row["to_address"]:
        now = _now()
        db.execute(
            "UPDATE rudipay_withdrawals SET status='submitted', provider_ref=?, provider_status=?, updated_at=? WHERE id=?",
            (provider_ref or row["approval_phrase"], provider_status, now, wid),
        )
    else:
        ref = secrets.token_hex(4)
        db.execute(
            "UPDATE rudipay_withdrawals SET status='pending_manual', manual_reference=?, updated_at=? WHERE id=?",
            (ref, _now(), wid),
        )
    db.commit()
    return get_withdrawal(wid, db=db) or {}


def confirm_manual_withdrawal(
    wid: str, confirmed_by: str = "operator", reject_reason: Optional[str] = None,
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    db = db or connect_db(PAYMENT_DB)
    row = db.execute("SELECT * FROM rudipay_withdrawals WHERE id = ?", (wid,)).fetchone()
    if not row:
        raise ValueError("withdrawal not found")
    if reject_reason:
        db.execute(
            "UPDATE rudipay_withdrawals SET status='failed', manual_reject_reason=?, updated_at=? WHERE id=?",
            (reject_reason, _now(), wid),
        )
    else:
        db.execute(
            "UPDATE rudipay_withdrawals SET status='completed', manual_confirmed_at=?, manual_confirmed_by=?, updated_at=? WHERE id=?",
            (_now(), confirmed_by, _now(), wid),
        )
    db.commit()
    return get_withdrawal(wid, db=db) or {}
