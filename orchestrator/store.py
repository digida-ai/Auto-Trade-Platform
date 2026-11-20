import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import RUNS_DIR, STATE_DIR, ensure_dirs

_lock = threading.Lock()
AGENT_STATES_PATH = STATE_DIR / "agent_states.json"
REPORTS_DIR = STATE_DIR / "reports"

# --- Payment ledger (payment_tx) -------------------------------------------
# Shared table written by the ported RudiPay manager
# (musashaban-port/payment/rudipay.py) through payment.db.record_payment.
PAYMENT_TX_SCHEMA = """
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
);
"""


def ensure_payment_schema(conn) -> None:
    conn.executescript(PAYMENT_TX_SCHEMA)
    conn.commit()


def record_payment(
    conn,
    *,
    rail: str,
    kind: str,
    amount_minor: float,
    currency: str,
    counterparty: str,
    account: str,
    reference: str,
    status: str = "completed",
) -> int:
    from .payment.db import _now, _new_id

    with _lock:
        conn.execute(
            "INSERT INTO payment_tx (id, rail, kind, amount_minor, currency, counterparty, "
            "account, reference, status, created_at, occurred_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (_new_id(), rail, kind, float(amount_minor), currency.upper(),
             counterparty, account, reference, status, _now(), _now()),
        )
        conn.commit()
    row = conn.execute("SELECT id FROM payment_tx WHERE reference = ? ORDER BY id DESC LIMIT 1",
                       (reference,)).fetchone()
    return row["id"] if row else 0


def update_payment_status(conn, row_id, status: str, note: str = "") -> None:
    from .payment.db import _now
    if row_id is None:
        raise ValueError("row_id required")
    conn.execute("UPDATE payment_tx SET status = ?, updated_at = ? WHERE id = ?",
                 (status, _now(), str(row_id)))
    conn.commit()


def list_payment_tx(account: Optional[str] = None, currency: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = __import__("sqlite3").connect(str(STATE_DIR / "payment.db"))
    conn.row_factory = __import__("sqlite3").Row
    try:
        ensure_payment_schema(conn)
        sql = "SELECT * FROM payment_tx"
        conds, params = [], []
        if account:
            conds.append("account = ?")
            params.append(account)
        if currency:
            conds.append("currency = ?")
            params.append(currency.upper())
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run(task: str, targets: List[str], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_dirs()
    run_id = uuid.uuid4().hex[:12]
    run = {
        "id": run_id,
        "task": task,
        "targets": targets,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "steps": [],
        "result": None,
        "error": None,
        "meta": meta or {},
    }
    save_run(run)
    return run


def run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def save_run(run: Dict[str, Any]) -> None:
    ensure_dirs()
    run["updated_at"] = _now()
    with _lock:
        with open(run_path(run["id"]), "w", encoding="utf-8") as f:
            json.dump(run, f, indent=2, ensure_ascii=False)


# --- Automaton state & reporting (additive, preserves existing run behavior) ---

def ensure_automaton_dirs() -> None:
    ensure_dirs()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_agent_states() -> Dict[str, Any]:
    ensure_automaton_dirs()
    if not AGENT_STATES_PATH.exists():
        return {"agents": {}, "admin": {"state": "idle", "overlay": None, "updated_at": _now()}}
    with _lock:
        try:
            with open(AGENT_STATES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"agents": {}, "admin": {"state": "idle", "overlay": None, "updated_at": _now()}}
    data.setdefault("agents", {})
    data.setdefault("admin", {"state": "idle", "overlay": None, "updated_at": _now()})
    return data


def save_agent_states(data: Dict[str, Any]) -> None:
    ensure_automaton_dirs()
    data["updated_at"] = _now()
    with _lock:
        with open(AGENT_STATES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def update_agent_state(agent: str, state: str, **fields: Any) -> Dict[str, Any]:
    data = load_agent_states()
    now = _now()
    if agent == "admin":
        entry = data.get("admin") or {}
        entry.update({"state": state, "updated_at": now})
        entry.update(fields)
        data["admin"] = entry
    else:
        agents = data.setdefault("agents", {})
        entry = agents.get(agent) or {}
        entry.update({"agent": agent, "state": state, "updated_at": now})
        entry.update(fields)
        agents[agent] = entry
    save_agent_states(data)
    return data


def get_agent_state(agent: str) -> Dict[str, Any]:
    data = load_agent_states()
    if agent == "admin":
        return data.get("admin") or {"state": "idle"}
    return (data.get("agents") or {}).get(agent) or {"agent": agent, "state": "idle"}


def append_report(report: Dict[str, Any]) -> Dict[str, Any]:
    ensure_automaton_dirs()
    report = dict(report)
    report.setdefault("id", uuid.uuid4().hex[:12])
    report.setdefault("timestamp", _now())
    run_id = report.get("run_id")
    with _lock:
        # append to run-scoped reports file if run known
        if run_id:
            p = REPORTS_DIR / f"{run_id}.jsonl"
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, ensure_ascii=False) + "\n")
        # also append to global recent reports
        gp = REPORTS_DIR / "recent.jsonl"
        with open(gp, "a", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False) + "\n")
    # track last report on agent state
    agent = report.get("agent")
    if agent:
        try:
            update_agent_state(agent, get_agent_state(agent).get("state", "idle"), last_report=report)
        except Exception:
            pass
    return report


def _read_jsonl_tail(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with _lock:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
    except Exception:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def list_reports(run_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_automaton_dirs()
    if run_id:
        return _read_jsonl_tail(REPORTS_DIR / f"{run_id}.jsonl", limit)
    return _read_jsonl_tail(REPORTS_DIR / "recent.jsonl", limit)


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    p = run_path(run_id)
    if not p.exists():
        return None
    with _lock:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    ensure_dirs()
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    runs = []
    for p in files[:limit]:
        try:
            with _lock:
                with open(p, "r", encoding="utf-8") as f:
                    runs.append(json.load(f))
        except Exception:
            continue
    return runs
