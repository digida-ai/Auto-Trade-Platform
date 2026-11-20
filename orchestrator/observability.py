"""Observability, sentry/watchdog, and alerts (ported/adapted from musashaban observability).

Checks are derived from live platform state: runs, automaton states, reports, map/readiness
signals. Alerts are persisted and surfaced in API/UI. Problems are surfaced for live
resolution (no hidden/simulated success).
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ALERTS_PATH, ensure_dirs
from .store import list_runs, load_agent_states, list_reports

_lock = threading.Lock()

SEVERITIES = {"info", "warn", "critical"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # handle trailing Z
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _age_seconds(ts: Optional[str]) -> Optional[float]:
    dt = _parse_iso(ts)
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def load_alerts() -> List[Dict[str, Any]]:
    ensure_dirs()
    if not ALERTS_PATH.exists():
        return []
    with _lock:
        try:
            data = json.loads(ALERTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            return []
    return []


def save_alerts(alerts: List[Dict[str, Any]]) -> None:
    ensure_dirs()
    with _lock:
        ALERTS_PATH.write_text(json.dumps(alerts, indent=2, ensure_ascii=False), encoding="utf-8")


def _alert(key: str, severity: str, title: str, detail: str, source: str = "watchdog", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sev = severity if severity in SEVERITIES else "info"
    return {
        "id": uuid.uuid4().hex[:12],
        "key": key,
        "severity": sev,
        "title": title,
        "detail": detail,
        "source": source,
        "meta": meta or {},
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
    }


def run_watchdog(stale_run_s: int = 300, stale_agent_s: int = 300) -> Dict[str, Any]:
    """Evaluate live platform state and replace current alert set (sentry)."""
    alerts: List[Dict[str, Any]] = []
    try:
        runs = list_runs(limit=50)
    except Exception:
        runs = []
    try:
        states = load_agent_states()
    except Exception:
        states = {"agents": {}, "admin": {"state": "idle"}}
    try:
        recent_reports = list_reports(run_id=None, limit=50)
    except Exception:
        recent_reports = []

    # Stalled/active runs
    for r in runs[:20]:
        status = r.get("status")
        rid = r.get("id")
        age = _age_seconds(r.get("updated_at") or r.get("created_at"))
        if status in ("queued", "running") and age is not None and age > stale_run_s:
            alerts.append(_alert(
                key=f"run.stalled.{rid}",
                severity="warn",
                title="Run stalled",
                detail=f"Run {rid} has status '{status}' with no update for {int(age)}s.",
                meta={"run_id": rid, "status": status, "age_s": int(age), "task": (r.get("task") or "")[:200]},
            ))
        if status == "failed":
            err = r.get("error") or ""
            # keep recent failures visible
            if age is not None and age < 3600:
                alerts.append(_alert(
                    key=f"run.failed.{rid}",
                    severity="critical",
                    title="Run failed",
                    detail=f"Run {rid} failed. {str(err)[:300]}".strip(),
                    meta={"run_id": rid, "error": str(err)[:500], "task": (r.get("task") or "")[:200]},
                ))

    # Agent states
    admin_state = (states or {}).get("admin") or {}
    if admin_state.get("state") == "failed":
        alerts.append(_alert(
            key="agent.failed.admin",
            severity="critical",
            title="Admin automaton failed",
            detail="Admin automaton is in failed state and requires live resolution.",
            meta={"agent": "admin", "state": "failed"},
        ))
    for name, st in ((states or {}).get("agents") or {}).items():
        sname = st.get("state")
        age = _age_seconds(st.get("updated_at"))
        if sname in ("failed", "blocked"):
            sev = "critical" if sname == "failed" else "warn"
            lr = st.get("last_report") or {}
            msg = lr.get("message") or ""
            alerts.append(_alert(
                key=f"agent.{sname}.{name}",
                severity=sev,
                title=f"Agent {sname}",
                detail=f"{name} is {sname}. {str(msg)[:250]}".strip(),
                meta={"agent": name, "state": sname, "last_report": lr},
            ))
        elif sname in ("running", "waking") and age is not None and age > stale_agent_s:
            alerts.append(_alert(
                key=f"agent.stale.{name}",
                severity="warn",
                title="Agent state stale",
                detail=f"{name} has been {sname} without state update for {int(age)}s.",
                meta={"agent": name, "state": sname, "age_s": int(age)},
            ))

    # Reporting gaps: active/running work but no recent reports at all
    active = [r for r in runs if r.get("status") in ("queued", "running")]
    if active and not recent_reports:
        alerts.append(_alert(
            key="reports.gap",
            severity="warn",
            title="Reporting gap",
            detail="Active runs detected but no recent reports are recorded.",
            meta={"active_runs": [r.get("id") for r in active[:10]]},
        ))

    # Readiness signals (musashaban-style surfacing): constitution/soul/access/paywall-funding docs present
    try:
        from .config import MUSASHABAN_PORT_DIR
        required = ["constitution.md", "soul.md", "access-methods.md", "paywall-funding.md", "automaton-model.md"]
        missing = [f for f in required if not (MUSASHABAN_PORT_DIR / f).exists()]
        if missing:
            alerts.append(_alert(
                key="readiness.musashaban_port",
                severity="critical",
                title="Musashaban port incomplete",
                detail=f"Missing port files: {', '.join(missing)}",
                source="readiness",
                meta={"missing": missing},
            ))
    except Exception:
        pass

    # de-dupe by key keeping most severe/latest
    by_key: Dict[str, Dict[str, Any]] = {}
    rank = {"info": 1, "warn": 2, "critical": 3}
    for a in alerts:
        k = a.get("key")
        prev = by_key.get(k)
        if not prev or rank.get(a.get("severity","info"),1) >= rank.get(prev.get("severity","info"),1):
            by_key[k] = a
    final = sorted(by_key.values(), key=lambda x: (-rank.get(x.get("severity","info"),1), x.get("created_at","")))
    save_alerts(final)
    return {"ok": True, "count": len(final), "alerts": final, "checked_at": _now()}


def get_alerts(active_only: bool = False) -> Dict[str, Any]:
    alerts = load_alerts()
    if active_only:
        alerts = [a for a in alerts if a.get("status", "active") == "active"]
    return {"count": len(alerts), "alerts": alerts}


def observability_status() -> Dict[str, Any]:
    alerts = load_alerts()
    counts = {"info": 0, "warn": 0, "critical": 0}
    for a in alerts:
        s = a.get("severity", "info")
        if s in counts:
            counts[s] += 1
    return {
        "alerts_total": len(alerts),
        "alerts_by_severity": counts,
        "status": "critical" if counts["critical"] else ("degraded" if counts["warn"] else "healthy"),
    }
