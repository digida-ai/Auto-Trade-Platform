"""Policy engine for agent actions.

Ported from the automaton's ``src/agent/policy-rules`` (authority,
command-safety, path-protection, financial, rate-limits). Every command an
eagent asks the orchestrator to run is evaluated here before execution. A
denied decision is final: dispatch records ``blocked`` and never reports
success.

Rules are intentionally conservative. They do not try to sandbox arbitrary
commands; they stop the classes of action the constitution forbids or that
would escape the subplatform's scope:

* authority     - external/heartbeat-sourced calls cannot run destructive tools
* command-safety- destructive shell patterns, remote code execution, secret exfiltration
* path-protection - writes to the constitution/agent policy, or cwd outside scope
* financial     - settlement/withdrawal/live-trading without explicit authorization
* rate-limits   - per-agent command budget per minute
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Optional

from .config import PLATFORM_ROOT, STATE_DIR

# Constitution and agent policy are immutable to agents (automaton's
# PROTECTED_PATHS equivalent).
PROTECTED_PATHS = (
    "musashaban-port/constitution.md",
    "musashaban-port/soul.md",
    "musashaban-port/access-methods.md",
    "musashaban-port/paywall-funding.md",
    "agents/registry.json",
    "agents/specs",
    "agents/eagents",
    "orchestrator/policy.py",
    "orchestrator/state/audit.log",
    ".env",
)

TRUSTED_SOURCES = {"operator", "admin", "agent", "task"}

# Destructive operations that are never dispatched from this platform.
_DESTRUCTIVE = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|/\*|\*|~|\.)\s*$", "recursive delete of a root/home path"),
    (r"\brm\s+-rf\s+--no-preserve-root", "recursive delete with --no-preserve-root"),
    (r"\b(mkfs|fdisk|diskpart|format)\b", "filesystem formatting/partitioning"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "host power control"),
    (r"\bdd\s+if=", "raw disk write"),
    (r":\s*\(\s*\)\s*\{", "fork bomb"),
    (r"\bdel\s+/[fsq]\b|\brd\s+/s\b|Remove-Item\s+-Recurse\s+-Force\s+[A-Za-z]:\\?\s*$", "recursive delete of a drive/root"),
    (r"\bgit\s+push\b.*--force", "history-destroying push"),
    (r"\bnpm\s+publish\b|\bcargo\s+publish\b|\btwine\s+upload\b", "publishing artifacts externally"),
]

# Downloading and piping into an interpreter, or reading secrets out.
_REMOTE_EXEC = [
    (r"(curl|wget|Invoke-WebRequest|iwr)\b[^\n|]*\|\s*(sh|bash|zsh|pwsh|powershell|iex|node|python)", "remote code executed without review"),
    (r"\b(IEX|Invoke-Expression)\b", "dynamic code execution"),
]
_SECRET_EXFIL = [
    (r"(curl|wget|Invoke-WebRequest|iwr|nc|ncat|scp|ftp)\b[^\n]*\.env\b", "uploading .env / credentials"),
    (r"\.env\b[^\n]*\|[^\n]*(curl|wget|nc|ncat|scp|ftp|Invoke-WebRequest|iwr)", "piping .env into a network client"),
    (r"(curl|wget|Invoke-WebRequest|iwr|nc|ncat|scp|ftp)\b[^\n]*\b(env|printenv|Get-ChildItem\s+env:)", "uploading process environment"),
    (r"(curl|wget|Invoke-WebRequest|iwr)\b[^\n]*\.(pem|key|p12|pfx)\b", "uploading key material"),
    (r"\bbase64\b[^\n]*\.env\b", "encoding credentials for exfiltration"),
]

# Money movement and live-capital actions.
_FINANCIAL = [
    (r"\b(withdraw|withdrawal|payout|transfer)\b", "money movement"),
    (r"\b(settle|settlement)\b", "settlement execution"),
    (r"--live\b|\blive[-_]trading\b|\breal[-_]money\b|\bmainnet\b", "live-capital trading"),
]

_WRITE_REDIRECT = re.compile(r"(>>?|\btee\b|\bSet-Content\b|\bOut-File\b|\bcp\b|\bmv\b|\bcopy\b|\bmove\b)\s*([^\s|;&]+)", re.IGNORECASE)

_RATE_LIMIT_PER_MINUTE = int(os.environ.get("PLATFORM_AGENT_COMMAND_LIMIT", "30"))
_rate_windows: Dict[str, Deque[float]] = defaultdict(deque)


# --- Recorded operator authorizations (spend) ------------------------------
# The policy default is "deny money movement". Operator authorizations are
# recorded in a durable JSONL ledger so a denial is final, and every
# authorization is visible afterwards (audit trail). 0-funding default stays.

SPEND_AUTH_PATH = STATE_DIR / "spend_authorizations.jsonl"

# When there is no operator authorization on file and funding is "unfunded",
# financial commands are denied. Authorized-spend sources, in order:
#   1. PLATFORM_ALLOW_LIVE_TRADING=1  (coarse operator env switch, legacy)
#   2. authorized_spend=True passed by the caller
#   3. a live spend_auth token recorded via admin.authorize_spend
#   4. any funding state other than "unfunded" (float/revenue funded)


@dataclass
class Decision:
    allow: bool
    rule: str
    reason_code: str
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _deny(rule: str, code: str, message: str) -> Decision:
    return Decision(False, rule, code, message)


def _allow(rule: str = "none") -> Decision:
    return Decision(True, rule, "allowed", "Allowed by policy.")


def load_spend_authorizations() -> list[dict]:
    """Active recorded spend authorizations (operator actions on the ledger)."""
    out: list[dict] = []
    try:
        with open(SPEND_AUTH_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict) and rec.get("status", "active") == "active":
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


def spend_auth_active(agent: str, spend_auth: Optional[str]) -> bool:
    """True if a live spend_auth token matches this agent (record exists, is
    active, is not expired). Unknown tokens fail closed."""
    if not spend_auth:
        return False
    for rec in load_spend_authorizations():
        if rec.get("spend_auth_id") != spend_auth:
            continue
        if rec.get("agent") not in (None, agent):
            return False
        expires = rec.get("expires")
        if expires:
            try:
                if datetime.fromisoformat(expires) < datetime.now(timezone.utc):
                    return False
            except Exception:
                return False
        return True
    return False


def _normalise(path_value: str, base: Path) -> Path:
    p = Path(os.path.expandvars(os.path.expanduser(path_value)))
    if not p.is_absolute():
        p = base / p
    try:
        return p.resolve()
    except Exception:
        return p.absolute()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except Exception:
        return False


def protected_paths() -> list[Path]:
    return [(PLATFORM_ROOT / rel).resolve() for rel in PROTECTED_PATHS]


def _touches_protected(command: str, cwd: Path) -> Optional[str]:
    """Detect writes/deletes aimed at protected paths."""
    lowered = command.lower().replace("/", "\\")
    for protected in protected_paths():
        rel = str(protected).lower().replace("/", "\\")
        if rel in lowered or protected.name.lower() in lowered and "\\" in lowered:
            if re.search(r"\b(rm|del|rd|mv|move|cp|copy|>|>>|tee|set-content|out-file|chmod|attrib)\b", lowered):
                return rel
    for match in _WRITE_REDIRECT.finditer(command):
        target = _normalise(match.group(2), cwd)
        for protected in protected_paths():
            if _inside(target, protected) or target == protected:
                return str(protected)
    return None


def evaluate(
    command: str,
    cwd: Path,
    *,
    agent: str = "admin",
    source: str = "admin",
    scope: Optional[Path] = None,
    authorized_spend: bool = False,
    guardrails: Optional[Dict[str, Any]] = None,
    spend_auth: Optional[str] = None,
    funding_state: Optional[str] = None,
) -> Decision:
    """Evaluate one shell command before it is executed.

    ``scope`` is the subplatform's component directory; eagent commands must
    stay inside it. Admin/operator commands may use the whole platform root.

    Financial commands are denied by default. They become allowed only via an
    explicit operator authorization: ``authorized_spend``, a live ``spend_auth``
    token from ``admin.authorize_spend``, ``PLATFORM_ALLOW_LIVE_TRADING=1``, or
    when the platform funding state is not "unfunded".
    """
    command = (command or "").strip()
    if not command:
        return _deny("validation", "empty_command", "No command supplied.")

    cwd = _normalise(str(cwd), PLATFORM_ROOT)
    if not _inside(cwd, PLATFORM_ROOT):
        return _deny("path-protection", "cwd_outside_platform", f"Working directory {cwd} is outside the platform root.")

    if scope is not None:
        scope = _normalise(str(scope), PLATFORM_ROOT)
        if not _inside(cwd, scope) and agent != "admin":
            return _deny(
                "path-protection",
                "cwd_outside_scope",
                f"eagent {agent} may only act inside {scope}, not {cwd}.",
            )

    # --- authority -------------------------------------------------------
    if source not in TRUSTED_SOURCES:
        if re.search(r"\b(rm|del|rd|format|shutdown|reboot|pip|npm|cargo|uv|git\s+push)\b", command, re.IGNORECASE):
            return _deny(
                "authority",
                "external_destructive",
                f"source '{source}' may not run install/destructive commands.",
            )

    # --- command safety --------------------------------------------------
    for pattern, why in _DESTRUCTIVE:
        if re.search(pattern, command, re.IGNORECASE):
            return _deny("command-safety", "destructive_command", f"Blocked: {why}.")
    for pattern, why in _REMOTE_EXEC:
        if re.search(pattern, command, re.IGNORECASE):
            return _deny("command-safety", "remote_code_execution", f"Blocked: {why}.")
    for pattern, why in _SECRET_EXFIL:
        if re.search(pattern, command, re.IGNORECASE):
            return _deny("command-safety", "secret_exfiltration", f"Blocked: {why}.")

    # --- path protection -------------------------------------------------
    hit = _touches_protected(command, cwd)
    if hit:
        return _deny("path-protection", "protected_path", f"Blocked: attempt to modify protected path {hit}.")

    # --- financial -------------------------------------------------------
    financial_allowed = (
        bool(authorized_spend)
        or os.environ.get("PLATFORM_ALLOW_LIVE_TRADING", "") == "1"
        or (spend_auth is not None and spend_auth_active(agent, spend_auth))
        or (funding_state or "unfunded") != "unfunded"
    )
    for pattern, why in _FINANCIAL:
        if re.search(pattern, command, re.IGNORECASE) and not financial_allowed:
            return _deny(
                "financial",
                "unauthorized_financial_action",
                (
                    f"Blocked: {why} requires explicit operator authorization "
                    f"(PLATFORM_ALLOW_LIVE_TRADING=1, admin-approved run, a live spend_auth token "
                    f"from admin.authorize_spend, or float/revenue funding state; "
                    f"platform state: {funding_state or 'unfunded'})."
                ),
            )

    # --- per-agent guardrails -------------------------------------------
    if guardrails:
        denied = set(guardrails.get("denied_commands") or [])
        for fragment in denied:
            if fragment and fragment.lower() in command.lower():
                return _deny("guardrails", "agent_denied_command", f"Blocked by {agent} guardrails: {fragment}.")
        max_seconds = int(guardrails.get("max_command_seconds") or 0)
        if max_seconds <= 0:
            pass

    # --- rate limits -----------------------------------------------------
    now = time.time()
    window = _rate_windows[agent]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= _RATE_LIMIT_PER_MINUTE:
        return _deny(
            "rate-limits",
            "rate_limited",
            f"{agent} exceeded {_RATE_LIMIT_PER_MINUTE} commands/minute.",
        )
    window.append(now)

    return _allow()
