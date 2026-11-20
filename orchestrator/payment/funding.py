r"""Funding requests and development needs — port of
``D:\AI\automation\musashaban\src\payments\funding.ts``.

A funding request names an expense (what it buys) before asking for money.
Collections are recorded against it until the target is met; the automaton
can then pay the expense through RudiPay/GSMA/telegram with the request id
as the reference.

Statuses:
  * funding: ``open`` → ``funded`` (all target met) | ``cancelled``
  * needs: ``open`` → ``doing`` → ``done``, or ``blocked``
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import PAYMENT_DB, connect_db

MONEY_SCALES = {"UGX": 0, "TZS": 0, "KES": 0, "XAF": 0, "ZAR": 2, "NAD": 2, "GHS": 2, "VND": 0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS funding_request (
          id TEXT PRIMARY KEY, purpose TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
          amount_minor REAL NOT NULL, collected_minor REAL NOT NULL DEFAULT 0,
          currency TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
          reference TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS funding_collections (
          id TEXT PRIMARY KEY, funding_id TEXT NOT NULL, amount_minor REAL NOT NULL DEFAULT 0,
          rail TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
          recorded_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS development_needs (
          id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
          priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open',
          estimated_minor REAL, currency TEXT, funding_id TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    db.commit()


def _scaled(amount: float, currency: str) -> float:
    scale = MONEY_SCALES.get(currency.upper(), 2)
    return round(amount * (10 ** scale))


def _unscale(minor: float, currency: str) -> float:
    scale = MONEY_SCALES.get(currency.upper(), 2)
    return round(minor / (10 ** scale), 2)


def create_funding_request(
    purpose: str,
    detail: str,
    amount: float,
    currency: str = "UGX",
    requested_by: str = "",
    reference: Optional[str] = None,
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    db = db or connect_db(PAYMENT_DB)
    _ensure(db)
    fid = reference or ("fund_" + uuid.uuid4().hex[:10])
    now = _now()
    db.execute(
        """INSERT INTO funding_request
           (id, purpose, detail, amount_minor, collected_minor, currency, status, reference,
            requested_by, created_at, updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (fid, purpose, detail, _scaled(amount, currency), 0, currency, "open", fid,
         requested_by, now, now),
    )
    db.commit()
    row = db.execute("SELECT * FROM funding_request WHERE id = ?", (fid,)).fetchone()
    return dict(row)


def get_funding_request(funding_id: str, db: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    row = db.execute("SELECT * FROM funding_request WHERE id = ?", (funding_id,)).fetchone()
    return dict(row) if row else None


def list_funding_requests(status: Optional[str] = None, db: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    sql = "SELECT * FROM funding_request"
    conds, params = [], []
    if status:
        conds.append("status = ?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def list_collections(funding_id: str, db: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    rows = db.execute(
        "SELECT * FROM funding_collections WHERE funding_id = ? ORDER BY created_at ASC", (funding_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def add_collection(
    funding_id: str,
    amount: float,
    currency: str = "UGX",
    rail: str = "",
    reference: str = "",
    recorded_by: str = "",
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    db = db or connect_db(PAYMENT_DB)
    _ensure(db)
    req = get_funding_request(funding_id, db=db)
    if not req:
        raise ValueError(f"funding request {funding_id} not found")
    if req["status"] != "open":
        raise ValueError(f"funding request {funding_id} is not open (status={req['status']})")
    cid = "coll_" + uuid.uuid4().hex[:10]
    now = _now()
    db.execute(
        """INSERT INTO funding_collections (id, funding_id, amount_minor, rail, reference, recorded_by, created_at)
         VALUES (?,?,?,?,?,?,?)""",
        (cid, funding_id, _scaled(amount, currency), rail, reference, recorded_by, now),
    )
    collected = db.execute(
        "SELECT COALESCE(SUM(amount_minor),0) AS s FROM funding_collections WHERE funding_id = ?",
        (funding_id,),
    ).fetchone()["s"]
    db.execute("UPDATE funding_request SET collected_minor = ?, updated_at = ? WHERE id = ?",
               (collected, now, funding_id))
    status = "funded" if collected >= req["amount_minor"] else "open"
    db.execute("UPDATE funding_request SET status = ?, updated_at = ? WHERE id = ?", (status, now, funding_id))
    db.commit()
    return {"id": cid, "funding_id": funding_id, "amount": _unscale(collected, req["currency"]),
            "amount_target": _unscale(req["amount_minor"], req["currency"]),
            "collected_pct": round(collected / req["amount_minor"], 2) if req["amount_minor"] else 100}


def cancel_funding(funding_id: str, db: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    db = db or connect_db(PAYMENT_DB)
    db.execute("UPDATE funding_request SET status='cancelled', updated_at=? WHERE id=?", (_now(), funding_id))
    db.commit()
    return get_funding_request(funding_id, db=db) or {}


def create_development_need(
    title: str,
    detail: str = "",
    priority: str = "normal",
    db: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    db = db or connect_db(PAYMENT_DB)
    _ensure(db)
    nid = "need_" + uuid.uuid4().hex[:10]
    now = _now()
    db.execute(
        "INSERT INTO development_needs (id, title, detail, priority, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (nid, title, detail, priority, "open", now, now),
    )
    db.commit()
    return get_development_need(nid, db=db) or {}


def get_development_need(need_id: str, db: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    row = db.execute("SELECT * FROM development_needs WHERE id = ?", (need_id,)).fetchone()
    return dict(row) if row else None


def list_development_needs(status: Optional[str] = None, db: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    sql = "SELECT * FROM development_needs"
    conds, params = [], []
    if status:
        conds.append("status = ?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def update_development_need(need_id: str, status: Optional[str] = None, funding_id: Optional[str] = None,
                            db: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    db = db or connect_db(PAYMENT_DB)
    _ensure(db)
    if status not in (None, "open", "doing", "done", "blocked"):
        raise ValueError("invalid status")
    db.execute(
        "UPDATE development_needs SET status = COALESCE(?, status), funding_id = COALESCE(?, funding_id), updated_at = ? WHERE id = ?",
        (status, funding_id, _now(), need_id),
    )
    db.commit()
    return get_development_need(need_id, db=db)
