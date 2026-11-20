"""Reference architecture for the platform's agentic system.

``workflows.py`` answers *what shape the work takes* (a pattern and its DAG).
This module answers *what the work stands on*:

* the six-layer agent stack,
* the agent loop (goal -> interface -> plan -> reason -> tools -> memory ->
  result -> evaluate -> continue/stop),
* the protocol layer (MCP, and the honest gap where A2A is missing),
* the semantic layer (entities, relationships, glossary, context rules),
* the data products each stage consumes, with owners and contracts,
* the governance that constrains access, audit, privacy and quality,
* the four engineering layers every stage must specify before it may run:
  prompt, context, harness, loop.

Everything is mapped onto modules that already exist in this repository.
Names such as LangGraph, Qdrant or LangSmith appear only under ``analogues``
for orientation. Nothing here imports, installs, or depends on them.

Two rules are enforced, not merely described:

* a stage is runnable only if its depth contract is complete (role, context
  sources, tool authorization, verifier, success criteria, budget, stop
  conditions);
* a gate whose independent verifier is unavailable is closed, even when the
  producing component is healthy.

The engine still performs no model call, no shell command, and no
settlement or funding. Depth here means *specified and gated*, not *faked*.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import GEO_DB_PATH, PLATFORM_ROOT, REPORTS_DIR, RUNS_DIR, STATE_DIR


def _exists(rel: str) -> bool:
    return (PLATFORM_ROOT / rel).exists()


def _module(rel: str) -> Dict[str, Any]:
    return {"path": rel, "exists": _exists(rel)}


# ============================================================================
# 01-06: the six-layer agent stack
# ============================================================================

LAYERS: Tuple[Dict[str, Any], ...] = (
    {
        "key": "foundation_models",
        "order": 1,
        "name": "Foundation models",
        "purpose": "Reasoning and planning: interpret the goal, choose a pattern, propose actions.",
        "ours": [
            _module("orchestrator/router.py"),
        ],
        "components": ["freellmapi", "grok-jev-router"],
        "analogues": ["OpenAI", "Anthropic", "Gemini", "OpenRouter", "local weights"],
        "obligations": [
            "Provider-agnostic: no stage may assume a specific vendor.",
            "Model choice is recorded on the run, never inferred later.",
            "No key, prompt or completion is written to a data product.",
        ],
        "gap": (
            "There is no in-process model gateway. Model work belongs to the "
            "freellmapi/grok-jev-router components, and this engine attests "
            "reasoning rather than performing it."
        ),
    },
    {
        "key": "agent_frameworks",
        "order": 2,
        "name": "Agent frameworks",
        "purpose": "Plan, route and execute multi-step work with explicit state.",
        "ours": [
            _module("orchestrator/planner.py"),
            _module("orchestrator/router.py"),
            _module("orchestrator/admin.py"),
            _module("orchestrator/worker.py"),
            _module("orchestrator/workflows.py"),
        ],
        "components": [],
        "analogues": ["LangGraph", "CrewAI", "OpenAI Agents SDK", "Pydantic AI"],
        "obligations": [
            "State lives in a run record, not in a process.",
            "Every stage transition is reported.",
            "A closed gate stops the DAG; downstream stages are skipped, never faked.",
        ],
        "gap": "Single-process orchestration; no durable execution across restarts beyond run replay.",
    },
    {
        "key": "tools_protocols",
        "order": 3,
        "name": "Tools and protocols",
        "purpose": "How the agent reaches tools, data, other agents and the outside world.",
        "ours": [
            _module("orchestrator/mcp.py"),
            _module("orchestrator/tools.py"),
            _module("orchestrator/policy.py"),
        ],
        "components": ["Pix-Payment-System", "FinceptTerminal", "Scrapling"],
        "analogues": ["MCP", "A2A", "function calling", "Composio", "Firecrawl"],
        "obligations": [
            "Every tool call is authorized by policy before execution.",
            "A denied decision is final; the stage records blocked, never success.",
            "Tool schemas are declared, not inferred from prose.",
        ],
        "gap": (
            "MCP is implemented (one shared server); A2A is not. Agent-to-agent "
            "handoff is mediated by the router and admin, not a peer protocol."
        ),
    },
    {
        "key": "memory_knowledge",
        "order": 4,
        "name": "Memory and knowledge",
        "purpose": "What the agent remembers: run history, reports, structured records, skills.",
        "ours": [
            _module("orchestrator/store.py"),
            _module("orchestrator/geo.py"),
            _module("orchestrator/skills.py"),
        ],
        "components": ["Scrapling", "LibreChat", "agentic-inbox"],
        "analogues": ["Qdrant", "Chroma", "Redis", "pgvector", "LlamaIndex"],
        "obligations": [
            "Short-term state is scoped to a run; long-term state is a named data product.",
            "Retrieval returns provenance (which record, which run, which timestamp).",
            "Compartment stores are isolated; sharing happens only through a recorded handoff.",
        ],
        "gap": (
            "No embedding index or vector store. Retrieval is metadata and keyword "
            "lookup over local stores, so similarity search is designed but not implemented."
        ),
    },
    {
        "key": "runtime_hosting",
        "order": 5,
        "name": "Agent runtime and hosting",
        "purpose": "Where agents run, how workers are created, suspended and killed.",
        "ours": [
            _module("orchestrator/__main__.py"),
            _module("orchestrator/server.py"),
            _module("orchestrator/components.py"),
            _module("orchestrator/worker.py"),
        ],
        "components": ["agent-desktop"],
        "analogues": ["Docker", "Kubernetes", "Modal", "Cloudflare"],
        "obligations": [
            "Worker lifecycle is explicit: created -> running -> completed/killed.",
            "Readiness is measured live from the component tree, never assumed.",
            "Spawning is capped and fail-closed.",
        ],
        "gap": (
            "No container orchestrator. Replication is registry-level (an agent "
            "record plus a reporting edge), not a new isolated process."
        ),
    },
    {
        "key": "interface_ui",
        "order": 6,
        "name": "Interface and UI",
        "purpose": "How the goal arrives and how the result is surfaced.",
        "ours": [
            _module("orchestrator/server.py"),
            _module("orchestrator/dashboard.html"),
        ],
        "components": ["hyperframes", "LibreChat", "Open-LLM-VTuber"],
        "analogues": ["Next.js", "Vercel AI SDK", "Gradio", "Streamlit"],
        "obligations": [
            "A trigger is evidence, never proof of achievement.",
            "Every mutation is an authenticated local API call.",
            "The UI shows blockers verbatim; it does not smooth them over.",
        ],
        "gap": "Single local dashboard and JSON API; no public or multi-tenant surface.",
    },
)

LAYER_BY_KEY = {layer["key"]: layer for layer in LAYERS}


# ============================================================================
# Subplatforms as environments — the places agents operate
# ============================================================================

# The subplatform (component) is the *environment* an agent operates in:
# a scope (the component directory), toolchains, services, guardrails and
# posture. Live spec data is read by environment_profile() below; the names
# here are the environment taxonomy we design, realized by agents/specs/*.json
# and agents/registry.json.
ENVIRONMENTS: Tuple[Dict[str, Any], ...] = (
    {
        "component": "sentry",
        "name": "Sentry (deploy/hosting/security) environment",
        "kind": "deploy_host_security_sentry",
        "role": "Deployment, hosting and security sentry department agent: watches surfaces (IPFS/Cloudflare/Tailscale), hosting posture (gapinvex deploy configs, backups) and security posture (protected paths, spend authorizations, alerts). Reports findings to admin; never deploys, pays, or changes policy.",
        "tools": ["surface status probes", "gapinvex hosting facts", "security posture reads"],
        "posture": "watch and report; all movement requires operator/human approval",
    },
    {
        "component": "vibetrading",
        "name": "Vibetrading environment",
        "kind": "trading_zero_funding",
        "role": "Trade signal generation and execution sequencing, not fund management.",
        "tools": [
            "market data reads",
            "order/cancel via Pix-Payment-System (policy-gated)",
        ],
        "posture": "trading-only; live capital denied by default (0-funding posture)",
    },

    {
        "component": "AutoHedge",
        "name": "AutoHedge (hedge trading) environment",
        "kind": "hedge_trading",
        "role": "Hedge/risk position management; exposure is the unit of operation.",
        "tools": ["risk reads", "reposition via Pix-Payment-System (policy-gated)"],
        "posture": "live capital denied without operator authorization",
    },
    {
        "component": "spider-ai--trading-bot",
        "name": "Spider trading bot environment",
        "kind": "autonomous_trading_rust",
        "role": "Autonomous order execution from a rust-based bot; copy/auto-claim settings.",
        "tools": ["market data", "order ops (policy-gated)"],
        "services": ["mongodb (required)"],
        "posture": "live capital denied without operator authorization; MAX_ORDER_SIZE_USD / MAX_POSITION_SIZE_USD are environment-level caps",
    },
    {
        "component": "Pix-Payment-System",
        "name": "Payment rail environment",
        "kind": "payment_settlement",
        "role": "Payments and settlement rails. This is the environment money crosses.",
        "tools": ["payment/settlement calls (policy-gated: financial block by default)"],
        "posture": "deny settlement/withdrawal unless explicitly authorized; spend denied",
    },
    {
        "component": "FinceptTerminal",
        "name": "Fincept Terminal environment",
        "kind": "financial_terminal",
        "role": "Real-time data, research terminals; financial operations surface.",
        "tools": ["terminal API (cmake+Qt 6 toolchain; policies apply)"],
        "posture": "live capital denied by default",
    },
    {
        "component": "agentic-inbox",
        "name": "Agentic inbox environment",
        "kind": "intake_document_store",
        "role": "Email intake, routing, document and long-term memory store.",
        "tools": ["email reads/writes", "document ingestion"],
        "services": ["Cloudflare Workers (required)"],
        "posture": "quiet intake; outbound writes gated",
    },
    {
        "component": "agent-desktop",
        "name": "Desktop environment",
        "kind": "autonomous_desktop",
        "role": "See and operate desktop apps via the OS accessibility tree.",
        "tools": ["guarded shell + accessibility-tree reads/writes"],
        "posture": "destructive command patterns blocked; cwd must stay inside scope",
    },
    {
        "component": "LibreChat",
        "name": "Multi-persona chat environment",
        "kind": "persona_chat",
        "role": "Chat UI with multiple agents; tenant-scoped SaaS surface.",
        "tools": ["chat gateway (mongodb required, audit-log fail-closed)"],
        "posture": "tenant isolation enforced; audit log fail-closed",
    },
    {
        "component": "Open-LLM-VTuber",
        "name": "Personal assistant + media worker environment",
        "kind": "assistant_media_worker",
        "role": "Chat/voice assistant surface and media generation worker.",
        "tools": ["ollama endpoint (optional)", "image/video generation (policy-gated)"],
        "posture": "local LLM by default; generation commands gated",
    },
    {
        "component": "Scrapling",
        "name": "Retrieval & scraping environment",
        "kind": "data_pipeline",
        "role": "Web retrieval and structured data pipelines.",
        "tools": ["scraping/retrieval jobs"],
        "posture": "no outbound publishing; be selective",
    },
    {
        "component": "claude-ads",
        "name": "Evaluation / ads environment",
        "kind": "evaluator",
        "role": "Evaluate work, judge, run ads tooling. The independent verifier lives here.",
        "tools": ["import/build checks", "judge/verdict calls"],
        "posture": "read-only-ish; denial is final",
    },
    {
        "component": "grok-jev-router",
        "name": "Classifier / gateway environment",
        "kind": "gateway",
        "role": "Classify, route, decide. Carries no tools of its own.",
        "tools": [],
        "posture": "pure decision surface; every verdict is an input, not an attestation",
    },
    {
        "component": "freellmapi",
        "name": "LLM gateway environment",
        "kind": "model_gateway",
        "role": "Shared inference gateway for the platform (Cloudflare Workers/R2/D1 + ollama fallback).",
        "tools": ["LLM completions (proxy rate limits; webhook validation on by default)"],
        "posture": "proxy first, fallback order logged; no spend, no logging of raw secrets",
    },
    {
        "component": "hyperframes",
        "name": "Video render environment",
        "kind": "media_render",
        "role": "Render video/vfx from code-like inputs.",
        "tools": ["render jobs (policy-gated)"],
        "posture": "renders only; output must be reviewed before distribution",
    },
    {
        "component": "Open-Higgsfield-AI",
        "name": "Image/media generation environment",
        "kind": "media_generation",
        "role": "Generate images and media.",
        "tools": ["generation jobs (policy-gated)"],
        "posture": "generation-only; nothing about generated media is truth until humans review",
    },
)

ENV_BY_COMPONENT = {env["component"]: env for env in ENVIRONMENTS}


def environment_profile(component: str, readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Live profile of one environment: spec data + scope + runtime status."""
    from .agent_specs import load_spec
    from .registry import subplatforms

    env = dict(ENV_BY_COMPONENT.get(component, {
        "component": component,
        "name": component,
        "kind": "unclassified_environment",
        "role": "",
        "tools": [],
        "posture": "no declared posture",
    }))
    spec = load_spec(component) or {}
    sp_info = next((sp for sp in subplatforms() if sp.get("name") == component), {})
    spec_env = spec.get("environment") or {}
    env["scope"] = (spec_env.get("root") or sp_info.get("component") or component)
    env["spec"] = {
        "exists": bool(spec),
        "toolchains": spec_env.get("toolchains") or {},
        "services": [(s or {}).get("name", "") for s in spec_env.get("services", []) if s],
        "required_env": spec_env.get("required_env", []),
        "required_env_count": len(spec_env.get("required_env", [])),
        "capabilities": [c.get("name") for c in spec.get("capabilities", [])],
        "constitution_hash": (spec.get("constitution") or {}).get("sha256", "")[:12],
    }
    env["runtime_status"] = (readiness or {}).get(component) or "unknown"
    env["eagent_doc"] = sp_info.get("eagent")
    return env


def environments(readiness: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    return [environment_profile(env["component"], readiness) for env in ENVIRONMENTS]


def stage_environment(workflow: Any, stage: Any) -> List[str]:
    """Environments that may execute one stage: the environments of its
    components; the replication source's environment when the stage has none."""
    keys: List[str] = []
    for name in getattr(stage, "components", ()) or ():
        if name in ENV_BY_COMPONENT and name not in keys:
            keys.append(name)
    if not keys:
        source = getattr(workflow, "replication_source", None)
        if source in ENV_BY_COMPONENT:
            keys.append(source)
    return keys


# ============================================================================
# The agent loop
# ============================================================================

AGENT_LOOP: Tuple[Dict[str, Any], ...] = (
    {
        "step": 1,
        "key": "goal",
        "name": "Goal",
        "purpose": "A user or trigger states an outcome, not a step list.",
        "ours": "POST /tasks, POST /workflows/execute, cron/watchdog triggers",
        "produces": "goal text + context",
        "fail_closed": "An empty or unparsable goal is rejected before any run exists.",
    },
    {
        "step": 2,
        "key": "interface_environment",
        "name": "Interface + environment join",
        "purpose": "Normalize the entry point (HTTP, dashboard, webhook, schedule) and mount the agent in its subplatform environment: spec validated, scope set, guardrails loaded.",
        "ours": "orchestrator/server.py adapters + agents/specs/*.json + policy.py",
        "produces": "normalized request, caller identity, environment scope",
        "fail_closed": "Unknown caller, missing/stale spec or unscoped cwd is refused by policy.",
    },
    {
        "step": 3,
        "key": "plan",
        "name": "Plan",
        "purpose": "Select a workflow pattern and build the stage DAG.",
        "ours": "workflows.select_workflow + build_run",
        "produces": "DAG, gates, depth contracts",
        "fail_closed": "No matching pattern falls back to Plan and Execute and says so.",
    },
    {
        "step": 4,
        "key": "reason",
        "name": "Reason and select",
        "purpose": "The model reasons over context and selects tools.",
        "ours": "component-level (freellmapi / Open-LLM-VTuber); attested here",
        "produces": "proposed action + tool choice",
        "fail_closed": "A stage with no declared model path cannot attest reasoning.",
    },
    {
        "step": 5,
        "key": "tools",
        "name": "Tools via MCP",
        "purpose": "Act in the real world through declared tools.",
        "ours": "mcp.py shared server + tools.py + policy.evaluate",
        "produces": "tool result + policy decision",
        "fail_closed": "Policy denial stops the stage and is reported as blocked.",
    },
    {
        "step": 6,
        "key": "memory",
        "name": "Memory",
        "purpose": "Retrieve relevant history and store what must persist.",
        "ours": "store.py, geo.py, skills.py",
        "produces": "retrieved context + persisted record",
        "fail_closed": "A write to a sensitive product without a contract is refused.",
    },
    {
        "step": 7,
        "key": "result",
        "name": "Result",
        "purpose": "Produce an answer or artifact with provenance.",
        "ours": "run.steps[].output + reports",
        "produces": "stage output + citations",
        "fail_closed": "An output without provenance is not accepted as complete.",
    },
    {
        "step": 8,
        "key": "evaluate",
        "name": "Evaluate",
        "purpose": "Check the result against the stage's success criteria.",
        "ours": "gate_decision + independent verifier + caller attestation",
        "produces": "accept / reject / needs human",
        "fail_closed": "The engine never infers achievement; the caller must attest it.",
    },
    {
        "step": 9,
        "key": "continue",
        "name": "Continue or stop",
        "purpose": "Iterate, escalate, or stop within an explicit budget.",
        "ours": "loop contract per stage (max_iterations, token/time/tool budgets)",
        "produces": "next iteration or terminal state + stop reason",
        "fail_closed": "Budget exhaustion or a repeated rejection halts; it never loops forever.",
    },
)


# ============================================================================
# Protocol and semantic layers
# ============================================================================

PROTOCOL_LAYER: Dict[str, Any] = {
    "mcp": {
        "status": "implemented",
        "ours": "orchestrator/mcp.py - one shared server, tools/resources/prompts, central rate limits",
        "covers": ["tool schemas", "data resources", "prompt templates", "rate limiting"],
        "gap": "Tool execution is still local; no external MCP transport is wired.",
    },
    "a2a": {
        "status": "not_implemented",
        "ours": "router.py + admin.py mediate agent-to-agent handoff",
        "covers": ["routed plan", "worker assignment", "reports-to-admin chain"],
        "gap": (
            "No peer agent-to-agent protocol, discovery, or capability negotiation. "
            "Handoffs are hierarchical, which is deliberate, but it is not A2A."
        ),
    },
    "function_calling": {
        "status": "implemented",
        "ours": "MCPTool.input_schema + policy.evaluate",
        "covers": ["declared arguments", "pre-execution authorization"],
        "gap": "No schema validation of model-proposed arguments before dispatch.",
    },
}

SEMANTIC_LAYER: Dict[str, Any] = {
    "purpose": "Give the work shared meaning: what entities exist, how they relate, which words mean what.",
    "departments": [
        {"key": "sentry", "name": "Sentry", "role": "Deployment, hosting and security sentry for the platform (deploy/hosting/security watch; reports findings to admin, never acts)."},
    ],
    "entities": [
        "agent", "component", "workflow", "compartment", "stage", "run",
        "report", "worker", "skill", "place", "work item", "route", "data product",
    ],
    "relationships": [
        "agent -> owns -> component",
        "agent -> reports_to -> admin",
        "run -> executes -> workflow",
        "run -> contains -> stage",
        "stage -> consumes -> data product",
        "stage -> verified_by -> agent",
        "compartment -> staffs -> department agent + admin agent",
        "replica -> derived_from -> source agent",
    ],
    "glossary": {
        "gate": "A stage whose result must be accepted by a verifier before downstream stages run.",
        "attestation": "A caller-supplied statement that the goal was achieved; never inferred.",
        "compartment": "An organizational domain with a remit, a department agent and an admin agent.",
        "replica": "An agent record derived from a source agent, with a reporting edge to admin.",
        "readiness": "Live status of a component: ready, needs_setup or blocked.",
        "data product": "A named, owned, contracted dataset that stages may consume.",
    },
    "context_rules": [
        "Only governed data products may enter a stage's context.",
        "Sensitive products require an explicit approval recorded on the run.",
        "Every retrieved item carries provenance: product, record id, timestamp.",
        "Context is budgeted per stage; overflow is summarized, never silently truncated.",
    ],
    "explainability": [
        "Pattern selection records matched keywords and alternatives.",
        "Compartment selection records score and rationale per candidate.",
        "Gate decisions record the blocking component or verifier.",
        "Cascade records why it was skipped, halted or capped.",
    ],
    "gaps": [
        "No ontology or knowledge graph store; relationships are code-level conventions.",
        "No business glossary data product; the glossary above is curated, not queryable.",
        "No vector embeddings, so 'similar meaning' is lexical today.",
    ],
}


# ============================================================================
# Data products (named, owned, contracted, classified)
# ============================================================================

def _probe_runs() -> Dict[str, Any]:
    from .store import list_runs
    runs = list_runs(limit=200)
    return {"count": len(runs), "sample": [r.get("id") for r in runs[:3]]}


def _probe_reports() -> Dict[str, Any]:
    from .store import list_reports
    reports = list_reports(limit=200)
    return {"count": len(reports), "path": str(REPORTS_DIR)}


def _probe_states() -> Dict[str, Any]:
    from .store import load_agent_states
    data = load_agent_states()
    return {"agents": len(data.get("agents") or {}), "admin": (data.get("admin") or {}).get("state")}


def _probe_specs() -> Dict[str, Any]:
    from .agent_specs import all_specs, constitution_sha256
    specs = all_specs()
    return {"count": len(specs), "constitution": bool(constitution_sha256())}


def _probe_registry() -> Dict[str, Any]:
    from .registry import subplatforms
    subs = subplatforms()
    return {"subplatforms": len(subs)}


def _probe_geo() -> Dict[str, Any]:
    from . import geo
    overview = geo.map_overview()
    return {"places": overview.get("places", 0), "automata": overview.get("automata", 0)}


def _probe_skills() -> Dict[str, Any]:
    from .skills import discover_skills
    skills = discover_skills()
    return {"count": len(skills)}


def _probe_mcp() -> Dict[str, Any]:
    from .mcp import get_mcp_registry
    reg = get_mcp_registry()
    return {"tools": len(reg.tools), "resources": len(reg.data), "prompts": len(reg.prompts)}


def _probe_catalogue() -> Dict[str, Any]:
    from . import workflows
    return {"workflows": len(workflows.WORKFLOWS), "compartments": len(workflows.COMPARTMENTS)}


def _probe_decisions() -> Dict[str, Any]:
    from .store import list_runs
    runs = list_runs(limit=200)
    with_decision = [r for r in runs if (r.get("meta") or {}).get("decision")]
    return {"count": len(with_decision), "of_runs": len(runs)}


DATA_PRODUCTS: Tuple[Dict[str, Any], ...] = (
    {
        "key": "runs",
        "name": "Runs",
        "owner": "orchestrator/admin",
        "classification": "internal",
        "contract": ["id", "task", "status", "steps", "result", "meta"],
        "retention": "kept until pruned by the operator",
        "consumers": ["dashboard", "observability", "workflow engine"],
        "probe": _probe_runs,
    },
    {
        "key": "reports",
        "name": "Reports",
        "owner": "admin",
        "classification": "internal",
        "contract": ["run_id", "agent", "from_state", "to_state", "message", "timestamp"],
        "retention": "append-only JSONL",
        "consumers": ["audit", "watchdog", "lineage"],
        "probe": _probe_reports,
    },
    {
        "key": "agent_states",
        "name": "Agent states",
        "owner": "admin",
        "classification": "internal",
        "contract": ["agents", "admin", "updated_at"],
        "retention": "current state only",
        "consumers": ["watchdog", "hierarchy", "dashboard"],
        "probe": _probe_states,
    },
    {
        "key": "agent_specs",
        "name": "Agent specs",
        "owner": "agents/registry",
        "classification": "public",
        "contract": ["name", "component", "capabilities", "guardrails", "constitution"],
        "retention": "regenerated from manifests",
        "consumers": ["dispatch", "policy", "workflow engine"],
        "probe": _probe_specs,
    },
    {
        "key": "registry",
        "name": "Subplatform registry",
        "owner": "agents/registry",
        "classification": "public",
        "contract": ["subplatforms", "name", "eagent", "component"],
        "retention": "versioned in git",
        "consumers": ["router", "components", "dashboard"],
        "probe": _probe_registry,
    },
    {
        "key": "geo",
        "name": "Geo records",
        "owner": "orchestrator/geo",
        "classification": "sensitive",
        "contract": ["places", "automata", "work_items", "routes"],
        "retention": "sqlite, operator-managed",
        "consumers": ["map overview", "route planning"],
        "probe": _probe_geo,
    },
    {
        "key": "skills",
        "name": "Skills repository",
        "owner": "orchestrator/skills",
        "classification": "internal",
        "contract": ["name", "meta", "body"],
        "retention": "files on disk",
        "consumers": ["workers", "dashboard"],
        "probe": _probe_skills,
    },
    {
        "key": "mcp_capabilities",
        "name": "MCP capabilities",
        "owner": "orchestrator/mcp",
        "classification": "internal",
        "contract": ["tools", "resources", "prompts", "rate_limits"],
        "retention": "in-process registry",
        "consumers": ["workers", "stages"],
        "probe": _probe_mcp,
    },
    {
        "key": "workflow_catalogue",
        "name": "Workflow catalogue",
        "owner": "orchestrator/workflows",
        "classification": "public",
        "contract": ["count", "workflows", "compartments", "anatomy"],
        "retention": "generated export",
        "consumers": ["dashboard", "API", "docs"],
        "probe": _probe_catalogue,
    },
    {
        "key": "decisions",
        "name": "Compartment decisions",
        "owner": "admin",
        "classification": "internal",
        "contract": ["candidates", "selected", "threshold", "rationale"],
        "retention": "recorded on each run",
        "consumers": ["audit", "replication", "dashboard"],
        "probe": _probe_decisions,
    },
)

DATA_PRODUCT_BY_KEY = {p["key"]: p for p in DATA_PRODUCTS}


def data_products() -> List[Dict[str, Any]]:
    """Live inventory: presence, shape and governance completeness."""
    out: List[Dict[str, Any]] = []
    for product in DATA_PRODUCTS:
        entry = {
            key: product[key]
            for key in ("key", "name", "owner", "classification", "contract", "retention", "consumers")
        }
        try:
            observed = product["probe"]()
            entry["observed"] = observed
            entry["available"] = True
        except Exception as exc:  # a probe failing is information, not a crash
            entry["observed"] = {"error": str(exc)}
            entry["available"] = False
        entry["governed"] = bool(
            entry["owner"] and entry["classification"] and entry["contract"] and entry["available"]
        )
        out.append(entry)
    return out


# ============================================================================
# Governance
# ============================================================================

def governance(readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    products = data_products()
    unclassified = [p["key"] for p in products if not p["classification"]]
    unavailable = [p["key"] for p in products if not p["available"]]
    sensitive = [p["key"] for p in products if p["classification"] == "sensitive"]
    audit_log = STATE_DIR / "audit.log"
    return {
        "principles": [
            "Data minimization: a stage receives only the products its context contract names.",
            "Purpose limitation: a product is used for the stage that declared it.",
            "Retention is declared per product, not implied.",
            "Sensitive products require an approval recorded on the run.",
        ],
        "access_model": {
            "status": "enforced",
            "rbac": "agent specs + registry scope (policy.evaluate, component_scope)",
            "abac": "guardrails per agent: denied_commands, allowed_paths, spend, live_capital",
            "ours": "orchestrator/policy.py, orchestrator/agent_specs.py",
        },
        "audit": {
            "status": "append_only" if REPORTS_DIR.exists() else "missing",
            "ours": "reports/*.jsonl + run records",
            "note": "audit.log is referenced by policy as a protected path; reports are the live trail.",
            "audit_log_present": audit_log.exists(),
        },
        "privacy": {
            "status": "partial" if (unclassified or sensitive) else "declared",
            "unclassified": unclassified,
            "sensitive": sensitive,
            "redaction": "admin._safe_meta strips payload secrets before persisting run meta",
        },
        "quality": {
            "status": "measured",
            "ours": "components.readiness + gate decisions",
            "unavailable_products": unavailable,
        },
        "lineage": {
            "status": "recorded",
            "chain": "run -> decision -> stage -> verifier -> cascade -> replica",
            "ours": "lineage_of(run) + reports",
        },
    }


def lineage_of(run: Dict[str, Any]) -> Dict[str, Any]:
    """Explain one run as a chain, without inventing missing links."""
    meta = run.get("meta") or {}
    result = run.get("result") or {}
    stages = []
    for step in run.get("steps") or []:
        out = step.get("output") or {}
        stages.append({
            "title": step.get("title"),
            "status": step.get("status"),
            "component": step.get("component"),
            "gate": bool(out.get("gate")),
            "verifier": (out.get("gate_decision") or {}).get("verifier"),
            "reason": out.get("reason"),
        })
    cascade = result.get("replication_cascade") or []
    return {
        "run_id": run.get("id"),
        "goal": run.get("task"),
        "workflow": (result.get("workflow") or {}).get("name") or meta.get("workflow"),
        "decision": {
            "selected": (meta.get("decision") or {}).get("selected"),
            "threshold": (meta.get("decision") or {}).get("threshold"),
        },
        "stages": stages,
        "cascade": [
            {"order": c.get("order"), "compartment": c.get("compartment"), "state": c.get("state")}
            for c in cascade
        ],
        "attested": bool(result.get("achievement_attested")),
        "status": run.get("status"),
    }


# ============================================================================
# Production layer (runs under every agent)
# ============================================================================

def production_layer(readiness: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    readiness = readiness or {}
    return [
        {
            "concern": "Observability",
            "ours": "orchestrator/observability.py + reports",
            "analogues": ["LangSmith", "Langfuse", "OpenTelemetry"],
            "obligation": "Every state transition is recorded and alerts are surfaced, not hidden.",
            "status": "realized" if _exists("orchestrator/observability.py") else "missing",
        },
        {
            "concern": "Evaluation",
            "ours": "workflow gates + independent verifier + caller attestation",
            "analogues": ["Braintrust", "DeepEval"],
            "obligation": "Only a verifier-accepted result plus a caller attestation may trigger replication.",
            "status": "realized",
        },
        {
            "concern": "Guardrails",
            "ours": "orchestrator/policy.py",
            "analogues": ["Guardrails AI", "NeMo Guardrails"],
            "obligation": "Policy denial is final and is recorded as blocked.",
            "status": "realized" if _exists("orchestrator/policy.py") else "missing",
        },
        {
            "concern": "Agent identity",
            "ours": "orchestrator/agent_specs.py (constitution hash bound to every spec)",
            "analogues": ["Auth0", "OAuth", "Entra Agent ID"],
            "obligation": "An agent without a valid, current spec cannot be dispatched.",
            "status": "realized" if _exists("orchestrator/agent_specs.py") else "missing",
        },
        {
            "concern": "LLM gateway",
            "ours": "freellmapi + grok-jev-router components",
            "analogues": ["Portkey", "LiteLLM", "OpenRouter"],
            "obligation": "Routing and fallback are recorded per run.",
            "status": "gap" if readiness.get("freellmapi") in (None, "blocked", "needs_setup") else "realized",
        },
    ]


# ============================================================================
# Engineering layers per stage: prompt, context, harness, loop
# ============================================================================

KINDS: Tuple[str, ...] = (
    "context_assembly",
    "decision",
    "generation",
    "verification",
    "action",
    "memory_write",
)

KIND_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("context_assembly", ("intake", "split", "embed", "retrieve", "observe", "scrape", "gather")),
    ("decision", ("plan", "route", "select", "match", "orchestr", "subplan", "handoff")),
    ("action", ("execute", "act", "tool", "pause", "resume", "store", "update", "memory")),
    ("verification", (
        "verify", "evaluate", "judge", "review", "validate", "feedback", "monitor",
        "consensus", "aggregate", "reduce", "synthesize", "combine",
    )),
)

VERIFIER_COMPONENTS: Tuple[str, ...] = ("claude-ads", "grok-jev-router", "AutoHedge")

KIND_PROFILES: Dict[str, Dict[str, Any]] = {
    "context_assembly": {
        "token_budget": 8000,
        "max_iterations": 1,
        "max_tool_calls": 4,
        "tools": ["mcp:resources.read", "files.read(scoped)"],
        "context": ["runs", "reports", "mcp_capabilities"],
        "criteria": "context assembled from governed products with provenance for every item",
        "compression": "summarize over budget; keep ids and timestamps",
        "citation_required": True,
    },
    "decision": {
        "token_budget": 3000,
        "max_iterations": 2,
        "max_tool_calls": 3,
        "tools": ["mcp:tools.list", "mcp:prompts.get"],
        "context": ["workflow_catalogue", "agent_specs", "decisions"],
        "criteria": "a single path chosen and recorded with matched evidence",
        "compression": "rank options, keep the top three",
        "citation_required": False,
    },
    "generation": {
        "token_budget": 4000,
        "max_iterations": 1,
        "max_tool_calls": 2,
        "tools": ["mcp:prompts.get"],
        "context": ["runs", "skills"],
        "criteria": "output matches the stage's declared schema and cites its inputs",
        "compression": "keep the last two attempts, not the whole loop",
        "citation_required": True,
    },
    "verification": {
        "token_budget": 2000,
        "max_iterations": 2,
        "max_tool_calls": 3,
        "tools": ["mcp:tools.call", "files.read(scoped)"],
        "context": ["reports", "runs"],
        "criteria": "accept or reject against explicit criteria; rejection states the failed criterion",
        "compression": "keep the failing evidence only",
        "citation_required": True,
    },
    "action": {
        "token_budget": 1500,
        "max_iterations": 2,
        "max_tool_calls": 5,
        "tools": ["mcp:tools.call", "shell(policy.evaluate)", "files.write(scoped)"],
        "context": ["agent_specs", "mcp_capabilities"],
        "criteria": "the tool call succeeded and its result is recorded",
        "compression": "never compress tool output; truncate with a pointer",
        "citation_required": False,
    },
    "memory_write": {
        "token_budget": 1200,
        "max_iterations": 1,
        "max_tool_calls": 2,
        "tools": ["mcp:resources.write", "files.write(scoped)"],
        "context": ["agent_states", "reports"],
        "criteria": "the record is persisted with a contract-valid shape",
        "compression": "store the delta, not the transcript",
        "citation_required": False,
    },
}

STOP_CONDITIONS: Tuple[str, ...] = (
    "success_criteria_met",
    "budget_exhausted",
    "policy_denied",
    "verifier_rejects_limit",
    "human_required",
    "upstream_gate_closed",
)


def stage_kind(stage: Any) -> str:
    key = f"{getattr(stage, 'key', '')} {getattr(stage, 'label', '')}".lower()
    for kind, needles in KIND_RULES:
        if any(n in key for n in needles):
            return kind
    return "generation"


def _verifier_for(stage: Any) -> Dict[str, Any]:
    components = list(getattr(stage, "components", ()) or ())
    if getattr(stage, "gate", False):
        for name in components:
            if name in VERIFIER_COMPONENTS:
                return {"mode": "component", "name": name, "independent": True}
        return {
            "mode": "admin",
            "name": "admin",
            "independent": True,
            "note": "no evaluator component in the stage, so the admin attests via caller attestation",
        }
    return {"mode": "self_check", "name": "stage", "independent": False}


def stage_depth(workflow: Any, stage: Any) -> Dict[str, Any]:
    """The four engineering layers, specified for one stage.

    This is a contract, not a suggestion: the loop budget and stop
    conditions are carried into the run so exhaustion is observable.
    """
    kind = stage_kind(stage)
    profile = KIND_PROFILES[kind]
    verifier = _verifier_for(stage)
    label = getattr(stage, "label", getattr(stage, "key", "stage"))
    achievement = getattr(workflow, "achievement_point", "the stage result is accepted by a verifier")
    criteria = [profile["criteria"]]
    if getattr(stage, "gate", False):
        criteria.append(f"independent verifier accepts: {verifier['name']}")
    criteria.append(f"contributes to: {achievement}")
    return {
        "kind": kind,
        "prompt": {
            "role": f"{label} worker for workflow {getattr(workflow, 'name', '')}".strip(),
            "instruction": (
                f"Do the {kind.replace('_', ' ')} step for '{label}'. "
                "Return only the declared shape; state uncertainty instead of guessing."
            ),
            "output_schema": {
                "result": "string|object",
                "evidence": "list[provenance]",
                "confidence": "low|medium|high",
                "next": "continue|escalate|stop",
            },
            "examples": "governed-only (no unclassified data in few-shot examples)",
        },
        "context": {
            "sources": list(profile["context"]),
            "token_budget": profile["token_budget"],
            "compression": profile["compression"],
            "citation_required": profile["citation_required"],
            "provenance_required": True,
        },
        "harness": {
            "tools": list(profile["tools"]),
            "policy_gate": "orchestrator/policy.py before any tool or shell call",
            "components": list(getattr(stage, "components", ()) or ()),
            "environment": stage_environment(workflow, stage),
            "verifier": verifier,
            "max_tool_calls": profile["max_tool_calls"],
        },
        "loop": {
            "success_criteria": criteria,
            "max_iterations": profile["max_iterations"],
            "budget": {
                "tokens": profile["token_budget"],
                "tool_calls": profile["max_tool_calls"],
                "seconds": 120 * profile["max_iterations"],
            },
            "stop_conditions": list(STOP_CONDITIONS),
            "on_budget_exhausted": "halt and report budget_exhausted; never continue silently",
            "escalation": "human_required when a gate cannot be independently verified",
        },
        "status": "specified",
    }


def _contract_gaps(depth: Dict[str, Any]) -> List[str]:
    gaps: List[str] = []
    loop = depth["loop"]
    if not loop["success_criteria"]:
        gaps.append("no success criteria")
    if loop["max_iterations"] < 1:
        gaps.append("no iteration budget")
    if loop["budget"]["tokens"] <= 0 or loop["budget"]["tool_calls"] <= 0:
        gaps.append("empty budget")
    if not loop["stop_conditions"]:
        gaps.append("no stop conditions")
    if not depth["harness"]["components"]:
        gaps.append("no component assigned")
    if depth["harness"]["verifier"]["mode"] == "self_check" and not depth["harness"]["verifier"]["name"]:
        gaps.append("no verifier")
    if depth["context"]["citation_required"] and not depth["context"]["sources"]:
        gaps.append("citations required but no context source")
    return gaps


def depth_readiness(workflow: Any, readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Validate every stage's depth contract and verifier availability.

    A gate whose independent verifier is blocked is closed. That is a
    different failure from the producer being blocked, and it is reported
    separately so the fix is obvious.
    """
    readiness = readiness or {}
    stages: List[Dict[str, Any]] = []
    incomplete: List[str] = []
    closed: List[str] = []
    for stage in getattr(workflow, "stages", ()) or ():
        depth = stage_depth(workflow, stage)
        gaps = _contract_gaps(depth)
        verifier = depth["harness"]["verifier"]
        verifier_status = readiness.get(verifier["name"]) if verifier["mode"] == "component" else "n/a"
        blocked = verifier["mode"] == "component" and verifier_status == "blocked"
        if gaps:
            incomplete.append(getattr(stage, "key", "stage"))
        if blocked:
            closed.append(getattr(stage, "key", "stage"))
        stages.append({
            "stage": getattr(stage, "key", "stage"),
            "label": getattr(stage, "label", ""),
            "kind": depth["kind"],
            "verifier": verifier["name"],
            "verifier_mode": verifier["mode"],
            "verifier_status": verifier_status,
            "contract_gaps": gaps,
            "status": "closed" if blocked else ("incomplete" if gaps else "ready"),
        })
    status = "closed" if closed else ("incomplete" if incomplete else "ready")
    return {
        "status": status,
        "stages": stages,
        "incomplete": incomplete,
        "closed": closed,
        "note": (
            "Depth is a contract: role, context sources, tool authorization, verifier, "
            "success criteria, budget and stop conditions must all be specified."
        ),
    }


# ============================================================================
# Authority for a replication (what a spawned compartment may touch)
# ============================================================================

def compartment_authority(
    workflow: Any,
    compartment: Any,
    item: Dict[str, Any],
    readiness: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Authorize one compartment spawn against data contracts and gates.

    Fail-closed: no verification gate, a blocked verifier, or sensitive data
    without approval refuses the spawn instead of spawning something unauditable.
    """
    readiness = readiness or {}
    blockers: List[str] = []
    environments_seen: List[Dict[str, Any]] = []
    seen_env_keys: List[str] = []
    for stage in getattr(workflow, "stages", ()) or ():
        for key in stage_environment(workflow, stage):
            if key in seen_env_keys:
                continue
            seen_env_keys.append(key)
            env = environment_profile(key, readiness)
            environments_seen.append({
                "component": key,
                "name": env["name"],
                "scope": env["scope"],
                "posture": env["posture"],
            })
    if not environments_seen:
        blockers.append("no environment: the workflow references no subplatform")

    gate_stages = [s for s in getattr(workflow, "stages", ()) or () if getattr(s, "gate", False)]
    gates = []
    for stage in gate_stages:
        verifier = _verifier_for(stage)
        gates.append({
            "stage": getattr(stage, "key", ""),
            "verifier": verifier["name"],
            "mode": verifier["mode"],
        })
        if verifier["mode"] == "component" and readiness.get(verifier["name"]) == "blocked":
            blockers.append(f"verifier {verifier['name']} is blocked")
    if not gate_stages:
        blockers.append("workflow has no verification gate; achievement cannot be attested")

    products: List[Dict[str, Any]] = []
    for stage in getattr(workflow, "stages", ()) or ():
        for key in stage_depth(workflow, stage)["context"]["sources"]:
            product = DATA_PRODUCT_BY_KEY.get(key)
            if not product or any(p["key"] == key for p in products):
                continue
            sensitive = product["classification"] == "sensitive"
            products.append({
                "key": key,
                "name": product["name"],
                "classification": product["classification"],
                "approval_required": sensitive,
                "approved": False,
            })
            if sensitive:
                blockers.append(f"sensitive product '{key}' requires recorded approval")

    return {
        "compartment": item.get("compartment"),
        "order": item.get("order"),
        "department": item.get("department"),
        "admin": item.get("admin"),
        "reports_to": "admin",
        "environments": environments_seen,
        "environment_transfer": {
            "rule": "the spawned department agent runs in its subplatform environment; handoffs between environments are mediated by the Admin Top Layer and recorded on the run",
            "recording": "run.steps[].output.environment + authority.environments",
        },
        "gates": gates,
        "data_contracts": products,
        "tool_authorization": {
            "policy": "orchestrator/policy.py",
            "scope": "agent_specs.component_scope + allowed_paths",
            "denied_by_default": ["settlement", "withdrawal", "live trading"],
        },
        "blockers": blockers,
        "spawn_allowed": not blockers,
    }


# ============================================================================
# Snapshot (what the API and UI read)
# ============================================================================

def _layer_status(layer: Dict[str, Any], readiness: Dict[str, str]) -> Dict[str, Any]:
    ours = layer.get("ours") or []
    missing = [m["path"] for m in ours if not m["exists"]]
    comps = {name: readiness.get(name) for name in layer.get("components", [])}
    blocked = [n for n, s in comps.items() if s == "blocked"]
    if missing:
        status = "partial" if len(missing) < len(ours) else "missing"
    elif blocked:
        status = "gated"
    else:
        status = "realized"
    return {"status": status, "missing": missing, "components": comps, "blocked_components": blocked}


def stack_snapshot(readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    readiness = readiness or {}
    layers = []
    for layer in LAYERS:
        entry = {k: layer[k] for k in ("key", "order", "name", "purpose", "ours", "components", "analogues", "obligations", "gap")}
        entry.update(_layer_status(layer, readiness))
        layers.append(entry)
    return {
        "note": (
            "Vendor names are orientation only. Nothing here depends on them; "
            "every layer is realized by modules in this repository or reported as a gap."
        ),
        "layers": layers,
        "agent_loop": list(AGENT_LOOP),
        "protocol": PROTOCOL_LAYER,
        "semantic": SEMANTIC_LAYER,
        "environments": environments(readiness),
        "production": production_layer(readiness),
        "data_products": data_products(),
        "governance": governance(readiness),
        "gaps": gaps(),
        "engineering": {
            "layers": ["prompt", "context", "harness", "loop"],
            "contract_required": [
                "role + output schema",
                "governed context sources + token budget",
                "tool authorization + verifier",
                "success criteria + iteration/token/time budgets + stop conditions",
            ],
            "kinds": list(KINDS),
            "stop_conditions": list(STOP_CONDITIONS),
        },
    }


def summary(readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Compact view for catalogue payloads (counts, not full inventories)."""
    snapshot = stack_snapshot(readiness)
    return {
        "layers": [
            {"key": l["key"], "name": l["name"], "status": l["status"]} for l in snapshot["layers"]
        ],
        "environments": [
            {
                "component": e["component"], "name": e["name"],
                "scope": e["scope"], "posture": e["posture"], "spec": e["spec"],
            }
            for e in snapshot["environments"]
        ],
        "loop_steps": len(snapshot["agent_loop"]),
        "production": [
            {"concern": c["concern"], "status": c["status"]} for c in snapshot["production"]
        ],
        "protocol": {k: v["status"] for k, v in snapshot["protocol"].items()},
        "semantic_gaps": len(snapshot["semantic"]["gaps"]),
        "data_products": {
            "count": len(snapshot["data_products"]),
            "governed": sum(1 for p in snapshot["data_products"] if p["governed"]),
            "unavailable": [p["key"] for p in snapshot["data_products"] if not p["available"]],
        },
        "governance": {
            "access": snapshot["governance"]["access_model"]["status"],
            "audit": snapshot["governance"]["audit"]["status"],
            "privacy": snapshot["governance"]["privacy"]["status"],
            "lineage": snapshot["governance"]["lineage"]["status"],
        },
        "engineering_kinds": snapshot["engineering"]["kinds"],
    }


def gaps() -> List[Dict[str, str]]:
    """The honest gap list, in one place."""
    out: List[Dict[str, str]] = []
    for layer in LAYERS:
        if layer.get("gap"):
            out.append({"layer": layer["name"], "gap": layer["gap"]})
    for key, proto in PROTOCOL_LAYER.items():
        if proto["status"] != "implemented":
            out.append({"layer": f"protocol:{key}", "gap": proto["gap"]})
    for gap in SEMANTIC_LAYER["gaps"]:
        out.append({"layer": "semantic", "gap": gap})
    return out
