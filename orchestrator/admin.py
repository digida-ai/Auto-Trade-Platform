"""Admin agent — orchestration engine (automaton).

Routes tasks to eagents (per registry), tracks runs, dispatches work, and
executes actions using local tool capabilities. Runs execute asynchronously so
the UI can poll for live progress.

Automaton model (ported from musashaban): every agent is a stateful automaton
under the constitution/soul; every eagent reports to admin; admin erects
paywalls, settles, and funds (fail-closed, 0-funding by default).
"""

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PLATFORM_ROOT, STATE_DIR
from .registry import route_task, component_path, subplatforms, load_registry, get_subplatform
from . import policy
from . import stack
from . import workflows as workflows_mod
from .agent_specs import component_scope, constitution_sha256, ensure_specs, guardrails_for, load_spec, resolve_spec, validate_spec
from .components import component_readiness
from .store import (
    new_run, save_run, get_run, list_runs,
    update_agent_state, get_agent_state, load_agent_states, save_agent_states,
    append_report, list_reports, _now,
)
from .tools import run_shell_streaming, read_text, write_text, list_dir
from .components import readiness as component_readiness_all
from .observability import run_watchdog
from .mcp import (
    get_mcp_registry, get_mcp_client, MCPTool, MCPDataResource, MCPPrompt,
)
from .worker import get_worker_lifecycle, Worker, WORKER_STATES
from .skills import get_skill_catalogue, AgentSkillContribution
from .planner import Planner, Plan, PlanStep
from .router import get_router, Router, Route, RoutedPlan


# --- Autonomous bootup (admin takes control at startup) ---

BOOTUP_INTERVAL_S = 60  # watchdog re-check interval
_LAST_BOOTUP_REPORT: Dict[str, Any] = {}


def last_bootup_report() -> Dict[str, Any]:
    """The last bootup survey + automation scope (no re-scan)."""
    if not _LAST_BOOTUP_REPORT:
        return {"ok": False, "error": "bootup has not run yet"}
    return {"ok": True, **_LAST_BOOTUP_REPORT}


def bootup_initiative() -> Dict[str, Any]:
    """Admin takes control at bootup: survey every subplatform, initialize agent
    states, heal specs, register MCP, and run the watchdog.

    Scope is honest: the **control plane** (states, spec validation/auto-generation,
    watchdog, MCP registration, workflow selection/gates, reporting, sentry,
    funding/settlement ledger) is automated from boot. **Preset directives are
    also activated** as real runs: ready components get manifest-derived commands
    and blocked/needs_setup components get a real init run. Results remain gated
    by readiness — a component whose dependencies are missing is reported blocked
    from the actual run evidence, never as successful.
    """
    init_automaton_states()
    transition("admin", "waking", None, task="bootup", message="admin taking control at bootup", overlay="bootup")

    report = {
        "phase": "bootup",
        "admin_state": "waking",
        "timestamp": _now(),
        "subplatforms": [],
        "ready": 0,
        "needs_setup": 0,
        "blocked": 0,
        "errors": [],
        "next_steps": [],
    }

    for sp in subplatforms():
        name = sp.get("name")
        comp_name = sp.get("component")
        comp_dir = component_path(sp)

        # Ensure agent state exists
        try:
            cur = get_agent_state(name)
            if not cur or cur.get("state") is None:
                update_agent_state(name, "idle")
        except Exception:
            pass

        # Check component readiness
        try:
            rd = component_readiness(name) or {}
        except Exception as e:
            rd = {"status": "blocked", "errors": [str(e)], "warnings": [], "next_steps": [], "manifests": [], "units": [], "services": [], "missing_tools": [], "deps_installed": False}

        status = rd.get("status", "unknown")
        entry = {
            "name": name,
            "component": comp_name,
            "status": status,
            "errors": rd.get("errors", []),
            "warnings": rd.get("warnings", []),
            "next_steps": rd.get("next_steps", []),
        }
        report["subplatforms"].append(entry)

        if status == "ready":
            report["ready"] += 1
        elif status == "blocked":
            report["blocked"] += 1
            report["errors"].extend([f"{name}: {e}" for e in rd.get("errors", [])])
        else:
            report["needs_setup"] += 1

        report["next_steps"].extend(rd.get("next_steps", []))

    # Count agent states
    states = load_agent_states()
    agent_count = len((states.get("agents") or {}))
    report["agent_count"] = agent_count

    # Run observability watchdog to surface any pre-existing alerts
    try:
        watchdog = run_watchdog()
        report["watchdog"] = {
            "alerts_total": watchdog.get("count", 0),
            "alerts": watchdog.get("alerts", [])[:10],
        }
    except Exception as e:
        report["watchdog"] = {"error": str(e)}
    # Register MCP servers for all agents (agent-to-agent orchestration layer)
    try:
        mcp_result = register_mcp_servers_for_agents()
        report["mcp_registration"] = mcp_result
    except Exception as e:
        report["mcp_registration"] = {"error": str(e)}


    # Automation scope: control plane runs at boot; component work is gated.
    total = len(report["subplatforms"])
    runnable = report["ready"]
    report["automation"] = {
        "mode": "control_plane_and_activation_automated__results_readiness_gated",
        "autonomous_from_boot": [
            "agent state initialization", "spec validation + auto-generation",
            "watchdog + alerts", "MCP capability registration",
            "workflow selection / gates / replication decisions",
            "mandatory eagent->admin->operator reporting",
            "sentry deployment/hosting/security watch",
            "funding ledger + fail-closed spend authorizations",
            "activation of preset directives into real durable runs (init for blocked, "
            "manifest-derived commands for ready)",
        ],
        "not_autonomous": [
            f"declaring success for {total - runnable} of {total} subplatforms that are "
            "needs_setup/blocked until their real init run and readiness re-check pass",
            "live capital movement, settlement, deployment or policy changes (operator/human gated)",
        ],
        "claim": (
            "Bootup automates administration, supervision and activation: directives become "
            "real runs with live evidence. Results are readiness-gated; blocked components "
            "are reported from actual run output, never silently skipped or faked."
        ),
    }
    report["summary"] = (
        f"Bootup complete: control plane automated; "
        f"{runnable} ready, {report['needs_setup']} needs setup, "
        f"{report['blocked']} blocked out of {total} subplatforms."
    )
    report["admin_state"] = "idle"
    report["phase"] = "complete"

    _LAST_BOOTUP_REPORT.clear()
    _LAST_BOOTUP_REPORT.update(report)

    # Turn the preset directives into real work. Without this, boot only surveys
    # and the directives remain inert text that fails closed at dispatch.
    try:
        report["activation"] = activate_platform(boot_report=report)
    except Exception as e:
        report["activation"] = {"error": str(e)}

    transition("admin", "idle", None, task="bootup", message="bootup survey complete", overlay=None)
    return report


# ============================================================================
# Boot activation — preset directives become real, executable agent work
# ============================================================================


def _component_boot_command(sp: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Derive a real activation command from the component's own manifests.

    Nested manifests (up to three levels, e.g. Pix's python/ and rust/ variants)
    are considered. Nothing is hardcoded or faked: a command is returned only
    when the component actually contains the manifest that justifies it.
    """
    comp_dir = component_path(sp)
    if not comp_dir or not Path(comp_dir).exists():
        return None
    comp_dir = Path(comp_dir)
    skip = {"node_modules", ".git", ".venv", "venv", "dist", "build", "target", "__pycache__", ".next", ".turbo", "vendor", ".cache"}

    found: Dict[str, List[Path]] = {}
    for base, dirs, files in os.walk(comp_dir):
        depth = len(Path(base).relative_to(comp_dir).parts)
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".") and depth < 3]
        for filename in files:
            if filename in {"package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "pom.xml"}:
                found.setdefault(filename, []).append(Path(base) / filename)

    def result(command: str, manifest: Path) -> Dict[str, str]:
        return {"command": command, "cwd": str(manifest.parent)}

    for pkg in sorted(found.get("package.json", [])):
        try:
            scripts = (json.loads(read_text(str(pkg)) or "{}").get("scripts") or {})
            for key in ("build", "test", "check", "lint"):
                if key in scripts:
                    return result(f"npm run {key}", pkg)
        except Exception:
            pass
    for manifest in sorted(found.get("pyproject.toml", [])):
        return result("python -m compileall -q .", manifest)
    for manifest in sorted(found.get("Cargo.toml", [])):
        return result("cargo check", manifest)
    for manifest in sorted(found.get("pom.xml", [])):
        return result("mvn -q -DskipTests compile", manifest)
    for pkg in sorted(found.get("package.json", [])):
        return result("npm ls --depth=0", pkg)
    for manifest in sorted(found.get("requirements.txt", [])):
        return result("python -m compileall -q .", manifest)
    return None


def activate_platform(
    boot_report: Optional[Dict[str, Any]] = None,
    targets: Optional[List[str]] = None,
    auto_init: bool = True,
) -> Dict[str, Any]:
    """Issue every agent's preset directive as an actual run.

    - Blocked/needs_setup components get a real initialize run (the init script
      installs their declared dependencies; it fails honestly when it cannot).
    - Ready components get a manifest-derived command and report real output.
    All actions are durable runs; no simulated success or placeholder states.
    """
    init_automaton_states()
    transition("admin", "planning", None, task="activation", message="activating agents from preset directives", overlay="planning")

    statuses: Dict[str, Dict[str, Any]] = {}
    if boot_report:
        for entry in boot_report.get("subplatforms", []):
            statuses[entry.get("name")] = entry

    result: Dict[str, Any] = {
        "phase": "activation",
        "timestamp": _now(),
        "issued": [],
        "initialized": 0,
        "directed": 0,
        "skipped": 0,
        "errors": [],
    }

    for sp in subplatforms():
        name = sp.get("name")
        if targets and name not in targets:
            continue
        entry = statuses.get(name) or (component_readiness(name) or {})
        status = entry.get("status")

        if status == "ready":
            boot = _component_boot_command(sp)
            if not boot:
                result["skipped"] += 1
                result["errors"].append(f"{name}: no real activation command derivable from manifests")
                continue
            payload = {
                "task": f"ACTIVATION DIRECTIVE: {name} — {boot['command']}",
                "target": name,
                "command": boot["command"],
                "cwd": boot["cwd"],
                "source": "admin",
                "timeout": 300,
            }
            action = "directive"
        elif auto_init:
            payload = {
                "task": f"initialize {name}",
                "target": name,
                "source": "admin",
                "timeout": 900,
            }
            action = "init"
        else:
            result["skipped"] += 1
            continue

        try:
            run = run_task(payload)
            result["issued"].append({
                "agent": name,
                "action": action,
                "status": status,
                "command": payload.get("command"),
                "cwd": payload.get("cwd"),
                "run_id": run.get("id"),
            })
            if action == "init":
                result["initialized"] += 1
            else:
                result["directed"] += 1
        except Exception as e:
            result["errors"].append(f"{name}: {e}")

    result["summary"] = (
        f"Activation issued: {result['directed']} ready agents directed with real commands, "
        f"{result['initialized']} blocked agents sent to initialize, {result['skipped']} skipped."
    )
    transition("admin", "idle", None, task="activation", message=result["summary"])
    return result


# ============================================================================
# MCP-based agent-to-agent orchestration integration
# Chain: (planner) -> (router) -> (admin agent) -> (agent) -> (worker A, Worker B)
# ============================================================================

def register_mcp_servers_for_agents() -> Dict[str, Any]:
    """Register all platform capabilities under the single shared MCP server.

    Instead of one server per agent (which creates 15 separate processes
    and makes rate limiting/load management fragmented), all agents share
    one MCP server (mcp-platform) with centralized rate limiting.
    """
    registry = get_mcp_registry()
    results: Dict[str, Any] = {"registered": [], "errors": []}

    all_capabilities = {
        "tools": [],
        "data": [],
        "prompts": [],
    }

    for sp in subplatforms():
        name = sp.get("name")
        comp_name = sp.get("component")
        try:
            # Agent-specific tools on the shared server
            all_capabilities["tools"].extend([
                {
                    "name": f"{name}.discover",
                    "description": f"Discover capabilities of {name} agent",
                    "inputSchema": {"type": "object"},
                    "agent_id": name,
                    "rate_limit_per_minute": 60,
                },
                {
                    "name": f"{name}.execute",
                    "description": f"Execute a task on {name} agent",
                    "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}, "payload": {"type": "object"}}},
                    "agent_id": name,
                    "rate_limit_per_minute": 30,
                },
            ])
            # Agent-specific data resources on the shared server
            all_capabilities["data"].extend([
                {
                    "uri": f"agent://{name}/state",
                    "name": f"{name} state",
                    "description": f"Current automaton state of {name}",
                    "mimeType": "application/json",
                    "agent_id": name,
                },
                {
                    "uri": f"agent://{name}/reports",
                    "name": f"{name} reports",
                    "description": f"Reports emitted by {name}",
                    "mimeType": "application/json",
                    "agent_id": name,
                },
            ])
            # Agent-specific prompts on the shared server
            all_capabilities["prompts"].append({
                "name": f"{name}.system",
                "description": f"System prompt for {name} agent",
                "template": f"You are the {name} agent in the AutoTrading Ideal Platform. Your purpose: {{purpose}}. Report to admin on all actions.",
                "arguments": [{"name": "purpose", "type": "string"}],
                "agent_id": name,
            })
            results["registered"].append({
                "agent": name,
                "component": comp_name,
                "server_id": "mcp-platform",
                "tools": 2,
                "data": 2,
                "prompts": 1,
            })
        except Exception as e:
            results["errors"].append({"agent": name, "error": str(e)})

    # Register all capabilities under the single shared server
    registry.register_capabilities(all_capabilities)

    results["server_id"] = "mcp-platform"
    results["server_type"] = "shared"
    results["total_tools"] = len(all_capabilities["tools"])
    results["total_data"] = len(all_capabilities["data"])
    results["total_prompts"] = len(all_capabilities["prompts"])

    return results

def _watchdog_loop(interval_s: int = BOOTUP_INTERVAL_S) -> None:
    """Background watchdog: continuously runs observability checks so the admin
    agent stays in control and surfaces hiccups without human polling."""
    import time as _time
    while True:
        try:
            _time.sleep(interval_s)
            run_watchdog()
        except Exception:
            pass

def create_worker_for_agent(
    agent_id: str,
    job_id: str,
    purpose: str,
    mcp_tools: Optional[List[str]] = None,
    skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a worker for an agent to execute a specific job."""
    lifecycle = get_worker_lifecycle()
    try:
        worker = lifecycle.create_worker(
            agent_id=agent_id, job_id=job_id, purpose=purpose,
            mcp_tools=mcp_tools, skills=skills,
        )
        lifecycle.start_worker(worker.worker_id)
        return {
            "ok": True, "worker_id": worker.worker_id,
            "agent_id": agent_id, "job_id": job_id, "state": worker.state,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def complete_worker_for_agent(worker_id: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Complete a worker and kill it (ephemeral)."""
    lifecycle = get_worker_lifecycle()
    try:
        worker = lifecycle.complete_worker(worker_id, result)
        if not worker:
            return {"ok": False, "error": f"Worker {worker_id} not found"}
        return {"ok": True, "worker_id": worker.worker_id, "state": worker.state, "result": worker.result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def contribute_skill(
    agent_id: str,
    skill_id: str,
    name: str,
    description: str,
    tags: Optional[List[str]] = None,
    mcp_tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Agent contributes a skill to the shared skills catalogue."""
    catalogue = get_skill_catalogue()
    try:
        contribution = catalogue.contribute_skill(
            agent_id=agent_id, skill_id=skill_id, name=name,
            description=description, tags=tags, mcp_tools=mcp_tools,
        )
        return {"ok": True, "skill_id": contribution.skill_id, "name": contribution.name, "contributed_by": agent_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def use_skill(skill_id: str, agent_id: str) -> Dict[str, Any]:
    """Record that an agent used a skill from the catalogue."""
    catalogue = get_skill_catalogue()
    if not catalogue.use_skill(skill_id, agent_id):
        return {"ok": False, "error": f"Skill {skill_id} not found"}
    skill = catalogue.get_skill(skill_id)
    return {"ok": True, "skill_id": skill_id, "used_by": agent_id, "usage_count": skill.usage_count}


def suspend_worker_for_agent(worker_id: str, reason: str) -> Dict[str, Any]:
    """Suspend a worker and report suspension to agent then admin."""
    lifecycle = get_worker_lifecycle()
    try:
        worker = lifecycle.suspend_worker(worker_id, reason)
        if not worker:
            return {"ok": False, "error": f"Worker {worker_id} not found"}
        emit_report(
            agent=worker.agent_id, run=None, from_state="running", to_state="suspended",
            task=worker.job_id, errors={"suspension_reason": reason},
            message=f"Worker {worker_id} suspended: {reason}",
        )
        transition(worker.agent_id, "reporting", None, task=worker.job_id,
                   message=f"Worker {worker_id} suspended, reporting to admin")
        return {"ok": True, "worker_id": worker.worker_id, "agent_id": worker.agent_id, "reason": reason}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def execute_plan_via_chain(
    plan_id: str,
    goal: str,
    target_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a plan through the full chain:
    (planner) -> (router) -> (admin agent) -> (agent) -> (worker A, Worker B)
    """
    planner = Planner()
    router = get_router()
    lifecycle = get_worker_lifecycle()
    catalogue = get_skill_catalogue()

    plan = planner.create_plan(goal=goal, context={})
    plan.state = "approved"
    routed = router.route_plan(plan)

    execution_results: List[Dict[str, Any]] = []
    for route in routed.routes:
        agent_name = route.agent
        mcp_client = get_mcp_client(agent_name)
        available_tools = [t.name for t in mcp_client.discover_tools()]

        for worker_idx in range(route.worker_count):
            job_id = f"{route.route_id}-w{worker_idx}"
            worker_result = create_worker_for_agent(
                agent_id=agent_name, job_id=job_id, purpose=route.action,
                mcp_tools=route.mcp_tools, skills=route.skills,
            )
            skill_id = f"skill-{agent_name}-{route.action.replace(' ', '-')[:20]}"
            if not catalogue.get_skill(skill_id):
                contribute_skill(
                    agent_id=agent_name, skill_id=skill_id,
                    name=f"{agent_name}: {route.action}",
                    description=f"Capability contributed by {agent_name} for {route.action}",
                    tags=[agent_name], mcp_tools=route.mcp_tools,
                )
            catalogue.use_skill(skill_id, agent_name)
            execution_results.append({
                "route_id": route.route_id, "agent": agent_name,
                "worker_id": worker_result.get("worker_id"),
                "job_id": job_id, "state": worker_result.get("state"),
                "mcp_tools_available": available_tools,
                "skill_contributed": skill_id,
            })

    return {
        "plan_id": plan_id, "goal": goal,
        "chain": "planner -> router -> admin -> agent -> workers",
        "routed_plan": routed.to_dict(),
        "executions": execution_results,
        "total_workers": len(execution_results),
    }


def get_orchestration_status() -> Dict[str, Any]:
    """Get full orchestration status: MCP, workers, skills, router."""
    return {
        "chain": "planner -> router -> admin agent -> agent -> workers",
        "mcp": get_mcp_registry().summary(),
        "workers": get_worker_lifecycle().summary(),
        "skills": get_skill_catalogue().summary(),
        "router": get_router().summary(),
    }


AUTOMATON_STATES = {"idle", "waking", "running", "reporting", "blocked", "completed", "failed", "sleeping"}
ADMIN_OVERLAYS = {"planning", "erecting_paywall", "settling", "funding"}


def _summarize(steps: List[Dict[str, Any]]) -> str:
    parts = []
    for s in steps:
        parts.append(f"{s.get('subplatform')}: {s.get('status')}")
    return "; ".join(parts) if steps else "no steps"


def _safe_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    meta = {}
    for k in ("target", "cwd", "fanout", "timeout", "autorun", "mode"):
        if k in payload:
            meta[k] = payload[k]
    if "command" in payload:
        c = str(payload["command"])
        meta["command"] = c[:500]
    return meta


def _timeout_seconds(value: Any, default: int = 120) -> int:
    """Coerce a user/plan timeout to a positive number of seconds."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return default
    return seconds if seconds > 0 else default


TRADING_PLATFORMS = [
    "spider-ai--trading-bot",
    "vibetrading",
    "AutoHedge",
    "FinceptTerminal",
]
ENABLER_PLATFORMS = ["freellmapi", "Scrapling", "agent-desktop"]
PAYMENT_PLATFORMS = ["Pix-Payment-System"]


# --- Automaton helpers (additive) ---

def _ensure_agent_known(agent: str) -> None:
    try:
        cur = get_agent_state(agent)
        if not cur or cur.get("state") is None:
            update_agent_state(agent, "idle")
    except Exception:
        pass


def emit_report(agent: str, run: Optional[Dict[str, Any]], from_state: Optional[str], to_state: str,
                task: str = "", result: Any = None, errors: Any = None, step_index: Any = None,
                actions: Any = None, signals: Any = None, message: str = "") -> Dict[str, Any]:
    """Emit a report to admin (admin reports to operator/audit). Persisted and attached to run."""
    report = {
        "agent": agent,
        "reports_to": "operator_audit" if agent == "admin" else "admin",
        "run_id": (run or {}).get("id"),
        "step": step_index,
        "state": {"from": from_state, "to": to_state},
        "task": (task or (run or {}).get("task") or "")[:500],
        "actions": actions,
        "result": result,
        "errors": errors,
        "signals": signals or {},
        "message": message,
        "constitution": "musashaban-port/constitution.md",
    }
    try:
        saved = append_report(report)
    except Exception:
        saved = report
    # attach bounded report ref to run for easy access
    try:
        if run is not None:
            rlist = run.setdefault("reports", [])
            rlist.append({
                "id": saved.get("id"),
                "agent": agent,
                "reports_to": report["reports_to"],
                "state": report["state"],
                "step": step_index,
                "message": message,
                "timestamp": saved.get("timestamp"),
            })
            # cap in-memory/run-file report refs
            if len(rlist) > 200:
                del rlist[:-200]
            save_run(run)
    except Exception:
        pass
    return saved


def transition(agent: str, to_state: str, run: Optional[Dict[str, Any]] = None, step_index: Any = None,
               task: str = "", result: Any = None, errors: Any = None, actions: Any = None,
               signals: Any = None, message: str = "", overlay: Any = None) -> Dict[str, Any]:
    from_state = None
    try:
        cur = get_agent_state(agent)
        from_state = cur.get("state")
    except Exception:
        from_state = None
    fields = {}
    if run is not None:
        fields["run_id"] = run.get("id")
        fields["task"] = (task or run.get("task") or "")[:300]
    if overlay is not None or agent == "admin":
        # preserve/clear overlay explicitly if provided
        if overlay is not None:
            fields["overlay"] = overlay
    try:
        update_agent_state(agent, to_state, **fields)
    except Exception:
        pass
    emit_report(agent, run, from_state, to_state, task=task, result=result, errors=errors,
                step_index=step_index, actions=actions, signals=signals, message=message)
    return {"agent": agent, "from": from_state, "to": to_state}


def init_automaton_states() -> None:
    try:
        _ensure_agent_known("admin")
        for sp in subplatforms():
            _ensure_agent_known(sp.get("name"))
    except Exception:
        pass


def recover_interrupted_runs() -> Dict[str, Any]:
    """Reconcile state left behind by a previous process.

    If the orchestrator is stopped mid-run, its worker thread dies with it and
    the run stays ``queued``/``running`` forever (the UI then polls it
    indefinitely). Called once at server start: such runs are marked failed and
    automata stuck mid-transition are returned to idle.
    """
    recovered: List[str] = []
    for run in list_runs(limit=1000):
        previous = run.get("status")
        if previous not in ("queued", "running"):
            continue
        run["status"] = "failed"
        run["error"] = run.get("error") or f"Interrupted: orchestrator stopped while run was {previous}."
        run["result"] = run.get("result") or {"summary": "interrupted", "previous_status": previous}
        for step in run.get("steps") or []:
            if step.get("status") in ("queued", "running"):
                step["status"] = "failed"
                step["error"] = step.get("error") or "Interrupted by orchestrator restart."
        save_run(run)
        recovered.append(run.get("id"))

    reset_agents: List[str] = []
    try:
        states = load_agent_states()
        names = list((states.get("agents") or {}).keys()) + ["admin"]
        for name in names:
            entry = (states.get("admin") if name == "admin" else (states.get("agents") or {}).get(name)) or {}
            if entry.get("state") not in (None, "idle"):
                if name == "admin":
                    update_agent_state(name, "idle", overlay=None)
                else:
                    update_agent_state(name, "idle")
                reset_agents.append(name)
    except Exception:
        pass

    return {"recovered_runs": recovered, "reset_agents": reset_agents}


# --- Planning / initiative (preserves existing behavior, adds automaton semantics) ---

def _is_zero_funding_trading_goal(task: str) -> bool:
    t = (task or "").lower()
    trading = any(w in t for w in ["trade", "trading", "market", "arbitrage", "hedge", "signal", "bot"])
    zero = any(w in t for w in ["0 funding", "zero funding", "no funding", "without funding", "without capital", "zero capital", "no capital", "from 0", "from zero", "bootstrap", "paper", "demo", "testnet", "backtest", "simulate", "no money"])
    return trading and zero


def _is_initiative(task: str, payload: Dict[str, Any]) -> bool:
    if payload.get("autorun") or payload.get("mode") in ("initiative", "autorun", "plan"):
        return True
    t = (task or "").lower()
    if _is_zero_funding_trading_goal(task):
        return True
    # high-level goals without explicit command/target benefit from planning
    if payload.get("command") or payload.get("target"):
        return False
    goal_words = ["do ", "start", "build", "make ", "create", "setup", "set up", "achieve", "run platform", "launch", "bootstrap", "how to", "plan", "initiative", "from 0", "from zero"]
    return any(w in t for w in goal_words) and len(t.split()) >= 3


# --- Funding state & spend authorizations (constitution bites) ---------------
# "Earn your existence": the platform default is 0-funding — money movement
# is denied, every authorization is recorded durably, and funding state
# (unfunded / float_funded / revenue_funded) is measurable.

FUNDING_LEDGER = STATE_DIR / "funding.jsonl"
_funding_lock = threading.Lock()


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _funding_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def funding_state() -> Dict[str, Any]:
    """The platform funding state from the durable funding ledger."""
    reserve_usd = 0.0
    revenue_usd = 0.0
    spend_usd = 0.0
    if FUNDING_LEDGER.exists():
        try:
            lines = FUNDING_LEDGER.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") != "recorded":
                continue
            kind = rec.get("kind")
            try:
                amt = float(rec.get("amount_usd") or 0.0)
            except Exception:
                continue
            if kind == "reserve":
                reserve_usd += amt
            elif kind == "revenue":
                revenue_usd += amt
            elif kind in ("spend", "withdrawal"):
                spend_usd += amt
    if revenue_usd > 0:
        state = "revenue_funded"
    elif reserve_usd > 0:
        state = "float_funded"
    else:
        state = "unfunded"
    return {
        "state": state,
        "reserve_usd": round(reserve_usd, 2),
        "revenue_usd": round(revenue_usd, 2),
        "spend_usd": round(spend_usd, 2),
        "net_realized_usd": round(revenue_usd - spend_usd, 2),
        "note": "unfunded: 0-funding posture; float_funded: float exists; revenue_funded: net revenue recorded",
    }


def record_funding(
    kind: str,
    amount_usd: float,
    currency: str = "USD",
    source: Optional[str] = None,
    purpose: Optional[str] = None,
    run_id: Optional[str] = None,
    evidence: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a durable funding record and report it to admin.

    Kinds: reserve, revenue, spend, withdrawal. Every paywall/sale/settlement/
    funding action must be recorded here so no side effect is hidden.
    """
    kind = (kind or "").strip().lower()
    if kind not in ("reserve", "revenue", "spend", "withdrawal"):
        raise ValueError(f"unsupported funding kind {kind!r} (reserve|revenue|spend|withdrawal)")
    amount = float(amount_usd or 0.0)
    if amount < 0:
        raise ValueError("amount_usd must be >= 0")
    rec = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": _now(),
        "kind": kind,
        "amount_usd": amount,
        "currency": currency,
        "source": source,
        "purpose": purpose or f"{kind} via funding ledger",
        "run_id": run_id,
        "evidence": evidence,
        "recorded_by": "admin",
        "status": "recorded",
    }
    _append_jsonl(FUNDING_LEDGER, rec)
    transition(
        "admin", "reporting", None, task="record_funding",
        result=rec, message=f"funding recorded: {kind} ${amount} {source or ''}".strip(),
    )
    # Auto-settle surplus to autoSettleTo when state + config + authorization
    # allow it (fail-closed: nothing moves without all three).
    try:
        from orchestrator.payment import settle as _payment_settle
        _payment_settle.maybe_settle()
    except Exception:
        pass
    return rec


def authorize_spend(
    agent: str,
    purpose: str,
    amount_usd: float = 0,
    approved_by: str = "operator",
    reason: Optional[str] = None,
    target: Optional[str] = None,
    scope: Optional[str] = None,
    status: str = "active",
    max_uses: Optional[int] = None,
    expires: Optional[str] = None,
) -> Dict[str, Any]:
    """Record an operator authorization for money movement for one agent.

    Returns the record including ``spend_auth_id``; dispatch passes it as
    ``spend_auth`` to ``policy.evaluate``. 0-funding default stays: a
    financial command is denied unless the token is live.
    """
    agent = (agent or "").strip()
    purpose = (purpose or "").strip()
    if not agent:
        raise ValueError("authorize_spend requires an agent")
    if not purpose:
        raise ValueError("authorize_spend requires a purpose")
    if status not in ("active", "revoked", "expired"):
        raise ValueError("status must be active|revoked|expired")
    rec = {
        "spend_auth_id": "sa-" + uuid.uuid4().hex[:12],
        "timestamp": _now(),
        "agent": agent,
        "target": target,
        "amount_usd": float(amount_usd or 0.0),
        "currency": "USD",
        "purpose": purpose,
        "approved_by": approved_by,
        "reason": reason,
        "scope": scope,
        "max_uses": max_uses,
        "status": status,
        "expires": expires,
    }
    _append_jsonl(policy.SPEND_AUTH_PATH, rec)
    # Visibility on the automaton state.
    st = get_agent_state(agent)
    if st.get("state") != "failed":
        update_agent_state(agent, st.get("state", "idle"), last_spend_authorization=rec["spend_auth_id"])
    transition(
        "admin", "reporting", None, task="authorize_spend",
        result={
            "spend_auth_id": rec["spend_auth_id"],
            "agent": agent,
            "amount_usd": rec["amount_usd"],
            "purpose": purpose,
            "status": status,
        },
        message=f"spend authorization issued for {agent}: {purpose} (approved_by={approved_by})",
    )
    return rec


def revoke_spend_authorization(spend_auth_id: str) -> Optional[Dict[str, Any]]:
    """Revoke (mark inactive) a recorded spend authorization."""
    target = None
    with _funding_lock:
        try:
            lines = policy.SPEND_AUTH_PATH.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        out = []
        for line in lines:
            if line.strip():
                rec = json.loads(line)
                if rec.get("spend_auth_id") == spend_auth_id:
                    target = rec
                    rec["status"] = "revoked"
                out.append(json.dumps(rec, ensure_ascii=False))
        policy.SPEND_AUTH_PATH.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    if target:
        transition(
            "admin", "reporting", None, task="revoke_spend_authorization",
            result={"spend_auth_id": spend_auth_id},
            message=f"spend authorization revoked: {spend_auth_id}",
        )
    return target


def spend_authorizations() -> List[Dict[str, Any]]:
    return policy.load_spend_authorizations()


def build_plan(task: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create an executable plan (list of steps) for an initiative/goal."""
    t = (task or "").lower()
    steps: List[Dict[str, Any]] = []

    def add(target, title, command, rationale=""):
        sp = get_subplatform(target)
        if not sp:
            return
        steps.append({
            "target": sp.get("name"),
            "subplatform": sp.get("name"),
            "component": sp.get("component"),
            "title": title,
            "command": command,
            "rationale": rationale,
        })

    if _is_zero_funding_trading_goal(task):
        # Primary initiative: bootstrap trading with zero capital (fail-closed)
        steps.append({
            "target": "admin",
            "subplatform": "admin",
            "component": "platform",
            "title": "Primary directive: zero-funding trading bootstrap",
            "command": None,
            "rationale": "Establish initiative and constraints (no capital, use paper/demo/backtest/free data).",
            "directive": {
                "goal": task,
                "constraints": [
                    "0 funding / no capital assumed (fail closed on paid actions)",
                    "no paid accounts/keys assumed; no real-capital trades without verified funds and confirmation",
                    "use simulation, paper trading, backtesting, demo/testnet, dry-run, and free data/LLM tiers where available",
                ],
                "strategy": [
                    "Discover zero-capital modes in each trading subplatform (paper/demo/testnet/backtest/simulate/dry-run/sandbox).",
                    "Identify free data and analysis enablers (freellmapi, Scrapling).",
                    "Produce an actionable bootstrap plan with concrete next commands per component.",
                    "Admin: erect paywalls / settle / fund only from realized, authorized resources via configured rails (e.g. Pix-Payment-System); fail closed otherwise.",
                ],
                "governance": {
                    "constitution": "musashaban-port/constitution.md",
                    "automaton_model": "musashaban-port/automaton-model.md",
                    "paywall_funding": "musashaban-port/paywall-funding.md",
                    "reporting": "every eagent reports to admin; admin reports to operator/audit",
                },
            },
        })
        for name in TRADING_PLATFORMS:
            add(name, f"Survey {name} for zero-funding modes",
                'Get-ChildItem -Force | Select-Object Name,Mode; ""; if (Test-Path README.md) { "--- README ---"; Get-Content README.md -TotalCount 120 }; ""; "--- zero-funding signals ---"; Get-ChildItem -Recurse -File -Include *.md,*.py,*.ts,*.js,*.env*,*.toml,*.yaml,*.yml -ErrorAction SilentlyContinue | Select-String -Pattern "paper|demo|testnet|backtest|simulat|sandbox|dry.?run|paper.?trad" -List | Select-Object -First 12 | ForEach-Object { $_.Path + ":" + $_.LineNumber + " " + $_.Line.Trim() }',
                "Find paper/demo/testnet/backtest/simulation capabilities and entrypoints.")
        for name in ENABLER_PLATFORMS:
            add(name, f"Survey {name} as zero-funding enabler",
                'if (Test-Path README.md) { Get-Content README.md -TotalCount 80 } else { Get-ChildItem -Force | Select-Object -First 20 Name,Mode }',
                "Identify free/no-capital enablement (free LLM/data/automation).")
        steps.append({
            "target": "admin",
            "subplatform": "admin",
            "component": "platform",
            "title": "Synthesize bootstrap directive",
            "command": None,
            "rationale": "Combine findings into an executable next-step plan.",
            "synthesize": True,
        })
        return steps

    # Generic initiative: survey relevant components and synthesize
    targets_sp = route_task(task, target=payload.get("target"))
    if not targets_sp:
        # platform-wide light survey of key areas
        targets_sp = [get_subplatform(n) for n in (TRADING_PLATFORMS[:2] + ["freellmapi", "Pix-Payment-System"])]
        targets_sp = [s for s in targets_sp if s]
    steps.append({
        "target": "admin",
        "subplatform": "admin",
        "component": "platform",
        "title": "Primary directive",
        "command": None,
        "rationale": "Capture goal and plan discovery.",
        "directive": {
            "goal": task,
            "strategy": ["Discover relevant subplatform capabilities", "Execute discovery actions", "Synthesize findings and next steps"],
            "governance": {
                "constitution": "musashaban-port/constitution.md",
                "automaton_model": "musashaban-port/automaton-model.md",
                "zero_funding": "fail closed; earn/simulate before spend",
                "reporting": "every eagent reports to admin",
            },
        },
    })
    for sp in targets_sp[:6]:
        name = sp.get("name")
        add(name, f"Discover {name} capabilities",
            'Get-ChildItem -Force | Select-Object Name,Mode; ""; if (Test-Path README.md) { "--- README ---"; Get-Content README.md -TotalCount 100 }',
            f"Inspect {name} for capabilities relevant to: {task}")
    steps.append({
        "target": "admin",
        "subplatform": "admin",
        "component": "platform",
        "title": "Synthesize findings",
        "command": None,
        "rationale": "Aggregate results into actionable next steps.",
        "synthesize": True,
    })
    return steps


# --- Execution (preserves existing dispatch/streaming behavior, adds states/reports) ---

def _spec_validation(spec: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Validate one spec against the live constitution hash.

    Used at dispatch: an agent's spec must be present, current and structurally
    complete; the constitution is the shared contract every spec must bind to.
    Agents that are registry entries without on-disk specs (replicas) are
    validated without the registry-membership requirement.
    """
    registry_eagents = {sp.get("name"): sp.get("eagent") for sp in subplatforms()}
    return validate_spec(
        spec or {}, constitution_sha256(), registry_eagents,
        require_registry=name in registry_eagents,
    )


def dispatch_to_eagent(
    sp: Dict[str, Any],
    task: str,
    payload: Dict[str, Any],
    run: Dict[str, Any],
    step_index: int,
) -> Dict[str, Any]:
    """Execute a task step for one eagent/subplatform, updating run live."""
    name = sp.get("name")
    comp_dir = component_path(sp)
    step: Dict[str, Any] = {
        "eagent": sp.get("eagent"),
        "subplatform": name,
        "component": sp.get("component"),
        "component_path": str(comp_dir),
        "status": "running",
        "actions": [],
        "output": "",
    }
    run["steps"][step_index] = step
    save_run(run)
    transition(name, "running", run, step_index, task=task, message="eagent step running")

    command = payload.get("command")
    cwd = payload.get("cwd")
    if cwd is None:
        cwd = str(comp_dir) if comp_dir.exists() else str(PLATFORM_ROOT)

    # --- agent validity + readiness gate ---------------------------------
    spec = resolve_spec(name)
    guard = guardrails_for(name)
    spec_errors = _spec_validation(spec, name).get("errors", []) if spec else ["missing"]
    readiness = component_readiness(name) or {}
    is_init_task = bool(
        re.search(r"\b(init|initialize|initialise|setup|set[- ]?up|install|bootstrap|repair)\b", task or "", re.IGNORECASE)
    )
    init_command = None

    if not spec:
        step["status"] = "blocked"
        step["error"] = "agent spec missing"
        step["output"] = {
            "message": f"eagent {name} has no valid spec; refusing to dispatch.",
            "fix": "node scripts/generate-agents.js (or npm run init)",
        }
        step["actions"].append({"type": "policy", "info": "blocked: missing agent spec"})
    elif spec_errors:
        step["status"] = "blocked"
        step["error"] = "agent spec fails constitution/soul validation"
        step["output"] = {
            "message": f"eagent {name} has a spec that fails constitution/soul validation; refusing to dispatch.",
            "errors": spec_errors,
            "constitution": constitution_sha256(),
            "fix": "re-run scripts/generate-agents.js (or npm run init), or update the constitution first",
        }
        step["actions"].append({"type": "policy", "info": "blocked: constitution/soul validation failed"})
    elif not command and is_init_task:
        node_bin = os.environ.get("PLATFORM_NODE_BIN") or "node"
        init_command = f'"{node_bin}" scripts/init-platform.js --component {sp.get("component")}'
        command = init_command
        cwd = str(PLATFORM_ROOT)
    elif not command and readiness.get("status") != "ready":
        step["status"] = "blocked"
        step["error"] = f"component not ready: {readiness.get('status')}"
        step["output"] = {
            "message": f"{name} is not initialized; refusing to report success.",
            "status": readiness.get("status"),
            "errors": readiness.get("errors", []),
            "next_steps": readiness.get("next_steps", []),
            "items": [
                {"path": u.get("path"), "languages": u.get("languages"), "dependencies": u.get("dependencies")}
                for u in readiness.get("units", [])
            ],
        }
        step["actions"].append({"type": "readiness", "info": "blocked before dispatch"})
    elif not command:
        step["status"] = "blocked"
        step["error"] = "no command supplied"
        step["output"] = {
            "message": f"{name} is ready, but the task supplied no command to execute.",
            "hint": "pass \"command\" to run, or ask admin to initialize/setup the component.",
            "readiness": {"status": readiness.get("status"), "toolchain": readiness.get("toolchain")},
        }
        step["actions"].append({"type": "dispatch", "info": "no executable step; nothing ran"})
    else:
        scope = None if init_command else (component_scope(name) or comp_dir)
        decision = policy.evaluate(
            command,
            Path(cwd),
            agent="admin" if init_command else name,
            source=payload.get("source") or "admin",
            scope=scope,
            authorized_spend=bool(payload.get("authorized_spend")),
            guardrails=guard,
            spend_auth=payload.get("spend_auth"),
            funding_state=funding_state().get("state"),
        )
        step["actions"].append({"type": "policy", "decision": decision.as_dict()})
        if not decision.allow:
            step["status"] = "blocked"
            step["error"] = decision.reason_code
            step["output"] = {
                "message": decision.message,
                "rule": decision.rule,
                "command": command,
            }
        else:
            def on_update(res):
                step["actions"] = [{"type": "shell", "command": command, "result": res}]
                step["output"] = (res.get("stdout") or "") + (
                    ("\n" + res.get("stderr")) if res.get("stderr") else ""
                )
                step["status"] = "running" if not res.get("done") else ("completed" if res.get("ok") else "failed")
                run["steps"][step_index] = step
                save_run(run)

            result = run_shell_streaming(
                command, cwd=cwd, timeout=_timeout_seconds(payload.get("timeout")), on_update=on_update
            )
            step["actions"] = [{"type": "shell", "command": command, "result": result}]
            step["output"] = (result.get("stdout") or "") + (
                ("\n" + result.get("stderr")) if result.get("stderr") else ""
            )
            step["status"] = "completed" if result.get("ok") else "failed"

            # An install is only complete when the component is actually ready.
            if init_command and result.get("ok"):
                after = component_readiness(name) or {}
                step["readiness_after"] = {
                    "status": after.get("status"),
                    "errors": after.get("errors", []),
                    "next_steps": after.get("next_steps", []),
                }
                if after.get("status") != "ready":
                    step["status"] = "failed"
                    step["error"] = f"install finished but component is {after.get('status')}"

    run["steps"][step_index] = step
    save_run(run)
    final = step.get("status")
    if final == "completed":
        transition(name, "reporting", run, step_index, task=task, result="completed", message="eagent reporting to admin")
        transition(name, "completed", run, step_index, task=task, result="completed", message="eagent step completed")
        transition(name, "idle", run, step_index, task=task, message="eagent idle")
    elif final == "blocked":
        transition(name, "reporting", run, step_index, task=task, errors=step.get("error"), message="eagent reporting blocker to admin")
        transition(name, "blocked", run, step_index, task=task, errors=step.get("error"), message="eagent blocked")
        transition(name, "idle", run, step_index, task=task, message="eagent idle")
    else:
        transition(name, "reporting", run, step_index, task=task, errors=final, message="eagent reporting failure to admin")
        transition(name, "failed", run, step_index, task=task, errors=final, message="eagent step failed")
        transition(name, "idle", run, step_index, task=task, message="eagent idle")
    return step


def _execute_run(run_id: str, targets_sp: List[Dict[str, Any]], task: str, payload: Dict[str, Any]):
    run = get_run(run_id)
    if not run:
        return
    try:
        init_automaton_states()
        transition("admin", "running", run, task=task, message="admin supervising run", overlay="dispatch")
        run["status"] = "running"
        run["steps"] = [{
            "eagent": sp.get("eagent"),
            "subplatform": sp.get("name"),
            "component": sp.get("component"),
            "status": "queued",
            "output": "",
            "actions": [],
        } for sp in targets_sp]
        for sp in targets_sp:
            transition(sp.get("name"), "waking", run, task=task, message="queued for dispatch")
        save_run(run)

        overall_ok = True
        for i, sp in enumerate(targets_sp):
            run = get_run(run_id) or run
            run["steps"][i]["status"] = "running"
            save_run(run)
            try:
                step = dispatch_to_eagent(sp, task, payload, run, i)
            except Exception as e:
                run = get_run(run_id) or run
                name = sp.get("name")
                run["steps"][i] = {
                    "eagent": sp.get("eagent"),
                    "subplatform": name,
                    "status": "failed",
                    "error": str(e),
                    "output": str(e),
                    "actions": [],
                }
                step = run["steps"][i]
                save_run(run)
                transition(name, "failed", run, i, task=task, errors=str(e), message="eagent step exception")
                transition(name, "idle", run, i, task=task, message="eagent idle")
            if step.get("status") != "completed":
                overall_ok = False
            run = get_run(run_id) or run

        run = get_run(run_id) or run
        run["status"] = "completed" if overall_ok else "failed"
        run["result"] = {"summary": _summarize(run.get("steps", [])), "steps": [s.get("subplatform") for s in run.get("steps", [])]}
        transition("admin", "reporting", run, task=task, result=run["result"], message="admin aggregating run reports")
        transition("admin", "completed" if overall_ok else "failed", run, task=task, result=run["result"], message="run finalized", overlay=None)
        transition("admin", "idle", run, task=task, message="admin idle", overlay=None)
        save_run(run)
    except Exception as e:
        run = get_run(run_id) or run
        run["status"] = "failed"
        run["error"] = str(e)
        transition("admin", "failed", run, task=task, errors=str(e), message="run failed", overlay=None)
        transition("admin", "idle", run, task=task, message="admin idle", overlay=None)
        save_run(run)


def _exec_plan_step(run_id: str, idx: int, step_def: Dict[str, Any], task: str, payload: Dict[str, Any]):
    run = get_run(run_id)
    if not run:
        return
    step = run["steps"][idx]
    step["status"] = "running"
    step["title"] = step_def.get("title")
    step["rationale"] = step_def.get("rationale")
    run["steps"][idx] = step
    save_run(run)

    name = step_def.get("target") or step_def.get("subplatform") or "admin"
    if name == "admin" or step_def.get("directive"):
        # admin automaton step
        transition("admin", "running", run, idx, task=task, message=step_def.get("title") or "admin step", overlay="planning")
        directive = step_def.get("directive")
        if directive:
            out = {"type": "directive", "title": step_def.get("title"), "directive": directive, "message": "Primary initiative established. Executing discovery and coordination workflow (automaton model, reporting to admin, 0-funding fail-closed)."}
        elif step_def.get("synthesize"):
            transition("admin", "running", run, idx, task=task, message="synthesizing reports", overlay="planning")
            findings = []
            for s in run.get("steps", []):
                if s is step:
                    continue
                findings.append({
                    "subplatform": s.get("subplatform"),
                    "title": s.get("title"),
                    "status": s.get("status"),
                    "excerpt": (str(s.get("output", ""))[:1200]),
                })
            out = {
                "type": "synthesis",
                "title": step_def.get("title"),
                "goal": task,
                "findings_count": len(findings),
                "findings": findings,
                "reports_collected": len((run or {}).get("reports", [])),
                "next_steps": [
                    "Open each trading subplatform README/entrypoint identified above and run its paper/demo/backtest/simulate mode (no capital).",
                    "Use freellmapi as the free LLM/analysis layer where supported; use Scrapling for free/public data collection.",
                    "Configure env from .env.example (no paid keys required for paper/sim modes where available) and run the component's demo/backtest command.",
                    "Iterate: pick the most viable zero-funding path surfaced (backtest/simulation/paper) and execute it via a dispatched command.",
                ],
                "admin_responsibilities": {
                    "erect_paywalls": "When honest value/offering exists, admin may erect paywalls and verify/record sales (musashaban-port/paywall-funding.md).",
                    "settle": "Admin routes/authorizes settlement via configured rails (e.g. Pix-Payment-System African/PIX variants) only with valid credentials/float/policy; fail closed otherwise.",
                    "fund": "Admin allocates only realized, authorized surplus (reserve first); in 0-funding mode do not allocate nonexistent capital.",
                    "reporting": "All actions/reports aggregated by admin; audit trail in runs/reports.",
                },
                "zero_funding_gate": "Fail closed: no real-capital trades, no paid rail enablement, no spend without verified funds/credentials/authorization.",
            }
            if _is_zero_funding_trading_goal(task):
                out["bootstrap_directive"] = {
                    "mode": "zero-funding",
                    "priority_order": ["backtest/simulation (no live funds)", "paper/demo trading", "testnet (if no real capital)", "free data + free LLM analysis"],
                    "agents": TRADING_PLATFORMS + ENABLER_PLATFORMS,
                    "paywall_settlement_funding": "admin-controlled, realized-revenue-before-spend, Pix-Payment-System rail when configured/authorized",
                }
        else:
            out = {"type": "admin", "message": step_def.get("title") or "Admin step", "detail": step_def.get("rationale")}
        step["status"] = "completed"
        step["actions"] = [{"type": "admin", "result": "ok", "automaton": "admin"}]
        step["output"] = out
        run["steps"][idx] = step
        save_run(run)
        transition("admin", "reporting", run, idx, task=task, result=out.get("type"), message="admin reporting step outcome")
        transition("admin", "completed", run, idx, task=task, result=out.get("type"), message="admin step completed", overlay=None)
        return

    sp = get_subplatform(name)
    if not sp:
        step["status"] = "failed"
        step["output"] = f"Unknown subplatform: {name}"
        run["steps"][idx] = step
        save_run(run)
        transition("admin", "blocked", run, idx, task=task, errors=step["output"], message="unknown subplatform in plan")
        return

    comp_dir = component_path(sp)
    cwd = str(comp_dir) if comp_dir.exists() else str(PLATFORM_ROOT)
    command = step_def.get("command")
    transition(name, "waking", run, idx, task=task, message="plan step waking")
    transition(name, "running", run, idx, task=task, message="plan step running")
    if command:
        def on_update(res):
            step["actions"] = [{"type": "shell", "command": command, "result": res}]
            step["output"] = (res.get("stdout") or "") + (("\n" + res.get("stderr")) if res.get("stderr") else "")
            step["status"] = "running" if not res.get("done") else ("completed" if res.get("ok") else "failed")
            run["steps"][idx] = step
            save_run(run)
        result = run_shell_streaming(command, cwd=cwd, timeout=_timeout_seconds(step_def.get("timeout") or payload.get("timeout")), on_update=on_update)
        step["actions"] = [{"type": "shell", "command": command, "result": result}]
        step["output"] = (result.get("stdout") or "") + (("\n" + result.get("stderr")) if result.get("stderr") else "")
        step["status"] = "completed" if result.get("ok") else "failed"
    else:
        exists = comp_dir.exists()
        listing = list_dir(str(comp_dir)) if exists else {"ok": False, "error": "component path missing"}
        step["actions"] = [{"type": "dispatch", "info": "Routed to eagent."}]
        step["status"] = "completed"
        step["output"] = {"message": f"Dispatched to {name}.", "component_exists": exists, "component_entries": (listing.get("entries") or [])[:30], "reports_to": "admin"}
    run["steps"][idx] = step
    save_run(run)
    final = step.get("status")
    if final == "completed":
        transition(name, "reporting", run, idx, task=task, result="completed", message="reporting to admin")
        transition(name, "completed", run, idx, task=task, result="completed", message="plan step completed")
        transition(name, "idle", run, idx, task=task, message="idle")
    else:
        transition(name, "reporting", run, idx, task=task, errors=final, message="reporting failure to admin")
        transition(name, "failed", run, idx, task=task, errors=final, message="plan step failed")
        transition(name, "idle", run, idx, task=task, message="idle")


def _execute_initiative(run_id: str, task: str, payload: Dict[str, Any]):
    run = get_run(run_id)
    if not run:
        return
    try:
        init_automaton_states()
        transition("admin", "waking", run, task=task, message="initiative waking")
        transition("admin", "running", run, task=task, message="planning initiative", overlay="planning")
        plan = build_plan(task, payload)
        run = get_run(run_id) or run
        run["status"] = "running"
        run["plan"] = [{"title": s.get("title"), "subplatform": s.get("subplatform"), "rationale": s.get("rationale")} for s in plan]
        run["steps"] = [{
            "subplatform": s.get("subplatform"),
            "component": s.get("component"),
            "title": s.get("title"),
            "status": "queued",
            "output": "",
            "actions": [],
        } for s in plan]
        save_run(run)
        emit_report("admin", run, "running", "running", task=task, actions=[{"type": "plan", "steps": len(plan)}], message="initiative plan created", signals={"mode": "initiative", "zero_funding": _is_zero_funding_trading_goal(task)})

        overall_ok = True
        for i, sdef in enumerate(plan):
            try:
                _exec_plan_step(run_id, i, sdef, task, payload)
            except Exception as e:
                run = get_run(run_id) or run
                run["steps"][i]["status"] = "failed"
                run["steps"][i]["error"] = str(e)
                run["steps"][i]["output"] = str(e)
                save_run(run)
                transition("admin", "failed", run, i, task=task, errors=str(e), message="plan step exception")
            run = get_run(run_id) or run
            if run["steps"][i].get("status") != "completed":
                overall_ok = False

        run = get_run(run_id) or run
        run["status"] = "completed" if overall_ok else "failed"
        run["result"] = {
            "summary": _summarize(run.get("steps", [])),
            "mode": "initiative",
            "steps_completed": sum(1 for s in run.get("steps", []) if s.get("status") == "completed"),
            "steps_total": len(run.get("steps", [])),
            "reports_collected": len(run.get("reports", [])),
            "workflow": (run.get("meta") or {}).get("workflow"),
            "automaton": {"model": "musashaban-port/automaton-model.md", "reporting": "eagent->admin->operator_audit"},
        }
        transition("admin", "reporting", run, task=task, result=run["result"], message="initiative reporting")
        transition("admin", "completed" if overall_ok else "failed", run, task=task, result=run["result"], message="initiative finalized", overlay=None)
        transition("admin", "idle", run, task=task, message="admin idle", overlay=None)
        save_run(run)
    except Exception as e:
        run = get_run(run_id) or run
        run["status"] = "failed"
        run["error"] = str(e)
        transition("admin", "failed", run, task=task, errors=str(e), message="initiative failed", overlay=None)
        transition("admin", "idle", run, task=task, message="admin idle", overlay=None)
        save_run(run)


def run_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a task: returns immediately with a run that executes in background."""
    init_automaton_states()
    task = (payload.get("task") or "").strip()
    if not task and not payload.get("command"):
        raise ValueError("Provide 'task' and/or 'command'.")

    # Autonomous initiative / primary directive mode: plan and execute workflow
    if task and _is_initiative(task, payload):
        plan = build_plan(task, payload)
        targets = [s.get("subplatform") or s.get("target") or "admin" for s in plan]
        # de-dupe for header while keeping plan order in steps
        seen = set()
        uniq = []
        for tname in targets:
            if tname not in seen:
                seen.add(tname); uniq.append(tname)
        pick = workflows_mod.select_workflow(task)
        meta = {
            "payload": _safe_meta(payload),
            "mode": "initiative",
            "automaton": True,
            "zero_funding": _is_zero_funding_trading_goal(task),
            "workflow": {
                "id": pick["workflow"].id,
                "slug": pick["workflow"].slug,
                "name": pick["workflow"].name,
                "matched": pick["matched"],
                "fallback": pick["fallback"],
            },
        }
        run = new_run(task, uniq, meta=meta)
        run["status"] = "queued"
        run["mode"] = "initiative"
        run.setdefault("reports", [])
        save_run(run)
        th = threading.Thread(target=_execute_initiative, args=(run["id"], task, payload), daemon=True)
        th.start()
        return run

    target = payload.get("target")
    targets_sp = route_task(task or payload.get("command", ""), target=target)

    if not targets_sp and target:
        run = new_run(task or "", [], meta={"payload": _safe_meta(payload), "automaton": True})
        run["status"] = "failed"
        run["error"] = f"Unknown target/subplatform: {target}"
        transition("admin", "blocked", run, task=task, errors=run["error"], message="unknown target")
        save_run(run)
        return run

    if not targets_sp:
        run = new_run(task or "", ["admin"], meta={"payload": _safe_meta(payload), "automaton": True})
        run["status"] = "completed"
        run.setdefault("reports", [])
        result = {
            "message": "No specific eagent matched; task recorded at admin level.",
            "hint": "Specify 'target' with a subplatform name/component to dispatch, or submit as an initiative/autorun goal.",
            "available_subplatforms": [s.get("name") for s in subplatforms()],
            "automaton": {"reporting": "admin recorded; eagents report to admin when dispatched", "zero_funding": "fail-closed by default"},
        }
        run["steps"] = [{
            "eagent": "admin",
            "subplatform": "platform",
            "status": "completed",
            "output": result,
        }]
        run["result"] = result
        transition("admin", "reporting", run, task=task, result="recorded", message="admin-level record (no eagent matched)")
        transition("admin", "completed", run, task=task, result="recorded", message="admin-level task completed")
        transition("admin", "idle", run, task=task, message="admin idle")
        save_run(run)
        return run

    target_names = [sp.get("name") for sp in targets_sp]
    run = new_run(task or payload.get("command", ""), target_names, meta={"payload": _safe_meta(payload), "automaton": True})
    run["status"] = "queued"
    run.setdefault("reports", [])
    save_run(run)

    t = threading.Thread(target=_execute_run, args=(run["id"], targets_sp, task, payload), daemon=True)
    t.start()
    return run



# ============================================================================
# Profit-Motive Directives (admin issues earning-oriented goals to each agent)
# ============================================================================

PIPELINE_AGENTS = {
    "agent-desktop": {
        "role": "pipeline",
        "purpose": "Interface and surface layer between agents, humans, and desktop applications",
        "serves": ["all_agents", "humans"],
        "exposes": [
            "OS accessibility-tree driven task execution for any agent",
            "Desktop app operation (macOS, Windows, Linux) as a shared capability",
            "Human-facing interface: surfaces other agents' capabilities via desktop automation",
            "Visual debugging and observation for agent actions",
        ],
        "internal_utility": (
            "agent-desktop is the platform's human-agent interface. Other agents "
            "delegate desktop/OS interactions to it. It surfaces each agent's "
            "capabilities so humans can observe, approve, or intervene."
        ),
        "kpi": "tasks_facilitated, agents_served, human_interventions, uptime",
    },
    "agentic-inbox": {
        "role": "hybrid",
        "purpose": "Dual role: external email client AND internal inter-agent communications bus",
        "serves": ["all_agents", "external_clients"],
        "exposes": [
            "External email routing, triage, and response drafting for clients",
            "Internal agent-to-agent messaging bus (comms utility)",
            "Routes notifications, alerts, and reports between agents",
            "Human-readable mail interface for agent-generated communications",
        ],
        "internal_utility": (
            "agentic-inbox is the platform's internal comms utility. Agents use it "
            "to send messages to each other, route alerts, and share reports."
        ),
        "kpi": "messages_routed_internal, messages_routed_external, triage_success_rate, delivery_latency",
    },
    "freellmapi": {
        "role": "pipeline",
        "purpose": "Shared inference layer: free LLM access for every agent in the platform",
        "serves": ["all_agents"],
        "exposes": [
            "Unified OpenAI-compatible inference endpoint for all agents",
            "87 free LLM providers / 1,313 free model endpoints",
            "Inference routing, load balancing, and failover across providers",
            "Shared inference budget management across agents",
        ],
        "internal_utility": (
            "freellmapi is the platform's shared inference layer. Every agent that "
            "needs LLM capability routes through freellmapi instead of provisioning "
            "its own provider. This prevents fragmentation and keeps inference costs "
            "at zero (free tiers only)."
        ),
        "kpi": "inference_requests_served, agents_using_endpoint, avg_latency, provider_failover_count",
    },
    "Open-LLM-VTuber": {
        "role": "pipeline",
        "purpose": "Shared voice/avatar interface utility for agents needing embodied presence",
        "serves": ["all_agents"],
        "exposes": [
            "Desktop voice/avatar (VTuber) capability for any agent",
            "Shared streaming interface for agent-human interaction",
            "Embodied presence for agents that need a face/voice",
        ],
        "internal_utility": (
            "Open-LLM-VTuber provides shared voice and avatar capabilities. Agents "
            "that need to present themselves to humans use this instead of building "
            "their own avatar stack."
        ),
        "kpi": "streaming_sessions, agents_using_avatar, viewer_hours, uptime",
    },
}

BUSINESS_AGENTS = {
    "AutoHedge": {
        "role": "business",
        "purpose": "Earn from spread capture and arbitrage (zero-funding, paper/demo/testnet only)",
        "strategy": [
            "Scan for price discrepancies across venues using free data feeds",
            "Execute hedge trades only in paper/demo/testnet/backtest/simulation modes",
            "Report profitable setups to admin for capital allocation decision",
            "Never trade live capital without verified funds and operator authorization",
        ],
        "kpi": "paper_profit_captured, setups_identified, win_rate",
    },
    "claude-ads": {
        "role": "business",
        "purpose": "Profit from AI-optimized ad campaign creation and management",
        "strategy": [
            "Generate ad copy and targeting recommendations using free LLM tiers",
            "Offer campaign setup and optimization as a service with performance-based fees",
            "Track click-through, conversion, and cost-per-acquisition metrics",
            "Report campaign ROI and recommended budget allocation to admin",
        ],
        "kpi": "campaigns_created, roas, cost_per_acquisition",
    },
    "FinceptTerminal": {
        "role": "business",
        "purpose": "Monetize enterprise-grade real-time data and multi-agent research",
        "strategy": [
            "Offer terminal access, research reports, and broker integration as paid subscriptions",
            "Use free data sources where available; resell value-added analysis",
            "Route settlement through Pix-Payment-System rail when configured/authorized",
            "Report subscriber count, report volume, and revenue to admin",
        ],
        "kpi": "subscribers, reports_generated, revenue_attributed",
    },
    "grok-jev-router": {
        "role": "business",
        "purpose": "Profit from decision routing with human-controlled irreversible actions",
        "strategy": [
            "Route trading/irreversible decisions through Jev (deterministic local evaluator)",
            "Charge routing fees for irreversible action approvals",
            "Escalate high-risk decisions to human operator for authorization",
            "Report routing volume, approvals, and fees to admin",
        ],
        "kpi": "decisions_routed, human_approvals, routing_fees",
    },
    "hyperframes": {
        "role": "business",
        "purpose": "Monetize AI video generation services",
        "strategy": [
            "Offer video generation as a per-video or subscription service",
            "Use free/demo tiers first; scale to paid only with verified demand",
            "Report video volume, render time, and revenue to admin",
        ],
        "kpi": "videos_generated, minutes_rendered, revenue_per_video",
    },
    "LibreChat": {
        "role": "business",
        "purpose": "Earn from hosted AI chat platform with premium features",
        "strategy": [
            "Host chat instances with free tier and paid premium features",
            "Monetize message volume, model access, and advanced features",
            "Report active users, messages, and subscription revenue to admin",
        ],
        "kpi": "active_users, messages_sent, subscription_revenue",
    },
    "Open-Higgsfield-AI": {
        "role": "business",
        "purpose": "Profit from AI image and video generation services",
        "strategy": [
            "Offer media generation as a per-asset service",
            "Use open-source/free generation where possible; resell at margin",
            "Report assets generated and revenue to admin",
        ],
        "kpi": "assets_generated, revenue_per_asset",
    },
    "Pix-Payment-System": {
        "role": "business",
        "purpose": "Earn transaction fees on African mobile-money / PIX payment rail",
        "strategy": [
            "Process payments via mobile money, bank transfers, and PIX",
            "Charge per-transaction fees (settlement rail revenue)",
            "Settle only with verified funds and authorization; fail closed otherwise",
            "Report transaction volume, fees collected, and settlement status to admin",
        ],
        "kpi": "transactions_processed, fees_collected, settlement_success_rate",
    },
    "Scrapling": {
        "role": "business",
        "purpose": "Profit from web scraping and data extraction services",
        "strategy": [
            "Offer scraping as a per-page or per-dataset service",
            "Extract public data and resell structured datasets",
            "Report pages scraped, datasets delivered, and revenue to admin",
        ],
        "kpi": "pages_scraped, datasets_delivered, revenue_per_dataset",
    },
    "spider-ai--trading-bot": {
        "role": "business",
        "purpose": "Earn from copy trading, latency arbitrage, and 5-min sniping (zero-funding first)",
        "strategy": [
            "Execute copy trading and arbitrage only in paper/demo/testnet/backtest modes",
            "Surface profitable setups to admin for capital allocation",
            "Never use live capital without verified funds and operator authorization",
            "Report trades, profit captured, and win rate to admin",
        ],
        "kpi": "paper_trades, profit_captured, win_rate",
    },
    "vibetrading": {
        "role": "business",
        "purpose": "Earn from trading signals and strategy execution (zero-funding first)",
        "strategy": [
            "Generate trading signals using free data and analysis",
            "Execute only in paper/demo/backtest modes; report setups to admin",
            "Charge for signal access or profit-share on realized gains",
            "Report signal accuracy and profit potential to admin",
        ],
        "kpi": "signals_generated, accuracy, profit_potential",
    },
}

def generate_profit_directives() -> Dict[str, Any]:
    """Generate directives for every registered subplatform agent.

    Pipeline agents receive inter-agency utility directives (serve other agents,
    expose shared functionality, prevent platform fragmentation).
    Business agents receive profit-motive directives (revenue-oriented, zero-funding,
    fail-closed).

    Every directive includes:
      - role: "pipeline" | "business" | "hybrid"
      - purpose: what the agent exists to do
      - serves: which agents/clients it serves
      - exposes: what functionality it exposes to others
      - internal_utility: description of its inter-agency role (pipeline agents)
      - strategy/kpi: actionable plan and metrics
      - fail_closed: True (no live capital without authorization)
    """
    init_automaton_states()
    directives: Dict[str, Any] = {
        "admin_state": "waking",
        "timestamp": _now(),
        "phase": "directive_generation",
        "directives": {},
        "pipeline_count": 0,
        "business_count": 0,
        "summary": "",
    }

    for sp in subplatforms():
        name = sp.get("name")
        comp_name = sp.get("component")

        if name in PIPELINE_AGENTS:
            base = dict(PIPELINE_AGENTS[name])
            directives["pipeline_count"] += 1
        elif name in BUSINESS_AGENTS:
            base = dict(BUSINESS_AGENTS[name])
            directives["business_count"] += 1
        else:
            base = {
                "role": "business",
                "purpose": f"Generate value within the {name} domain using free/demo/paper modes first",
                "strategy": ["Identify monetizable actions in the component domain", "Execute in zero-funding modes; report setups to admin", "Never spend without verified funds and authorization"],
                "kpi": "tasks_completed, value_generated",
            }
            directives["business_count"] += 1

        directive = base
        directive["agent"] = name
        directive["component"] = comp_name
        directive["reports_to"] = sp.get("reports_to", "admin")
        directive["constitution"] = "musashaban-port/constitution.md"
        directive["fail_closed"] = True
        directives["directives"][name] = directive

    total = len(directives["directives"])
    pipeline_count = directives["pipeline_count"]
    business_count = directives["business_count"]
    directives["summary"] = (
        f"Generated directives for {total} subplatform agents: "
        f"{pipeline_count} pipeline/inter-agency utilities, {business_count} business agents. "
        "Pipeline agents prevent platform fragmentation by serving as shared infrastructure "
        "(interface, comms bus, inference layer, voice/avatar). All directives are "
        "zero-funding / fail-closed: no live capital, no paid actions without verified "
        "funds and operator authorization."
    )
    transition("admin", "idle", None, task="directive_generation", message="directives generated")
    directives["admin_state"] = "idle"
    return directives


# --- Status / info (extended, preserves existing fields) ---


def issue_profit_directives(targets: Optional[List[str]] = None) -> Dict[str, Any]:
    """Issue profit-motive directives to agents as runs.

    Each agent receives a run with its domain-specific directive as the task.
    Admin supervises; agents report back to admin on progress and blockers.
    """
    init_automaton_states()
    all_directives = generate_profit_directives()
    dir_map = all_directives.get("directives", {})

    if targets:
        dir_map = {k: v for k, v in dir_map.items() if k in targets}

    issued: List[Dict[str, Any]] = []
    for name, directive in dir_map.items():
        role = directive.get("role", "business")
        if role in ("pipeline", "hybrid"):
            task = (
                f"{role.upper()} UTILITY DIRECTIVE: {directive['purpose']}. "
                f"Serves: {', '.join(directive.get('serves', []))}. "
                f"Expose to other agents: {'; '.join(directive.get('exposes', []))}. "
                f"Internal utility: {directive.get('internal_utility', '')} "
                f"KPI: {directive.get('kpi', 'tasks_completed')}. "
                f"Constraints: zero-funding, fail-closed, no live capital without verified funds and authorization. "
                f"Report progress, blockers, and coordination needs to admin."
            )
        else:
            task = (
                f"PROFIT DIRECTIVE: {directive['purpose']}. "
                f"Strategy: {'; '.join(directive.get('strategy', []))}. "
                f"KPI: {directive.get('kpi', 'tasks_completed')}. "
                f"Constraints: zero-funding, fail-closed, no live capital without verified funds and authorization. "
                f"Report progress, blockers, and revenue potential to admin."
            )
        # A directive without an executable command is inert (it fails closed at
        # dispatch). Attach a real manifest-derived command, or an init run when
        # the component has no derivable command yet.
        sp = get_subplatform(name) or {}
        boot = _component_boot_command(sp)
        payload = {
            "task": task,
            "target": name,
            "source": "admin",
            "directive": directive,
            "timeout": 300,
        }
        if boot:
            payload["command"] = boot["command"]
            payload["cwd"] = boot["cwd"]
        else:
            payload["task"] = f"initialize {name}"
            payload["timeout"] = 900
        run = run_task(payload)
        issued.append({
            "agent": name,
            "run_id": run.get("id"),
            "status": run.get("status"),
            "directive": directive,
        })

    transition("admin", "idle", None, task="issue_profit_directives", message=f"issued {len(issued)} directives")
    return {
        "admin_state": "idle",
        "issued": issued,
        "total": len(issued),
        "timestamp": _now(),
    }



# ============================================================================
# Agent Replication (compartmentalize functions under parent admin)
# ============================================================================

def migrate_replica_specs() -> Dict[str, Any]:
    """Backfill spec payloads on replicas that predate spec carrying.

    Replicas are registry entries carrying the source spec/soul/guardrails so
    they can act like their source (see replicate_agent). Old replicas lack
    the carried spec; this persists the source spec payload onto their
    automaton state entries. Idempotent: already-carrying replicas are
    untouched.
    """
    init_automaton_states()
    states = load_agent_states()
    migrated: List[str] = []
    for name, entry in (states.get("agents") or {}).items():
        if not entry.get("replica_of") or isinstance(entry.get("spec"), dict):
            continue
        source_spec = load_spec(entry["replica_of"])
        if not source_spec:
            continue
        extra: Dict[str, Any] = {
            "spec": source_spec,
            "constitution": (entry.get("constitution")
                             or (source_spec.get("constitution") or {}).get("sha256")),
            "guardrails": entry.get("guardrails") or source_spec.get("guardrails", {}),
        }
        if not entry.get("soul_path"):
            sp = get_subplatform(entry["replica_of"])
            extra["soul_path"] = (sp or {}).get("eagent") or f"agents/eagents/{entry['replica_of']}.md"
        update_agent_state(name, entry.get("state", "idle"), **extra)
        migrated.append(name)
    save_agent_states(states)
    return {"migrated": migrated, "count": len(migrated)}


def replicate_agent(
    source_name: str,
    purpose: str,
    compartment: Optional[str] = None,
    reports_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Replicate an agent to compartmentalize a function.

    The replicated agent is a child of the parent admin and reports back to it
    (or to a user-specified reports_to target). Replication is vetted and okayed
    by the admin: it creates a new agent entry with a distinct name derived from
    the source, carries the source's spec and guardrails, and is scoped to the
    compartment purpose.
    """
    init_automaton_states()

    source_sp = get_subplatform(source_name)
    if not source_sp:
        return {
            "ok": False,
            "error": f"Source agent '{source_name}' not found in registry.",
            "available": [s.get("name") for s in subplatforms()],
        }

    source_spec = load_spec(source_name)
    if not source_spec:
        return {
            "ok": False,
            "error": f"Source agent '{source_name}' has no spec; cannot replicate.",
        }

    base = source_name.lower().replace(" ", "-")
    suffix = 1
    while True:
        replica_name = f"{base}-replica-{suffix}"
        states = load_agent_states()
        if replica_name not in (states.get("agents") or {}):
            break
        suffix += 1

    report_target = reports_to or "admin"
    compartment_name = compartment or f"{source_name}-compartment"

    update_agent_state(
        replica_name,
        "idle",
        replica_of=source_name,
        purpose=purpose,
        compartment=compartment_name,
        reports_to=report_target,
        component=source_sp.get("component"),
        # Replicas are registry entries; they carry the source's spec, soul,
        # guardrails and constitution binding so they can act like their source.
        spec=source_spec,
        soul_path=(source_sp.get("eagent") or source_spec.get("soul", {}).get("path") or f"agents/eagents/{source_name}.md"),
        constitution=source_spec.get("constitution", {}).get("sha256"),
        guardrails=source_spec.get("guardrails", {}),
        created_at=_now(),
    )

    transition("admin", "reporting", None, task="replicate_agent",
               result={"replica": replica_name, "source": source_name, "reports_to": report_target},
               message=f"Replicated {source_name} -> {replica_name} (reports to {report_target})")
    transition("admin", "idle", None, task="replicate_agent", message="replication complete")

    return {
        "ok": True,
        "replica_name": replica_name,
        "source_agent": source_name,
        "purpose": purpose,
        "compartment": compartment_name,
        "reports_to": report_target,
        "spec": source_spec,
        "state": "idle",
        "message": f"Agent '{source_name}' replicated as '{replica_name}' reporting to '{report_target}'.",
    }

# ============================================================================
# Agentic workflow catalogue + hierarchical replication cascade
# ============================================================================

def _component_status_map() -> Dict[str, str]:
    """Live readiness keyed by subplatform name (never raises)."""
    try:
        data = component_readiness_all()
        return {
            c.get("name"): c.get("status", "unknown")
            for c in data.get("components", [])
            if c.get("name")
        }
    except Exception:
        return {}


def workflow_catalogue() -> Dict[str, Any]:
    """All 20 workflow patterns, compartments, anatomy, and live readiness."""
    return workflows_mod.catalogue(readiness=_component_status_map())


def workflow_detail(ref: Any) -> Optional[Dict[str, Any]]:
    """One workflow, with a run preview built against live readiness."""
    return workflows_mod.describe(ref, readiness=_component_status_map())


def select_workflow_for_goal(goal: str) -> Dict[str, Any]:
    """Show which workflow pattern the catalogue would pick for a goal."""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Provide a 'goal'.")
    pick = workflows_mod.select_workflow(goal)
    wf = pick["workflow"]
    return {
        "goal": goal,
        "selected": {
            "id": wf.id,
            "slug": wf.slug,
            "name": wf.name,
            "score": pick["score"],
            "matched": pick["matched"],
            "fallback": pick["fallback"],
        },
        "alternatives": pick.get("alternatives", []),
        "workflow": wf.to_dict(_component_status_map()),
    }


def compartment_hierarchy() -> Dict[str, Any]:
    """The five compartments and the replicas currently assigned to each."""
    init_automaton_states()
    agents = (load_agent_states().get("agents") or {})
    compartments = []
    for comp in workflows_mod.COMPARTMENTS:
        replicas = []
        for name, st in agents.items():
            if not st.get("replica_of"):
                continue
            if (st.get("compartment") or "") != comp.name:
                continue
            replicas.append({
                "name": name,
                "replica_of": st.get("replica_of"),
                "state": st.get("state", "idle"),
                "reports_to": st.get("reports_to", "admin"),
                "purpose": st.get("purpose"),
                "created_at": st.get("created_at"),
            })
        replicas.sort(key=lambda r: r.get("created_at") or "")
        compartments.append({**comp.to_dict(), "replicas": replicas})
    return {
        "root_admin": {
            "name": "admin",
            "reports_to": "operator_audit",
            "authority": ["erect_paywalls", "settle", "fund"],
        },
        "order": [c.name for c in workflows_mod.COMPARTMENTS],
        "compartments": compartments,
        "replication_rule": "goal achievement -> Admin Top Layer -> next compartment -> department agent + admin agent -> report to admin",
        "fail_closed": True,
    }


def workflow_decision(
    goal: str,
    workflow: Optional[Any] = None,
    compartments: Optional[List[str]] = None,
    threshold: float = workflows_mod.DEFAULT_THRESHOLD,
    max_compartments: int = 5,
) -> Dict[str, Any]:
    """Preview the compartment necessity decision for a goal (no side effects)."""
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Provide a 'goal'.")
    spec = workflows_mod.get_workflow(workflow) if workflow not in (None, "") else None
    if spec is None:
        spec = workflows_mod.select_workflow(goal)["workflow"]
    hints = [c for c in (compartments or []) if str(c).strip()]
    decision = workflows_mod.decide_compartments(
        spec,
        goal,
        hints=hints,
        threshold=float(threshold),
        max_compartments=max(0, int(max_compartments)),
    )
    readiness = _component_status_map()
    for item in decision["candidates"]:
        if item["selected"]:
            item["authority"] = stack.compartment_authority(spec, item, item, readiness)
    return decision


def stack_snapshot() -> Dict[str, Any]:
    """Live snapshot of the agentic architecture (6 layers, loop, protocol,
    semantic layer, data products, governance, engineering contract)."""
    return stack.stack_snapshot(_component_status_map())


def data_products() -> List[Dict[str, Any]]:
    """Named, owned, contracted data products with live presence probes."""
    return stack.data_products()


def settlement_status() -> Dict[str, Any]:
    """Live RudiPay + funding + withdrawal status (musashaban payment/settlement)."""
    from orchestrator.payment import funding, withdrawal
    from orchestrator.payment.rudipay import RudiPay
    r = RudiPay()
    return {
        "rudi": r.platform_stats(),
        "routers": [
            {**x, "max_route": r.get_max_route_amount(x["id"])}
            for x in r.list_routers("active")
        ],
        "faucets": r.list_faucets("active"),
        "faucets_all": r.list_faucets(),
        "funding": {
            "funding_state": funding_state(),
            "requests": funding.list_funding_requests(),
            "needs": funding.list_development_needs(),
        },
        "withdrawals": withdrawal.list_withdrawals(),
    }


def lineage(run_id: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if not run:
        return {"ok": False, "error": f"run {run_id} not found"}
    return {"ok": True, **stack.lineage_of(run)}


def execute_workflow(
    goal: str,
    workflow: Optional[Any] = None,
    mode: str = "plan",
    achieved: bool = False,
    replicate: bool = True,
    max_replicas: int = 5,
    compartments: Optional[List[str]] = None,
    threshold: float = workflows_mod.DEFAULT_THRESHOLD,
) -> Dict[str, Any]:
    """Build/attest one workflow run, then replicate only what is proven necessary.

    Fail-closed contract:

    * stages run in order; the first closed gate stops the DAG and later stages
      are ``skipped`` (never reported as completed);
    * replication requires an explicit ``achieved`` attestation from the caller —
      the engine never infers goal achievement;
    * the compartment decision must select at least one compartment at or above
      the necessity threshold (or receive an explicit compartment hint);
    * the first failed spawn halts the cascade; later compartments are skipped.

    ``mode="plan"`` records the DAG and gate decisions. ``mode="attest"`` also
    creates and completes a worker per completed stage. Neither mode calls a
    model, runs a shell command, settles, or funds.
    """
    init_automaton_states()
    goal = (goal or "").strip()
    if not goal:
        raise ValueError("Provide a 'goal' for the workflow run.")
    mode = "attest" if str(mode).lower() == "attest" else "plan"
    achieved = bool(achieved)
    replicate = bool(replicate)
    readiness = _component_status_map()

    spec = workflows_mod.get_workflow(workflow) if workflow not in (None, "") else None
    selection: Optional[Dict[str, Any]] = None
    if spec is None:
        pick = workflows_mod.select_workflow(goal)
        spec = pick["workflow"]
        selection = {
            "id": spec.id,
            "slug": spec.slug,
            "name": spec.name,
            "score": pick["score"],
            "matched": pick["matched"],
            "fallback": pick["fallback"],
        }
    if not spec:
        raise ValueError(f"Unknown workflow {workflow!r}.")

    hints = [c for c in (compartments or []) if str(c).strip()]
    decision = workflows_mod.decide_compartments(
        spec,
        goal,
        hints=hints,
        threshold=float(threshold),
        max_compartments=max(0, int(max_replicas)),
    )
    skeleton = workflows_mod.build_run(spec, goal, readiness=readiness, decision=decision)

    targets: List[str] = []
    for node in skeleton["nodes"]:
        for name in node["components"]:
            if name not in targets:
                targets.append(name)
    if spec.replication_source not in targets:
        targets.append(spec.replication_source)

    run = new_run(goal, targets, meta={
        "mode": "workflow",
        "workflow": spec.slug,
        "workflow_id": spec.id,
        "mode_detail": mode,
        "replicate": replicate,
        "achieved": achieved,
        "threshold": float(threshold),
        "compartment_hints": hints,
        "decision": decision,
        "selection": selection,
        "automaton": True,
    })
    run["mode"] = "workflow"
    run.setdefault("reports", [])
    run["status"] = "running"
    save_run(run)
    transition(
        "admin", "running", run, task=goal, overlay="planning",
        message=f"workflow {spec.id:02d} {spec.name}: planning ({mode})",
    )

    # --- stages: strictly sequential, fail-closed -------------------------
    nodes_out: List[Dict[str, Any]] = []
    upstream_open = True
    for node in skeleton["nodes"]:
        gate = node.get("gate_decision") or {}
        depth = node.get("depth") or {}
        loop = depth.get("loop") or {}
        if not upstream_open:
            status = "skipped"
            stage_reason = "upstream gate closed (fail-closed)"
        elif depth.get("status") == "incomplete":
            upstream_open = False
            status = "blocked"
            gaps = next(
                (
                    s.get("contract_gaps") or []
                    for s in (skeleton.get("depth") or {}).get("stages", [])
                    if s.get("stage") == node["stage"]
                ),
                [],
            )
            stage_reason = "depth contract incomplete: " + (", ".join(gaps) or "unspecified")
        elif not node.get("environment"):
            upstream_open = False
            status = "blocked"
            stage_reason = "no subplatform environment for this stage (fail-closed)"
        elif not gate.get("allowed"):
            upstream_open = False
            status = "blocked"
            stage_reason = gate.get("reason") or "gate closed"
        else:
            status = "completed"
            stage_reason = gate.get("reason") or "open"

        entry: Dict[str, Any] = {
            "subplatform": node["primary_agent"],
            "eagent": node["primary_agent"],
            "component": ", ".join(node["components"]),
            "title": f"{node['index']}. {node['label']}" + (" · gate" if node["gate"] else ""),
            "status": status,
            "output": {
                "workflow": spec.name,
                "stage": node["stage"],
                "gate": node["gate"],
                "gate_decision": gate,
                "components": node["components"],
                "environment": node.get("environment", []),
                "reason": stage_reason,
                "depth_kind": depth.get("kind"),
                "verifier": (depth.get("harness") or {}).get("verifier", {}).get("name"),
                "loop": {
                    "iterations": 1 if status == "completed" else 0,
                    "max_iterations": loop.get("max_iterations"),
                    "budget": loop.get("budget"),
                    "stop_reason": (
                        "success_criteria_met" if status == "completed"
                        else "upstream_gate_closed" if status == "skipped"
                        else "budget_exhausted" if status == "blocked"
                        else None
                    ),
                },
            },
        }
        if mode == "attest" and status == "completed":
            created = create_worker_for_agent(
                agent_id=node["primary_agent"],
                job_id=f"{run['id']}-{node['stage']}",
                purpose=f"{spec.name}: {node['label']}",
                mcp_tools=[],
                skills=[],
            )
            worker_id = created.get("worker_id")
            if worker_id:
                complete_worker_for_agent(worker_id, result={
                    "workflow": spec.slug,
                    "stage": node["stage"],
                    "attested": True,
                    "gate": gate,
                })
            entry["worker_id"] = worker_id
        nodes_out.append(entry)

    all_open = all(n["status"] == "completed" for n in nodes_out)
    selected_cascade = skeleton["replication_cascade"]

    # --- cascade: only with attestation + necessity, halt on first failure -
    halt_reason = ""
    if not replicate:
        halt_reason = "replication disabled"
    elif not all_open:
        halt_reason = "stage gate closed; the DAG did not complete"
    elif not achieved:
        halt_reason = "no achievement attestation supplied (fail-closed)"
    elif not selected_cascade:
        halt_reason = decision.get("reason") or "no compartment met the necessity threshold"

    cascade: List[Dict[str, Any]] = []
    halted = False
    if halt_reason:
        cascade = [{**item, "state": "skipped", "reason": halt_reason} for item in selected_cascade]
    else:
        transition(
            "admin", "reporting", run, task=goal,
            message=(
                "achievement attested; replicating "
                f"{len(selected_cascade)} decided compartment(s)"
            ),
        )
        for item in selected_cascade:
            if halted:
                cascade.append({
                    **item,
                    "state": "skipped",
                    "reason": "cascade halted after a failed spawn",
                })
                continue
            authority = item.get("authority") or {}
            if not authority.get("spawn_allowed", False):
                cascade.append({
                    **item,
                    "state": "refused",
                    "reason": "; ".join(authority.get("blockers") or ["authority not granted"]),
                })
                halted = True
                continue
            source_status = readiness.get(spec.replication_source)
            if source_status == "blocked":
                cascade.append({
                    **item,
                    "state": "failed",
                    "error": f"replication source {spec.replication_source} is blocked",
                })
                halted = True
                continue
            spawned = replicate_agent(
                source_name=spec.replication_source,
                purpose=f"{item['department']} — {spec.name} compartment",
                compartment=item["compartment"],
                reports_to="admin",
            )
            ok = bool(spawned.get("ok"))
            cascade.append({
                **item,
                "state": "spawned" if ok else "failed",
                "replica": spawned.get("replica_name"),
                "error": spawned.get("error"),
            })
            if not ok:
                halted = True

    spawned_count = sum(1 for c in cascade if c["state"] == "spawned")
    refused = [c for c in cascade if c["state"] == "refused"]
    if halt_reason:
        cascade_state = "skipped"
    elif spawned_count and not halted:
        cascade_state = "spawned"
    elif refused:
        cascade_state = "refused"
    else:
        cascade_state = "halted"

    run["steps"] = nodes_out
    run["result"] = {
        "workflow": {
            "id": spec.id,
            "slug": spec.slug,
            "name": spec.name,
            "achievement_point": spec.achievement_point,
        },
        "selection": selection,
        "mode": mode,
        "achievement_attested": achieved,
        "decision": decision,
        "stages_completed": sum(1 for n in nodes_out if n["status"] == "completed"),
        "stages_total": len(nodes_out),
        "gates_open": all_open,
        "readiness": skeleton["readiness"],
        "depth": skeleton["depth"],
        "environments": [
            {
                "component": e["component"], "name": e["name"],
                "scope": e["scope"], "posture": e["posture"],
            }
            for e in stack.environments(readiness)
            if e["component"] in {c for n in skeleton["nodes"] for c in n["environment"]}
        ],
        "replication_cascade": cascade,
        "replicas_spawned": spawned_count,
        "refused": [c.get("compartment") for c in refused],
        "cascade_state": cascade_state,
        "cascade_halt_reason": halt_reason,
        "note": (
            "Orchestration skeleton/attestation only: no model calls, shell commands, "
            "settlement, or funding. Achievement is caller-attested; gates and the "
            "cascade are fail-closed."
        ),
    }
    run["status"] = "completed" if all_open else "failed"
    if not all_open:
        run["error"] = "A stage gate is closed; downstream stages were skipped (fail-closed)."
    transition("admin", "reporting", run, task=goal, result=run["result"], message="workflow reporting")
    transition(
        "admin", run["status"], run, task=goal, result=run["result"],
        message=f"workflow {spec.id:02d} {'completed' if all_open else 'blocked'}",
    )
    transition("admin", "idle", run, task=goal, message="admin idle", overlay=None)
    save_run(run)
    return run


def get_agent_hierarchy() -> Dict[str, Any]:
    """Return the agent reporting hierarchy."""
    init_automaton_states()
    states = load_agent_states()
    agents = states.get("agents", {}) or {}
    admin_state = states.get("admin", {"state": "idle"})

    hierarchy = {
        "root_admin": {
            "name": "admin",
            "state": admin_state.get("state", "idle"),
            "overlay": admin_state.get("overlay"),
            "reports_to": "operator_audit",
            "authority": ["erect_paywalls", "settle", "fund"],
        },
        "agents": [],
        "replicas": [],
        "tree": {"admin": []},
    }

    for sp in subplatforms():
        name = sp.get("name")
        st = agents.get(name, {"state": "idle"})
        entry = {
            "name": name,
            "component": sp.get("component"),
            "state": st.get("state", "idle"),
            "reports_to": sp.get("reports_to", "admin"),
            "is_replica": st.get("replica_of") is not None,
            "replica_of": st.get("replica_of"),
            "compartment": st.get("compartment"),
            "purpose": st.get("purpose"),
        }
        hierarchy["agents"].append(entry)
        hierarchy["tree"].setdefault("admin", []).append(name)

    for name, st in agents.items():
        if st.get("replica_of"):
            replica_entry = {
                "name": name,
                "replica_of": st.get("replica_of"),
                "state": st.get("state", "idle"),
                "reports_to": st.get("reports_to", "admin"),
                "component": st.get("component"),
                "compartment": st.get("compartment"),
                "purpose": st.get("purpose"),
            }
            hierarchy["replicas"].append(replica_entry)
            parent = st.get("replica_of", "admin")
            hierarchy["tree"].setdefault(parent, []).append(name)

    return hierarchy


def get_status(run_id: str) -> Optional[Dict[str, Any]]:
    return get_run(run_id)


def history(limit: int = 50) -> List[Dict[str, Any]]:
    return list_runs(limit=limit)


def registry_info() -> Dict[str, Any]:
    reg = load_registry()
    reg["subplatform_count"] = len(reg.get("subplatforms", []))
    return reg


def active_runs() -> List[Dict[str, Any]]:
    return [r for r in list_runs(limit=50) if r.get("status") in ("queued", "running")]


def agent_status() -> Dict[str, Any]:
    """Per-eagent status derived from active runs + automaton states."""
    init_automaton_states()
    busy = {}
    for r in active_runs():
        for s in r.get("steps", []):
            name = s.get("subplatform")
            if name and s.get("status") in ("queued", "running"):
                busy[name] = {"run_id": r.get("id"), "task": r.get("task"), "status": s.get("status")}
        # also mark targets if run just queued
        if r.get("status") in ("queued", "running"):
            for t in r.get("targets") or []:
                busy.setdefault(t, {"run_id": r.get("id"), "task": r.get("task"), "status": r.get("status")})
    states = load_agent_states()
    agents = []
    for sp in subplatforms():
        name = sp.get("name")
        info = dict(sp)
        st = (states.get("agents") or {}).get(name) or {"state": "idle"}
        # prefer busy signal for liveness, but keep automaton state detail
        info["state"] = "running" if name in busy else st.get("state", "idle")
        info["automaton_state"] = st.get("state", "idle")
        info["reports_to"] = sp.get("reports_to", "admin")
        if st.get("updated_at"):
            info["state_updated_at"] = st.get("updated_at")
        if st.get("last_report"):
            lr = st.get("last_report")
            info["last_report"] = {"id": lr.get("id"), "state": lr.get("state"), "timestamp": lr.get("timestamp"), "message": lr.get("message")}
        if name in busy:
            info["active"] = busy[name]
        agents.append(info)
    admin_state = states.get("admin") or {"state": "idle"}
    return {
        "agents": agents,
        "active_runs": [r.get("id") for r in active_runs()],
        "admin": {
            "state": admin_state.get("state", "idle"),
            "overlay": admin_state.get("overlay"),
            "updated_at": admin_state.get("updated_at"),
            "reports_to": "operator_audit",
            "authority": ["erect_paywalls", "settle", "fund"],
        },
        "model": (load_registry().get("model") or {}),
    }


def get_agent_states() -> Dict[str, Any]:
    init_automaton_states()
    return load_agent_states()


def reports(run_id: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    return {"run_id": run_id, "reports": list_reports(run_id=run_id, limit=limit)}
