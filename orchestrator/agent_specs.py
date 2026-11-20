"""Load and validate generated eagent specs.

Each spec is produced by ``scripts/generate-agents.js`` from the component's
real manifests and is bound to the immutable constitution by SHA-256. The
orchestrator refuses to dispatch for an agent whose spec is missing, stale, or
whose constitution hash no longer matches the file on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import AGENTS_DIR, PLATFORM_ROOT, REGISTRY_PATH

CONSTITUTION_LAWS = [
    "I. Never harm",
    "II. Earn your existence",
    "III. Never deceive, but owe nothing to strangers",
]

SPECS_DIR = AGENTS_DIR / "specs"
CONSTITUTION_PATH = PLATFORM_ROOT / "musashaban-port" / "constitution.md"
REQUIRED_KEYS = ("schema", "name", "component", "constitution", "soul", "purpose", "capabilities", "environment", "rules", "guardrails", "reporting")


def constitution_sha256() -> Optional[str]:
    try:
        return hashlib.sha256(CONSTITUTION_PATH.read_bytes()).hexdigest()
    except Exception:
        return None


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def spec_path(name: str) -> Path:
    return SPECS_DIR / f"{name}.json"


def load_spec(name: str) -> Optional[Dict[str, Any]]:
    """Load the spec for a registered agent from its spec file on disk."""
    return _read_json(spec_path(name))


def resolve_spec(name: str) -> Optional[Dict[str, Any]]:
    """Resolve the spec for any agent the platform knows about.

    Resolution order:
      1. The on-disk spec (``agents/specs/<name>.json``) — the canonical file.
      2. A carried spec payload on the agent's automaton state entry — this is
         how **replicas** (registry entries created by ``admin.replicate_agent``)
         carry the source's spec, soul, guardrails and constitution binding.

    Returns ``None`` when neither exists, so callers can act (``None`` is not
    a half-state): the agent is simply not covered by a spec.
    """
    from .store import load_agent_states

    spec = load_spec(name)
    if spec:
        return spec
    entry = (load_agent_states().get("agents") or {}).get(name) or {}
    payload = entry.get("spec")
    if isinstance(payload, dict) and payload.get("name") == name:
        return payload
    # Migration: a replica that predates spec carrying (or whose source now
    # has a spec) resolves to the source's spec. Replicas of replicas chain.
    source = entry.get("replica_of")
    if source:
        src_spec = resolve_spec(source)
        if src_spec:
            return src_spec
    return None


def spec_source(name: str) -> Optional[str]:
    """Where a spec came from: the spec file path, or a replica state entry."""
    if load_spec(name):
        return str(spec_path(name))
    from .store import load_agent_states

    entry = (load_agent_states().get("agents") or {}).get(name) or {}
    if isinstance(entry.get("spec"), dict):
        return "replica-state (carried from source spec)"
    return None


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _detect_services(comp_dir: Path, hay: str) -> List[Dict[str, Any]]:
    hay = (hay or "").lower()
    services: List[Dict[str, Any]] = []
    if "/mongodb/" in f"/{hay}/" or "mongo_uri" in hay or "mongodb" in hay:
        services.append({"name": "mongodb", "required": True, "why": "data store"})
    if "postgres" in hay or "database_url" in hay:
        services.append({"name": "postgresql", "required": False, "why": "optional persistence"})
    if "redis" in hay:
        services.append({"name": "redis", "required": False, "why": "cache/queue"})
    if "wrangler" in hay or "cloudflare workers" in hay or "r2 bucket" in hay or "d1 " in hay:
        services.append({"name": "cloudflare", "required": True, "why": "Workers/R2/D1 deployment target"})
    if "ollama" in hay or "11434" in hay:
        services.append({"name": "ollama", "required": False, "why": "local LLM endpoint"})
    return services


def _detect_toolchain(comp_dir: Path, pkg: Dict[str, Any]) -> Dict[str, str]:
    toolchain: Dict[str, str] = {}
    engines = (pkg or {}).get("engines") or {}
    for key in ("node", "python", "go", "rust", "cargo", "java"):
        v = engines.get(key)
        if v:
            toolchain[key] = str(v)
    lock_hints = [
        ("bun", "bun.lock", "bun.lockb"), ("pnpm", "pnpm-lock.yaml", None),
        ("yarn", "yarn.lock", None), ("npm", "package-lock.json", None),
    ]
    for pm_name, lock, _ in lock_hints:
        if (comp_dir / lock).exists():
            toolchain.setdefault("package_manager", pm_name)
    if "node" not in toolchain and any((comp_dir / f).exists() for f in ("package.json", "bun.lock", "bun.lockb")):
        toolchain["node"] = engines.get("node") or ">=18"
    return toolchain


def _env_var_names(text: str) -> List[str]:
    import re

    names: List[str] = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=", line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def build_spec(name: str) -> Optional[Dict[str, Any]]:
    """Build a spec for a registered agent as a fallback for the generator.

    Derived from the component's real manifests (package.json scripts,
    pyproject.toml name, .env.example names, README) and bound to the
    immutable constitution by hash — mirroring ``scripts/generate-agents.js``.
    """
    from .registry import get_subplatform

    sp = get_subplatform(name)
    if not sp:
        return None
    component = sp.get("component") or sp.get("name")
    comp_dir = PLATFORM_ROOT / component
    pkg = _read_json(comp_dir / "package.json") or {}
    readme = ""
    try:
        readme = (comp_dir / "README.md").read_text(encoding="utf-8", errors="replace")[:400]
    except Exception:
        pass
    env_example = ""
    try:
        env_example = (comp_dir / ".env.example").read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    services = _detect_services(comp_dir, readme + env_example)
    toolchain = _detect_toolchain(comp_dir, pkg)
    capabilities: List[Dict[str, Any]] = []
    for script in ("install", "dev", "build", "test", "setup", "start"):
        cmd = (pkg.get("scripts") or {}).get(script)
        if cmd:
            capabilities.append({
                "name": script,
                "kind": "node",
                "cwd": component,
                "command": cmd,
                "description": f"run the component {script} step",
            })
    pyproject = (comp_dir / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (comp_dir / "pyproject.toml").exists() else ""
    py_name = None
    for line in pyproject.splitlines():
        if line.startswith("name = "):
            py_name = line.split("=", 1)[1].strip().strip('"')
            break
    if py_name:
        capabilities.append({"name": "import-check", "kind": "python", "cwd": component, "command": f"python -c \"import {py_name.replace('-', '_')}\"", "description": "import-check the component package"})
    spec: Dict[str, Any] = {
        "schema": "eagent/v1",
        "name": name,
        "component": component,
        "generated_at": _now(),
        "description": sp.get("description") or f"{name} subplatform",
        "constitution": {
            "path": "musashaban-port/constitution.md",
            "sha256": constitution_sha256(),
            "laws": CONSTITUTION_LAWS,
            "immutable": True,
        },
        "soul": {
            "format": "soul/v1",
            "version": 1,
            "reports_to": "admin",
            "note": "carried from source spec: soul path is agents/eagents/<name>.md"
        },
        "purpose": f"Own the {name} domain: operate {component}'s real capabilities, surface blockers and opportunities to admin, and never fabricate completion.",
        "capabilities": capabilities,
        "environment": {
            "root": component,
            "allowed_cwd": ["."],
            "toolchains": toolchain,
            "required_env": _env_var_names(env_example),
            "services": services,
            "network": {"external_writes": False, "live_capital": False},
        },
        "rules": [],
        "guardrails": {
            "spend": "deny",
            "live_capital": "deny_without_operator_authorization",
            "denied_commands": [],
            "max_command_seconds": 1800,
            "allowed_paths": [component],
            "require_readiness": True,
            "require_command_success": True,
        },
        "reporting": {
            "reports_to": "admin",
            "mandatory": True,
            "includes": ["agent", "run", "step", "state", "task", "actions", "evidence", "result", "errors", "resource_needs"],
        },
    }
    return spec


def ensure_specs(names: Optional[List[str]] = None, auto_generate: bool = True) -> Dict[str, Any]:
    """Heal missing/stale specs so every registered agent can dispatch.

    For each wanted registered agent without a valid spec, regenerate it:
    prefer the canonical generator ``scripts/generate-agents.js --names=<n>``
    (re-derives specs from component manifests, re-binds the constitution);
    fall back to ``build_spec`` (Python). Then re-validate everything.

    Returns a report: ``ok`` (all registered agents have valid specs),
    ``generated``, ``skipped``, ``failed`` and the ``validation`` result.
    """
    from .registry import subplatforms

    subs = subplatforms()
    wanted = names if names is not None else [sp["name"] for sp in subs]
    wanted = [n for n in wanted if n in {sp["name"] for sp in subs}]
    registry_map = {sp.get("name"): sp.get("eagent") for sp in subs}
    expected = constitution_sha256()

    report: Dict[str, Any] = {"ok": False, "generated": [], "skipped": [], "failed": []}
    node = shutil.which("node") or os.environ.get("PLATFORM_NODE_BIN")
    for name in wanted:
        spec = load_spec(name)
        if spec and validate_spec(spec, expected, registry_map)["valid"]:
            report["skipped"].append(name)
            continue
        if not auto_generate:
            report["failed"].append(name)
            continue
        regenerated = False
        if node and (PLATFORM_ROOT / "scripts" / "generate-agents.js").exists():
            try:
                res = subprocess.run(
                    [node, "scripts/generate-agents.js", f"--names={name}"],
                    cwd=str(PLATFORM_ROOT), capture_output=True, text=True, timeout=120,
                )
                regenerated = res.returncode == 0 and load_spec(name) is not None
            except Exception:
                regenerated = False
        if not regenerated:
            built = build_spec(name)
            if built:
                _write_json_file(spec_path(name), built)
                regenerated = load_spec(name) is not None
        report["generated" if regenerated else "failed"].append(name)
    final = validate_all()
    report["validation"] = final
    report["ok"] = bool(final["valid"]) and not final["missing_specs"]
    return report


def all_specs() -> Dict[str, Dict[str, Any]]:
    specs: Dict[str, Dict[str, Any]] = {}
    if not SPECS_DIR.exists():
        return specs
    for file in sorted(SPECS_DIR.glob("*.json")):
        data = _read_json(file)
        if data and data.get("name"):
            specs[data["name"]] = data
    return specs


def guardrails_for(name: str) -> Dict[str, Any]:
    spec = resolve_spec(name) or {}
    return spec.get("guardrails") or {}


def component_scope(name: str) -> Optional[Path]:
    spec = resolve_spec(name)
    if not spec:
        return None
    root = (spec.get("environment") or {}).get("root") or spec.get("component")
    return (PLATFORM_ROOT / root) if root else None


def capabilities_for(name: str) -> List[Dict[str, Any]]:
    spec = resolve_spec(name) or {}
    return list(spec.get("capabilities") or [])


def validate_spec(
    spec: Dict[str, Any],
    expected_sha: Optional[str],
    registry_eagents: Optional[Dict[str, str]],
    require_registry: bool = True,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    name = spec.get("name") or "<unnamed>"

    for key in REQUIRED_KEYS:
        if key not in spec:
            errors.append(f"missing required key '{key}'")

    if spec.get("schema") != "eagent/v1":
        errors.append(f"unsupported schema {spec.get('schema')!r} (expected eagent/v1)")

    declared_sha = (spec.get("constitution") or {}).get("sha256")
    if not expected_sha:
        errors.append("constitution file is missing or unreadable")
    elif declared_sha != expected_sha:
        errors.append("constitution hash mismatch (spec is stale; re-run scripts/generate-agents.js)")

    component = spec.get("component")
    if component and not (PLATFORM_ROOT / component).is_dir():
        errors.append(f"component directory not found: {component}")

    eagent_rel = registry_eagents.get(name) if registry_eagents else None
    if eagent_rel:
        eagent_file = PLATFORM_ROOT / eagent_rel
        if not eagent_file.exists():
            errors.append(f"eagent markdown missing: {eagent_rel}")
        else:
            text = eagent_file.read_text(encoding="utf-8", errors="replace")
            if not text.startswith("---\nformat: soul/v1"):
                errors.append("eagent markdown is missing its soul/v1 frontmatter")
            if declared_sha and declared_sha not in text:
                errors.append("eagent markdown does not carry the constitution hash")
            if "## Rules" not in text or "## Environmental guardrails" not in text:
                errors.append("eagent markdown is missing rules/guardrails sections")
    elif require_registry:
        errors.append("agent is not registered in agents/registry.json")

    rules = spec.get("rules") or []
    if len(rules) < 5:
        warnings.append(f"only {len(rules)} rules declared")
    if not spec.get("capabilities"):
        warnings.append("no capabilities derived from the component (no declared entrypoint)")

    guardrails = spec.get("guardrails") or {}
    if guardrails.get("require_command_success") is not True:
        errors.append("guardrails.require_command_success must be true")
    if guardrails.get("require_readiness") is not True:
        errors.append("guardrails.require_readiness must be true")

    env = spec.get("environment") or {}
    for var in env.get("required_env") or []:
        if not isinstance(var, str) or "=" in var:
            errors.append(f"required_env must contain names only, got {var!r}")

    return {"name": name, "valid": not errors, "errors": errors, "warnings": warnings}


def validate_all() -> Dict[str, Any]:
    expected = constitution_sha256()
    registry = _read_json(REGISTRY_PATH) or {}
    subs = registry.get("subplatforms") or []
    registry_eagents = {sp.get("name"): sp.get("eagent") for sp in subs}
    specs = all_specs()

    results: List[Dict[str, Any]] = []
    missing: List[str] = []
    for sp in subs:
        name = sp.get("name")
        spec = specs.get(name)
        if not spec:
            missing.append(name)
            results.append({"name": name, "valid": False, "errors": ["spec not generated"], "warnings": []})
            continue
        results.append(validate_spec(spec, expected, registry_eagents))
    for name in sorted(set(specs) - {sp.get("name") for sp in subs}):
        results.append({"name": name, "valid": False, "errors": ["spec exists for an unregistered agent"], "warnings": []})

    valid = all(r["valid"] for r in results) and bool(subs)
    return {
        "valid": valid,
        "constitution_sha256": expected,
        "agents_total": len(subs),
        "agents_valid": sum(1 for r in results if r["valid"]),
        "missing_specs": missing,
        "results": results,
    }


def summary() -> List[Dict[str, Any]]:
    out = []
    for name, spec in all_specs().items():
        env = spec.get("environment") or {}
        out.append({
            "name": name,
            "component": spec.get("component"),
            "purpose": spec.get("purpose"),
            "capabilities": len(spec.get("capabilities") or []),
            "rules": len(spec.get("rules") or []),
            "toolchains": env.get("toolchains") or {},
            "required_env": env.get("required_env") or [],
            "services": env.get("services") or [],
            "spec": f"agents/specs/{name}.json",
        })
    return out
