"""Catalogue of agentic LLM workflows + compartment decision + replication engine.

The 20 workflow patterns are **compartment-agnostic**: a pattern describes how
work gets done (stages, gates, components), never *where* in the organization it
belongs. Where it belongs is a separate, inspectable decision
(:func:`decide_compartments`), made per goal, per run.

Two rules keep this honest:

* **No autonomy theatre.** The engine never infers "goal achieved". Achievement
  is an input (``achieved=True``) supplied by the caller after evaluation. No
  model calls and no shell commands happen here; the engine builds, gates, and
  records.
* **Fail-closed, end to end.** A closed stage gate stops the DAG; a missing
  achievement attestation stops replication; a compartment below the necessity
  threshold is not spawned; the first failed spawn halts the cascade.

The compartment decision is a seam, not a hardcoded table. The default decider
is deterministic and explainable (keyword/evidence scoring). A caller can supply
its own decision — which is where an orchestrating agent (or an LLM-backed
decider) makes the call — and the engine will enforce it fail-closed.

CLI::

    python -m orchestrator.workflows --list
    python -m orchestrator.workflows --select "summarize 400 support tickets"
    python -m orchestrator.workflows --decide "audit the trading policy for legal risk"
    python -m orchestrator.workflows --export
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import stack
from .config import PLATFORM_ROOT

CATALOGUE_PATH = PLATFORM_ROOT / "workflows" / "catalogue.json"

# Default necessity threshold. A goal keyword hit scores 2.0, so replication
# requires actual evidence that a compartment is implicated; generic capability
# overlap alone (0.5 per hit) cannot cross the line.
DEFAULT_THRESHOLD = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# Compartments: fixed topology, no fixed occupants
# ============================================================================

@dataclass(frozen=True)
class Compartment:
    key: str
    name: str
    short: str
    order: int
    remit: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "short": self.short,
            "order": self.order,
            "remit": list(self.remit),
        }


COMPARTMENTS: Tuple[Compartment, ...] = (
    Compartment(
        "social_welfare", "Social and Welfare", "Social Welfare", 1,
        ("welfare", "benefit", "care", "housing", "health", "community", "family",
         "shelter", "disaster", "poverty", "child", "elder", "caseworker", "social"),
    ),
    Compartment(
        "business_trade", "Business and Trade", "Business Trade", 2,
        ("market", "trade", "supply", "vendor", "payment", "logistics", "procurement",
         "contract", "finance", "financial", "pricing", "inventory", "business",
         "commerce", "settlement"),
    ),
    Compartment(
        "politics_governance", "Politics and Governance", "Politics Governance", 3,
        ("policy", "law", "legal", "legislation", "election", "governance", "regulation",
         "compliance", "municipal", "treaty", "budget", "constituent", "court", "statutory"),
    ),
    Compartment(
        "science_research", "Science and Research", "Science Research", 4,
        ("research", "experiment", "data", "model", "hypothesis", "study", "patent",
         "clinical", "simulation", "proof", "science", "scientific", "analysis", "evidence"),
    ),
    Compartment(
        "religious_mystical", "Religious and Mystical Organization", "Religious Mystical", 5,
        ("doctrine", "ritual", "pastoral", "scripture", "congregation", "pilgrim",
         "spiritual", "canon", "theolog", "sacred", "clergy", "faith"),
    ),
)

COMPARTMENT_BY_KEY = {c.key: c for c in COMPARTMENTS}
COMPARTMENT_BY_NAME = {c.name.lower(): c for c in COMPARTMENTS}


# ============================================================================
# Fused repository components (declared posture from the catalogue)
# ============================================================================

@dataclass(frozen=True)
class ComponentNote:
    name: str
    declared_status: str  # ready | needs_setup | blocked
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "declared_status": self.declared_status, "note": self.note}


COMPONENT_NOTES: Tuple[ComponentNote, ...] = (
    ComponentNote("agentic-inbox", "ready", "Intake, routing, and long-term document/memory store."),
    ComponentNote("grok-jev-router", "needs_setup", "Primary dispatcher/classifier; JEV_OFFLINE=1 uses the bundled local evaluator, paid use needs TYPESAFE_API_KEY."),
    ComponentNote("freellmapi", "blocked", "Shared free LLM worker pool; needs npm install in the shared tree."),
    ComponentNote("Scrapling", "needs_setup", "Retrieval, embedding, and scraping workers; needs orjson via uv sync."),
    ComponentNote("vibetrading", "needs_setup", "Market/signal branches; needs pandas via uv sync."),
    ComponentNote("AutoHedge", "needs_setup", "Hedge/monitoring branches; needs python-dotenv plus full requirements."),
    ComponentNote("Open-LLM-VTuber", "needs_setup", "Conversational/voice worker and reasoning surface; needs a Python venv with deps."),
    ComponentNote("claude-ads", "needs_setup", "Evaluator/critic worker; needs a local venv."),
    ComponentNote("agent-desktop", "blocked", "Worker shell / computer-use environment; needs Rust toolchain plus npm install."),
    ComponentNote("LibreChat", "blocked", "Human-facing chat, short-term memory, multi-persona; needs npm/Bun dependencies."),
    ComponentNote("Open-Higgsfield-AI", "blocked", "Media-generation worker; needs npm ci."),
    ComponentNote("hyperframes", "blocked", "Video-render worker; needs npm install plus Bun."),
    ComponentNote("FinceptTerminal", "blocked", "Financial terminal environment/tool; needs CMake plus the C++/Qt toolchain."),
    ComponentNote("Pix-Payment-System", "needs_setup", "Payment/settlement tool rail; Java needs JDK/Maven, Rust needs cargo."),
)

COMPONENT_BY_NAME = {c.name: c for c in COMPONENT_NOTES}
STATUS_RANK = {"ready": 0, "needs_setup": 1, "blocked": 2}


# ============================================================================
# Workflow model (pattern only — no compartment assignments)
# ============================================================================

@dataclass(frozen=True)
class Stage:
    """One node of a workflow DAG."""

    key: str
    label: str
    components: Tuple[str, ...]
    gate: bool = False

    def to_dict(self, readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        readiness = readiness or {}
        comps = []
        for name in self.components:
            note = COMPONENT_BY_NAME.get(name)
            comps.append({
                "name": name,
                "declared_status": note.declared_status if note else "unknown",
                "live_status": readiness.get(name),
                "note": note.note if note else "",
            })
        return {
            "key": self.key,
            "label": self.label,
            "gate": self.gate,
            "components": comps,
            "primary_component": self.components[0] if self.components else None,
        }


@dataclass(frozen=True)
class Workflow:
    id: int
    slug: str
    name: str
    description: str
    best_for: Tuple[str, ...]
    mechanism: Tuple[str, ...]
    achievement_point: str
    stages: Tuple[Stage, ...]
    replication_source: str
    keywords: Tuple[str, ...]
    domain: Tuple[str, ...]
    department_kind: str
    admin_kind: str

    def referenced_components(self) -> List[str]:
        seen: List[str] = []
        for stage in self.stages:
            for name in stage.components:
                if name not in seen:
                    seen.append(name)
        if self.replication_source not in seen:
            seen.append(self.replication_source)
        return seen

    def to_dict(self, readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        readiness = readiness or {}
        refs = self.referenced_components()
        worst = "ready"
        for name in refs:
            note = COMPONENT_BY_NAME.get(name)
            status = (readiness.get(name) or (note.declared_status if note else "ready"))
            if STATUS_RANK.get(status, 0) > STATUS_RANK.get(worst, 0):
                worst = status
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "best_for": list(self.best_for),
            "mechanism": list(self.mechanism),
            "achievement_point": self.achievement_point,
            "stages": [s.to_dict(readiness) for s in self.stages],
            "replication_source": self.replication_source,
            "referenced_components": refs,
            "readiness": worst,
            "domain": list(self.domain),
            "department_kind": self.department_kind,
            "admin_kind": self.admin_kind,
            "compartment_agnostic": True,
        }


def _wf(
    id: int,
    slug: str,
    name: str,
    description: str,
    best_for: Sequence[str],
    mechanism: Sequence[str],
    achievement_point: str,
    stages: Sequence[Stage],
    replication_source: str,
    keywords: Sequence[str],
    domain: Sequence[str],
    department_kind: str,
    admin_kind: str,
) -> Workflow:
    return Workflow(
        id=id,
        slug=slug,
        name=name,
        description=description,
        best_for=tuple(best_for),
        mechanism=tuple(mechanism),
        achievement_point=achievement_point,
        stages=tuple(stages),
        replication_source=replication_source,
        keywords=tuple(keywords),
        domain=tuple(domain),
        department_kind=department_kind,
        admin_kind=admin_kind,
    )


# ============================================================================
# The 20 workflows
# ============================================================================

WORKFLOWS: Tuple[Workflow, ...] = (
    _wf(
        1, "prompt-chaining", "Prompt Chaining",
        "Decomposes a complex task into sequential steps where each LLM call processes the output of the previous step.",
        ("Chatbot applications", "Tool-using AI agents"),
        ("Input", "LLM call", "Gate/validation", "LLM call", "Output"),
        "the terminal LLM call's output is evaluated by the caller",
        (
            Stage("intake", "Intake and routing", ("agentic-inbox",)),
            Stage("reason", "First LLM call", ("freellmapi",)),
            Stage("validate", "Gate / validation", ("grok-jev-router",), gate=True),
            Stage("reason2", "Second LLM call", ("freellmapi",)),
            Stage("retrieve", "Retrieval support", ("Scrapling",)),
        ),
        "agentic-inbox",
        ("prompt chaining", "chain", "sequential", "step by step", "pipeline", "chatbot"),
        ("workflow", "chain", "process", "pipeline", "chatbot"),
        "Prompt Chain", "Output Validation",
    ),
    _wf(
        2, "parallelization", "Parallelization",
        "Runs multiple LLM calls simultaneously and combines or aggregates the outputs for a final result.",
        ("Guardrails", "Automated evaluations"),
        ("Input", "Parallel LLM calls", "Aggregator", "Output"),
        "the aggregator's threshold result is evaluated by the caller",
        (
            Stage("fanout", "Parallel branches", ("vibetrading", "AutoHedge", "Open-LLM-VTuber", "Scrapling")),
            Stage("aggregate", "Aggregator / threshold", ("grok-jev-router",), gate=True),
        ),
        "AutoHedge",
        ("parallel", "simultaneous", "guardrail", "evaluation", "fan out", "aggregate"),
        ("evaluation", "guardrail", "monitoring", "market", "signal"),
        "Parallel Evaluation", "Threshold Review",
    ),
    _wf(
        3, "orchestrator-worker", "Orchestrator-Worker",
        "A central LLM breaks down tasks and delegates them to multiple independent worker LLMs.",
        ("Agentic RAG", "Coding agents"),
        ("Input", "Orchestrator", "Worker LLMs (parallel)", "Synthesizer", "Output"),
        "the orchestrator's synthesized result is evaluated by the caller",
        (
            Stage("intake", "Task intake", ("agentic-inbox",)),
            Stage("orchestrate", "Orchestrator", ("grok-jev-router",)),
            Stage("workers", "Worker pool", ("agent-desktop", "freellmapi", "LibreChat", "Open-Higgsfield-AI")),
            Stage("synthesize", "Synthesizer", ("Open-LLM-VTuber",), gate=True),
        ),
        "agent-desktop",
        ("orchestrator", "worker", "agentic rag", "coding agent", "delegate", "synthesize"),
        ("research", "coding", "rag", "delegation", "synthesis"),
        "Worker Orchestration", "Synthesis Review",
    ),
    _wf(
        4, "evaluator-optimizer", "Evaluator-Optimizer",
        "One model generates output while another evaluates it and suggests improvements in a continuous loop.",
        ("Code generation agents", "Real-time data monitoring"),
        ("Generator", "Response", "Evaluator", "Loop back on rejection", "Output"),
        "the evaluator's acceptance decision is the attestation point",
        (
            Stage("generate", "Generator", ("Open-LLM-VTuber", "freellmapi")),
            Stage("evaluate", "Evaluator / acceptance", ("claude-ads", "grok-jev-router"), gate=True),
        ),
        "claude-ads",
        ("evaluator", "optimizer", "critique", "revise", "accept", "reject", "quality loop"),
        ("quality", "code", "monitoring", "review", "improvement"),
        "Generation and Review", "Acceptance Gate",
    ),
    _wf(
        5, "router", "Router",
        "Classifies an input and directs it to a specialized workflow or specific task path.",
        ("Customer support agents", "Multi-agent debate"),
        ("Input", "Router", "Specialized path A / B / C", "Output"),
        "the selected path's output is evaluated by the caller",
        (
            Stage("intake", "Intake", ("agentic-inbox",)),
            Stage("route", "Router / classification", ("grok-jev-router",), gate=True),
            Stage("paths", "Specialized paths", ("LibreChat", "Scrapling")),
        ),
        "grok-jev-router",
        ("router", "classify", "dispatch", "support ticket", "triage", "route"),
        ("support", "classification", "routing", "intake", "triage"),
        "Path Routing", "Classification Review",
    ),
    _wf(
        6, "autonomous-workflow", "Autonomous Workflow",
        "An LLM performs actions based on environmental feedback in a continuous cycle.",
        ("Autonomous embodied agents", "Computer-using agents (CUA)"),
        ("Input", "LLM call", "Action", "Environment/tools", "Feedback loop", "Output"),
        "the environment feedback loop's terminal state is evaluated by the caller",
        (
            Stage("observe", "Observe environment", ("agent-desktop",)),
            Stage("act", "Act through tools", ("FinceptTerminal", "Pix-Payment-System")),
            Stage("feedback", "Feedback loop / goal check", ("Open-Higgsfield-AI",), gate=True),
        ),
        "agent-desktop",
        ("autonomous", "computer use", "cua", "embodied", "feedback loop", "environment"),
        ("environment", "desktop", "operations", "control", "feedback"),
        "Environment Control", "Loop Safety",
    ),
    _wf(
        7, "reflexion", "Reflexion",
        "Agents learn from previous mistakes and revise responses iteratively through self-reflection.",
        ("Complex data monitoring", "Full-stack app agents"),
        ("Input", "Respond", "Revisor", "Reflective loop", "Output"),
        "the revisor's accepted revision is evaluated by the caller",
        (
            Stage("respond", "Respond", ("Open-LLM-VTuber",)),
            Stage("monitor", "Monitor outcome", ("AutoHedge", "vibetrading")),
            Stage("revise", "Revisor / reflection", ("claude-ads",), gate=True),
        ),
        "claude-ads",
        ("reflexion", "self-reflection", "learn from mistakes", "retry", "iterate", "revisor"),
        ("monitoring", "risk", "reflection", "learning", "correction"),
        "Reflection", "Revision Review",
    ),
    _wf(
        8, "rewoo", "ReWOO (Reasoning WithOut Observation)",
        "Separates planning from execution for efficient task solving; planning happens before tool outputs are seen.",
        ("Research agents", "Multi-step question answering"),
        ("Input", "Planner", "Task list", "Worker/tools", "Solver", "Update and results", "Output"),
        "the solver's final answer is evaluated by the caller",
        (
            Stage("plan", "Planner", ("grok-jev-router",)),
            Stage("tasks", "Task list execution", ("Scrapling", "agentic-inbox", "freellmapi")),
            Stage("solve", "Solver / validation", ("Open-LLM-VTuber",), gate=True),
        ),
        "grok-jev-router",
        ("rewoo", "reasoning without observation", "plan then execute", "research agent", "multi-step question"),
        ("research", "planning", "evidence", "question answering"),
        "Plan Execution", "Solver Review",
    ),
    _wf(
        9, "plan-and-execute", "Plan and Execute",
        "An agent generates subtasks, specialized agents solve them, and results are sent back to a planner.",
        ("Business process automation", "Data pipeline orchestration"),
        ("Input", "Planner", "Generate tasks", "Single task agents (parallel)", "Update task", "Output"),
        "the planner's updated task state is evaluated by the caller",
        (
            Stage("plan", "Planner", ("grok-jev-router", "agentic-inbox")),
            Stage("tasks", "Single-task agents", ("vibetrading", "AutoHedge", "hyperframes", "Open-Higgsfield-AI", "Pix-Payment-System")),
            Stage("update", "Update plan / completion", ("grok-jev-router",), gate=True),
        ),
        "agentic-inbox",
        ("plan and execute", "subtask", "business process", "data pipeline", "task agents", "orchestrate plan"),
        ("process", "pipeline", "operations", "planning", "tasks"),
        "Plan Execution", "Completion Review",
    ),
    _wf(
        10, "map-reduce", "Map-Reduce",
        "Splits a large document or dataset into chunks (Map), processes them independently, then summarizes or combines the results (Reduce).",
        ("Summarizing large documents", "Analyzing extensive datasets", "Sentiment analysis across many reviews"),
        ("Large input", "Splitter", "Map LLM calls", "Combiner/reducer", "Final output"),
        "the reducer's combined result is evaluated by the caller",
        (
            Stage("split", "Splitter", ("agentic-inbox",)),
            Stage("map", "Map partitions", ("Scrapling", "freellmapi", "vibetrading")),
            Stage("reduce", "Reduce / combine", ("Open-LLM-VTuber",), gate=True),
        ),
        "agentic-inbox",
        ("map reduce", "summarize", "large document", "chunk", "dataset", "many documents", "tickets", "reviews", "batch analysis"),
        ("dataset", "documents", "analysis", "survey", "reviews"),
        "Map-Reduce", "Aggregation Review",
    ),
    _wf(
        11, "human-in-the-loop", "Human-in-the-Loop (HITL)",
        "The agent pauses at critical junctures to request human approval, feedback, or manual intervention before proceeding.",
        ("High-stakes financial transactions", "Medical diagnosis assistance", "Legal document review", "Sensitive content moderation"),
        ("Agent action", "Pause", "Human review/edit", "Resume agent", "Output"),
        "the recorded human approval is the attestation; the engine never assumes it",
        (
            Stage("act", "Agent action", ("agentic-inbox",)),
            Stage("pause", "Pause / human surface", ("LibreChat",)),
            Stage("review", "Human review gate", ("claude-ads",), gate=True),
            Stage("resume", "Resume with approval", ("FinceptTerminal",)),
        ),
        "LibreChat",
        ("human in the loop", "hitl", "approval", "manual review", "pause", "sign off", "legal review"),
        ("approval", "review", "compliance", "legal", "safety"),
        "Human Approval", "Sign-off Audit",
    ),
    _wf(
        12, "tool-use", "Tool Use (Function Calling)",
        "The LLM is given access to external APIs, databases, or calculators; it selects a tool, formats input, and uses the result.",
        ("Real-time weather updates", "Stock market queries", "Booking flights", "Database retrieval (SQL agents)"),
        ("Input", "LLM selects tool", "Execute API/tool", "Observation", "LLM synthesizes answer", "Output"),
        "the tool result and synthesized answer are evaluated by the caller",
        (
            Stage("select", "Tool selection", ("grok-jev-router",)),
            Stage("execute", "Execute tool", ("Pix-Payment-System", "FinceptTerminal", "Scrapling")),
            Stage("observe", "Observation", ("agentic-inbox",)),
            Stage("synthesize", "Synthesize answer", ("freellmapi",), gate=True),
        ),
        "grok-jev-router",
        ("tool use", "function calling", "call an api", "database query", "sql", "calculator", "external api"),
        ("api", "database", "payment", "query", "integration"),
        "Tool Execution", "Tool Authorization",
    ),
    _wf(
        13, "multi-agent-debate", "Multi-Agent Debate",
        "Two or more agents debate opposing viewpoints; a judge agent or consensus mechanism determines the final answer.",
        ("Complex reasoning tasks", "Fact-checking", "Reducing hallucinations", "Policy analysis"),
        ("Input", "Agent A (pro) + Agent B (con)", "Multiple debate rounds", "Judge agent", "Final output"),
        "the judge's verdict is evaluated by the caller",
        (
            Stage("pro", "Pro position", ("Open-LLM-VTuber",)),
            Stage("con", "Con position", ("freellmapi",)),
            Stage("rounds", "Multi-persona orchestration", ("LibreChat",)),
            Stage("judge", "Judge / verdict", ("claude-ads",), gate=True),
        ),
        "claude-ads",
        ("debate", "pro and con", "judge", "fact check", "opposing", "verdict", "policy analysis"),
        ("policy", "fact", "reasoning", "verdict", "argument"),
        "Structured Debate", "Verdict Review",
    ),
    _wf(
        14, "rag", "Retrieval-Augmented Generation (RAG)",
        "Retrieves relevant documents from a vector store based on the query and uses that context to generate a grounded answer.",
        ("Enterprise search", "Customer support knowledge bases", "Legal research"),
        ("Input", "Embedding", "Vector DB search", "Retrieved context", "LLM call", "Output"),
        "the grounded answer's citations are evaluated by the caller",
        (
            Stage("embed", "Embedding", ("Scrapling",)),
            Stage("retrieve", "Vector/doc retrieval", ("agentic-inbox",)),
            Stage("generate", "Grounded generation", ("freellmapi", "Open-LLM-VTuber"), gate=True),
        ),
        "Scrapling",
        ("rag", "retrieval augmented", "knowledge base", "vector", "grounded answer", "enterprise search"),
        ("knowledge", "research", "legal", "records", "documents"),
        "Knowledge Retrieval", "Grounding Audit",
    ),
    _wf(
        15, "cot-cove", "Chain of Thought / Chain of Verification",
        "The agent thinks step by step; in CoVe it drafts an answer, writes verification questions, answers them, then produces a verified response.",
        ("Mathematical problem solving", "Logical reasoning", "Complex code debugging"),
        ("Input", "Step-by-step reasoning", "Draft answer", "Verification steps", "Final output"),
        "the verification pass's result is evaluated by the caller",
        (
            Stage("reason", "Step-by-step reasoning", ("Open-LLM-VTuber",)),
            Stage("draft", "Draft answer", ("freellmapi",)),
            Stage("verify", "Verification", ("claude-ads", "grok-jev-router"), gate=True),
        ),
        "Open-LLM-VTuber",
        ("chain of thought", "cove", "verification", "math", "step-by-step", "logical reasoning", "proof"),
        ("math", "proof", "verification", "reasoning", "evidence"),
        "Verification", "Proof Review",
    ),
    _wf(
        16, "ensemble", "Ensemble / Mixture of Agents",
        "Different models answer the same prompt simultaneously; a synthesizer compares outputs to find the most robust answer.",
        ("High-accuracy tasks", "Creative brainstorming", "Reducing model-specific biases"),
        ("Input", "Model A + Model B + Model C", "Synthesizer/aggregator", "Output"),
        "the synthesizer's chosen answer is evaluated by the caller",
        (
            Stage("members", "Ensemble members", ("freellmapi", "Open-LLM-VTuber", "claude-ads")),
            Stage("synthesize", "Synthesizer", ("grok-jev-router",), gate=True),
        ),
        "grok-jev-router",
        ("ensemble", "mixture of agents", "multiple models", "consensus", "compare models", "robust answer"),
        ("forecast", "strategy", "consensus", "accuracy", "comparison"),
        "Ensemble Synthesis", "Consensus Review",
    ),
    _wf(
        17, "memory-augmented", "Memory-Augmented Agent",
        "The agent uses short-term (conversation) and long-term (vector) memory, deciding what to store and retrieve.",
        ("Personalized assistants", "Long-term project management", "Therapy bots", "Tutoring systems"),
        ("Input", "Retrieve memory", "LLM call", "Update memory", "Output"),
        "the memory update's persisted record is evaluated by the caller",
        (
            Stage("retrieve", "Retrieve long-term memory", ("agentic-inbox",)),
            Stage("reason", "LLM call", ("Open-LLM-VTuber",)),
            Stage("store", "Update memory", ("Scrapling",)),
            Stage("shortterm", "Short-term conversation memory", ("LibreChat",), gate=True),
        ),
        "agentic-inbox",
        ("memory", "personalized", "long term", "tutor", "therapy", "remember", "history"),
        ("history", "personalization", "records", "longitudinal", "memory"),
        "Memory", "Record Stewardship",
    ),
    _wf(
        18, "semantic-routing", "Semantic Routing",
        "Routes queries by vector-embedding similarity rather than keyword classification, often to dedicated sub-agents.",
        ("Large-scale customer service", "Multi-domain knowledge bases"),
        ("Input", "Embedding", "Semantic similarity match", "Dedicated agent/workflow", "Output"),
        "the routed agent's answer is evaluated by the caller",
        (
            Stage("embed", "Embedding", ("Scrapling",)),
            Stage("match", "Semantic similarity match", ("grok-jev-router",), gate=True),
            Stage("agents", "Dedicated sub-agents", ("agentic-inbox", "freellmapi", "Open-LLM-VTuber")),
        ),
        "grok-jev-router",
        ("semantic routing", "semantic", "embedding", "similarity", "multi-domain", "billing or tech support"),
        ("support", "semantic", "triage", "domain", "classification"),
        "Semantic Routing", "Routing Audit",
    ),
    _wf(
        19, "hierarchical-planning", "Hierarchical Planning",
        "A top-level agent makes a high-level plan; sub-planners break it into granular tasks that execution agents handle.",
        ("Software development", "Complex event planning", "Supply chain logistics"),
        ("Goal", "High-level planner", "Sub-planners", "Execution agents", "Aggregator", "Output"),
        "the aggregator's completion report is evaluated by the caller",
        (
            Stage("plan", "High-level planner", ("grok-jev-router",)),
            Stage("subplan", "Sub-planners", ("agentic-inbox", "claude-ads")),
            Stage("execute", "Execution agents", ("agent-desktop", "Open-Higgsfield-AI", "vibetrading", "AutoHedge")),
            Stage("aggregate", "Aggregator / completion", ("grok-jev-router",), gate=True),
        ),
        "grok-jev-router",
        ("hierarchical planning", "sub-planner", "build an app", "event planning", "supply chain", "program plan"),
        ("program", "strategy", "development", "logistics", "planning"),
        "Hierarchical Plan", "Milestone Review",
    ),
    _wf(
        20, "swarm-intelligence", "Swarm Intelligence (Handoffs)",
        "Decentralized agents operate independently and hand off control to one another; no central orchestrator decides who speaks next.",
        ("Dynamic customer service flows", "Complex simulations", "Game AI"),
        ("Input", "Agent A", "Handoff", "Agent B", "Handoff to Agent C", "Final output"),
        "the swarm's emergent terminal state is evaluated by the caller; there is no self-elected authority",
        (
            Stage("agents", "Independent agents", ("agentic-inbox", "freellmapi", "LibreChat")),
            Stage("handoff", "Handoff control", ("grok-jev-router",)),
            Stage("consensus", "Emergent consensus / elected admin", ("Open-LLM-VTuber",), gate=True),
        ),
        "agentic-inbox",
        ("swarm", "handoff", "decentralized", "no orchestrator", "game ai", "simulation"),
        ("network", "handoff", "coordination", "simulation", "service"),
        "Swarm Handoff", "Handoff Review",
    ),
)

WORKFLOW_BY_ID = {w.id: w for w in WORKFLOWS}
WORKFLOW_BY_SLUG = {w.slug: w for w in WORKFLOWS}


# ============================================================================
# Compartment decision — the "agentic decision call" seam
# ============================================================================

def _compartment_for(ref: Any) -> Optional[Compartment]:
    if ref is None:
        return None
    if isinstance(ref, Compartment):
        return ref
    text = str(ref).strip().lower()
    if text in COMPARTMENT_BY_KEY:
        return COMPARTMENT_BY_KEY[text]
    if text in COMPARTMENT_BY_NAME:
        return COMPARTMENT_BY_NAME[text]
    for comp in COMPARTMENTS:
        if comp.short.lower() == text:
            return comp
    return None


def derive_roles(workflow: Workflow, compartment: Compartment) -> Dict[str, str]:
    """Derive the department/admin pair for any workflow × compartment.

    Names are composed from the workflow's kind and the compartment's short
    name, so every pattern can staff any compartment without a lookup table.
    """
    return {
        "department": f"{compartment.short} {workflow.department_kind} Department Agent",
        "admin": f"{compartment.short} {workflow.admin_kind} Admin Agent",
    }


def heuristic_decider(
    workflow: Workflow,
    goal: str,
    context: Optional[Dict[str, Any]] = None,
    hints: Optional[Sequence[Any]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_compartments: int = 5,
) -> Dict[str, Any]:
    """Deterministic necessity decision.

    Evidence, not assignment:

    * explicit hint .......... +3.0 (caller/agent declares necessity)
    * goal keyword in remit .. +2.0 per hit
    * context signal ......... +1.0 per signal
    * workflow domain overlap +0.5 per hit

    A compartment is selected only at or above ``threshold``. With the default
    threshold, generic capability overlap cannot trigger replication: there must
    be a goal/signal/hint that actually implicates the compartment. If nothing
    qualifies, nothing is spawned.
    """
    context = context or {}
    goal_l = (goal or "").lower()
    signals = [str(s).lower() for s in (context.get("signals") or [])]
    hinted = {c.key for c in (_compartment_for(h) for h in (hints or [])) if c}
    unknown_hints = [str(h) for h in (hints or []) if _compartment_for(h) is None]

    candidates: List[Dict[str, Any]] = []
    for comp in COMPARTMENTS:
        score = 0.0
        rationale: List[str] = []
        if comp.key in hinted:
            score += 3.0
            rationale.append("declared necessary by caller/agent hint")
        goal_hits = [kw for kw in comp.remit if kw in goal_l]
        if goal_hits:
            score += 2.0 * len(goal_hits)
            rationale.append(f"goal mentions: {', '.join(goal_hits)}")
        signal_hits = [s for s in signals if any(kw in s for kw in comp.remit)]
        if signal_hits:
            score += 1.0 * len(signal_hits)
            rationale.append(f"context signals: {', '.join(signal_hits[:3])}")
        domain_hits = [
            kw for kw in workflow.domain
            if any(kw in remit or remit in kw for remit in comp.remit)
        ]
        if domain_hits:
            score += 0.5 * len(domain_hits)
            rationale.append(f"workflow domain overlap: {', '.join(domain_hits)}")
        roles = derive_roles(workflow, comp)
        candidates.append({
            "compartment": comp.name,
            "compartment_key": comp.key,
            "order": comp.order,
            "score": round(score, 2),
            "selected": score >= threshold,
            "department": roles["department"],
            "admin": roles["admin"],
            "rationale": rationale or ["no necessity evidence"],
        })

    selected = [c for c in candidates if c["selected"]][:max(0, max_compartments)]
    for cand in candidates:
        if cand["selected"] and cand not in selected:
            cand["selected"] = False
            cand["rationale"].append("cut by max_compartments cap")

    reason = ""
    if not selected:
        reason = (
            "no compartment met the necessity threshold; pass explicit compartment hints "
            "or a goal that implicates a compartment to authorize replication"
        )
    return {
        "decider": "heuristic-v1",
        "workflow": {"id": workflow.id, "slug": workflow.slug, "name": workflow.name},
        "goal": goal,
        "threshold": threshold,
        "max_compartments": max_compartments,
        "candidates": candidates,
        "selected": [c["compartment_key"] for c in selected],
        "selected_compartments": [c["compartment"] for c in selected],
        "unknown_hints": unknown_hints,
        "reason": reason,
    }


Decider = Callable[..., Dict[str, Any]]


def decide_compartments(
    workflow: Workflow,
    goal: str,
    context: Optional[Dict[str, Any]] = None,
    hints: Optional[Sequence[Any]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_compartments: int = 5,
    decider: Optional[Decider] = None,
) -> Dict[str, Any]:
    """Decide which compartments are necessary for this achieved workflow.

    ``decider`` is the extension seam: pass an LLM-backed or agent-supplied
    decider with the same signature. Its output is validated and normalized so
    a bad decision cannot silently spawn the wrong compartments.
    """
    fn = decider or heuristic_decider
    raw = fn(
        workflow,
        goal,
        context=context,
        hints=hints,
        threshold=threshold,
        max_compartments=max_compartments,
    )
    return _normalize_decision(workflow, raw, goal, threshold, max_compartments)


def _normalize_decision(
    workflow: Workflow,
    raw: Dict[str, Any],
    goal: str,
    threshold: float,
    max_compartments: int,
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("compartment decision must be a dict")
    by_key: Dict[str, Dict[str, Any]] = {}
    for item in raw.get("candidates") or []:
        comp = _compartment_for(item.get("compartment_key") or item.get("compartment"))
        if not comp:
            continue
        roles = derive_roles(workflow, comp)
        by_key[comp.key] = {
            "compartment": comp.name,
            "compartment_key": comp.key,
            "order": comp.order,
            "score": float(item.get("score") or 0.0),
            "selected": bool(item.get("selected")),
            "department": roles["department"],
            "admin": roles["admin"],
            "rationale": list(item.get("rationale") or []),
        }
    candidates = [by_key[c.key] for c in COMPARTMENTS if c.key in by_key]
    selected = [c for c in candidates if c["selected"]][:max(0, max_compartments)]
    for cand in candidates:
        if cand["selected"] and cand not in selected:
            cand["selected"] = False
            cand["rationale"].append("cut by max_compartments cap")
    return {
        "decider": str(raw.get("decider") or "custom"),
        "workflow": {"id": workflow.id, "slug": workflow.slug, "name": workflow.name},
        "goal": goal,
        "threshold": threshold,
        "max_compartments": max_compartments,
        "candidates": candidates,
        "selected": [c["compartment_key"] for c in selected],
        "selected_compartments": [c["compartment"] for c in selected],
        "unknown_hints": list(raw.get("unknown_hints") or []),
        "reason": raw.get("reason") or (
            "" if selected else "no compartment met the necessity threshold"
        ),
    }


# ============================================================================
# Anatomy: seven layers of a self-replicating compartmentalized agent
# ============================================================================

ANATOMY: Tuple[Dict[str, Any], ...] = (
    {
        "layer": 1,
        "name": "Agent Core (model routing + reasoning)",
        "role": "Cognitive engine: interprets input, selects one of the 20 workflow patterns, decides actions.",
        "examples": ["Claude", "OpenAI", "Gemini", "OpenRouter"],
        "components": ["freellmapi", "Open-LLM-VTuber", "claude-ads", "grok-jev-router"],
        "implementation": "AgentCore.route_and_reason(prompt, context) behind a provider-agnostic LLM gateway.",
        "governance": "Achievement is attested here, not inferred. The Top Admin replicates only on attestation + a necessity decision.",
    },
    {
        "layer": 2,
        "name": "Orchestration & workflow execution",
        "role": "Control flow: executes the chosen workflow pattern and manages state.",
        "examples": ["LangGraph", "CrewAI"],
        "components": ["agentic-inbox", "AutoHedge"],
        "implementation": "WorkflowEngine.run(workflow, input, context, llm, tools) over a DAG/state machine of stage nodes.",
        "governance": "Stages run sequentially; a closed gate stops the DAG and downstream stages are skipped, never faked.",
    },
    {
        "layer": 3,
        "name": "Memory & knowledge",
        "role": "Context and persistence: short-term conversation history and long-term retrieval.",
        "examples": ["Redis", "Qdrant", "Supabase", "LlamaIndex"],
        "components": ["Scrapling", "LibreChat", "agentic-inbox"],
        "implementation": "MemoryManager.retrieve(query), store(context), update_long_term().",
        "governance": "Each spawned compartment keeps an isolated store; sharing only via permissioned handoffs.",
    },
    {
        "layer": 4,
        "name": "Tools & actions",
        "role": "Hands: APIs, code execution, payments.",
        "examples": ["MCP", "Firecrawl", "Tavily", "Exa"],
        "components": ["Pix-Payment-System", "FinceptTerminal", "agent-desktop", "vibetrading"],
        "implementation": "ToolRegistry of callables with JSON schemas (function calling, workflow 12).",
        "governance": "Only agents explicitly authorized for the compartment may trigger high-stakes rails.",
    },
    {
        "layer": 5,
        "name": "Interfaces & triggers",
        "role": "Entry points and output surfaces.",
        "examples": ["Vercel/Next.js", "Twilio/ElevenLabs", "Telegram/WhatsApp", "Zapier", "Temporal"],
        "components": ["Open-LLM-VTuber", "hyperframes", "agentic-inbox"],
        "implementation": "InterfaceAdapter layer: cron, webhook, chat, and voice adapters call the workflow engine.",
        "governance": "Triggers are recorded as evidence; they never count as achievement on their own.",
    },
    {
        "layer": 6,
        "name": "Observability & evaluation",
        "role": "Feedback loop: trace actions, evaluate performance, catch errors.",
        "examples": ["LangSmith", "Langfuse", "Braintrust", "Sentry"],
        "components": ["claude-ads", "AutoHedge"],
        "implementation": "Tracer logs every state transition; an Evaluator node checks a rubric before the workflow proceeds.",
        "governance": "The evaluator produces the achievement attestation the engine consumes; it is an input, not a self-verdict.",
    },
    {
        "layer": 7,
        "name": "Deployment & scaling",
        "role": "Infrastructure that keeps agents running and spawns new ones.",
        "examples": ["Docker", "Modal", "Cloudflare"],
        "components": ["agent-desktop", "FinceptTerminal"],
        "implementation": "Per-component images (Rust/CMake/Qt baked in) and a deploy orchestrator for worker pools.",
        "governance": "Spawning is capped and fail-closed; a failed spawn halts the cascade instead of skipping ahead.",
    },
)

TECH_STACK: Tuple[Dict[str, Any], ...] = (
    {"layer": "01", "name": "Foundation models", "components": ["freellmapi", "grok-jev-router"], "note": "Abstracted behind an LLM gateway to enable Router/Ensemble workflows."},
    {"layer": "02", "name": "Agent frameworks", "components": ["agentic-inbox", "AutoHedge"], "note": "Orchestration logic behind the platform WorkflowEngine."},
    {"layer": "03", "name": "Tools & protocols", "components": ["Pix-Payment-System", "FinceptTerminal", "Scrapling"], "note": "MCP and function calling plug in here."},
    {"layer": "04", "name": "Memory & knowledge", "components": ["Scrapling", "LibreChat", "agentic-inbox"], "note": "Vector store, cache, RAG behind MemoryManager."},
    {"layer": "05", "name": "Agent runtime & hosting", "components": ["agent-desktop"], "note": "Docker/Kubernetes/Modal; required for self-replication."},
    {"layer": "06", "name": "Interface & UI", "components": ["hyperframes", "LibreChat"], "note": "Chat, web, voice, CLI surfaces."},
)

PRODUCTION_LAYER: Tuple[Dict[str, Any], ...] = (
    {"concern": "Observability", "examples": ["LangSmith"], "platform": "run/report tracking, watchdog, alerts", "gate": "Supplies evidence; the caller turns evidence into attestation."},
    {"concern": "Evaluation", "examples": ["Braintrust"], "platform": "claude-ads evaluator + gate decisions", "gate": "Powers Evaluator-Optimizer (04) and every stage gate."},
    {"concern": "Guardrails", "examples": ["Guardrails AI"], "platform": "orchestrator/policy.py", "gate": "Input filtering and command policy; required for HITL (11)."},
    {"concern": "Agent identity", "examples": ["Auth0"], "platform": "agent specs + registry + constitution hash", "gate": "Only authorized agents execute high-stakes tools."},
    {"concern": "LLM gateway", "examples": ["Portkey"], "platform": "freellmapi + grok-jev-router", "gate": "Routing, fallbacks; powers semantic routing and parallelization."},
)


# ============================================================================
# Selection
# ============================================================================

def select_workflow(goal: str) -> Dict[str, Any]:
    """Pick the workflow pattern whose keywords best match ``goal``.

    Deterministic: longer (more specific) keywords score higher, and ties go to
    the lower catalogue id. With no signal at all the fallback is Plan and
    Execute (09), which is the platform's general-purpose pattern.
    """
    text = (goal or "").lower()
    scored: List[Dict[str, Any]] = []
    for wf in WORKFLOWS:
        hits = [kw for kw in wf.keywords if kw in text]
        score = sum(len(kw) for kw in hits)
        if score:
            scored.append({"workflow": wf, "score": score, "matched": hits})
    if not scored:
        fallback = WORKFLOW_BY_ID[9]
        return {"workflow": fallback, "score": 0, "matched": [], "fallback": True}
    scored.sort(key=lambda item: (-item["score"], item["workflow"].id))
    best = scored[0]
    best["fallback"] = False
    best["alternatives"] = [
        {"slug": s["workflow"].slug, "id": s["workflow"].id, "name": s["workflow"].name, "score": s["score"], "matched": s["matched"]}
        for s in scored[1:4]
    ]
    return best


def get_workflow(ref: Any) -> Optional[Workflow]:
    """Look up a workflow by id, numeric string, slug, or name."""
    if ref is None:
        return None
    if isinstance(ref, Workflow):
        return ref
    if isinstance(ref, int):
        return WORKFLOW_BY_ID.get(ref)
    text = str(ref).strip().lower()
    if text.isdigit():
        return WORKFLOW_BY_ID.get(int(text))
    if text in WORKFLOW_BY_SLUG:
        return WORKFLOW_BY_SLUG[text]
    for wf in WORKFLOWS:
        if wf.name.lower() == text:
            return wf
    return None


# ============================================================================
# Readiness and gates
# ============================================================================

def _declared(component: str) -> str:
    note = COMPONENT_BY_NAME.get(component)
    return note.declared_status if note else "ready"


def workflow_readiness(workflow: Workflow, readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    readiness = readiness or {}
    blocked: List[str] = []
    needs_setup: List[str] = []
    for name in workflow.referenced_components():
        status = readiness.get(name) or _declared(name)
        if status == "blocked":
            blocked.append(name)
        elif status == "needs_setup":
            needs_setup.append(name)
    overall = "blocked" if blocked else ("needs_setup" if needs_setup else "ready")
    return {"status": overall, "blocked": blocked, "needs_setup": needs_setup}


def gate_decision(stage: Stage, readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Fail-closed gate for one stage.

    A blocked component closes the gate. ``needs_setup`` components stay open
    but are reported so the caller can decide whether to attempt the stage.
    """
    readiness = readiness or {}
    blocked = [c for c in stage.components if (readiness.get(c) or _declared(c)) == "blocked"]
    setup = [c for c in stage.components if (readiness.get(c) or _declared(c)) == "needs_setup"]
    if blocked:
        return {
            "allowed": False,
            "reason": f"closed: blocked component(s) {', '.join(blocked)}",
            "blocked": blocked,
            "needs_setup": setup,
        }
    reason = "open" if not setup else f"open (needs setup: {', '.join(setup)})"
    return {"allowed": True, "reason": reason, "blocked": [], "needs_setup": setup}


# ============================================================================
# Run construction (the skeleton the admin layer executes)
# ============================================================================

def build_run(
    workflow: Workflow,
    goal: str,
    context: Optional[Dict[str, Any]] = None,
    readiness: Optional[Dict[str, str]] = None,
    decision: Optional[Dict[str, Any]] = None,
    hints: Optional[Sequence[Any]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_compartments: int = 5,
    decider: Optional[Decider] = None,
) -> Dict[str, Any]:
    """Build the stage DAG plus the (decided, not assumed) replication plan."""
    context = context or {}
    run_id = f"wf-{uuid.uuid4().hex[:12]}"
    decision = decision or decide_compartments(
        workflow, goal, context=context, hints=hints,
        threshold=threshold, max_compartments=max_compartments, decider=decider,
    )

    nodes: List[Dict[str, Any]] = []
    previous: Optional[str] = None
    for index, stage in enumerate(workflow.stages, start=1):
        node_id = f"{run_id}-n{index}"
        depth = stack.stage_depth(workflow, stage)
        verdict = gate_decision(stage, readiness)
        verdict["verifier"] = depth["harness"]["verifier"]["name"]
        # A gate is only as good as its independent verifier. If that verifier
        # is blocked, the gate closes even when the producer is healthy.
        if depth["harness"]["verifier"]["mode"] == "component":
            verifier = depth["harness"]["verifier"]["name"]
            if (readiness.get(verifier) or _declared(verifier)) == "blocked" and verdict["allowed"]:
                verdict = {
                    "allowed": False,
                    "reason": f"closed: independent verifier {verifier} is blocked",
                    "blocked": [verifier],
                    "needs_setup": verdict.get("needs_setup", []),
                    "verifier": verifier,
                }
        nodes.append({
            "node_id": node_id,
            "index": index,
            "stage": stage.key,
            "label": stage.label,
            "components": list(stage.components),
            "primary_agent": stage.components[0] if stage.components else "admin",
            "gate": stage.gate,
            "gate_decision": verdict,
            "depth": depth,
            "environment": stack.stage_environment(workflow, stage),
            "depends_on": [previous] if previous else [],
            "state": "pending",
        })
        previous = node_id

    cascade = []
    for item in decision["candidates"]:
        if not item["selected"]:
            continue
        cascade.append({
            "order": item["order"],
            "compartment": item["compartment"],
            "compartment_key": item["compartment_key"],
            "department": item["department"],
            "admin": item["admin"],
            "reports_to": "admin",
            "source_agent": workflow.replication_source,
            "necessity_score": item["score"],
            "rationale": item["rationale"],
            "authority": stack.compartment_authority(workflow, item, item, readiness),
            "state": "pending",
        })

    return {
        "run_id": run_id,
        "workflow": {
            "id": workflow.id,
            "slug": workflow.slug,
            "name": workflow.name,
            "achievement_point": workflow.achievement_point,
        },
        "goal": goal,
        "context": context,
        "nodes": nodes,
        "decision": decision,
        "replication_cascade": cascade,
        "readiness": workflow_readiness(workflow, readiness),
        "depth": stack.depth_readiness(workflow, readiness),
        "created_at": _now(),
        "state": "built",
    }


# ============================================================================
# Catalogue / API payloads
# ============================================================================

def catalogue(readiness: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    items = [wf.to_dict(readiness) for wf in WORKFLOWS]
    return {
        "count": len(items),
        "compartments": [c.to_dict() for c in COMPARTMENTS],
        "workflows": items,
        "components": [c.to_dict() for c in COMPONENT_NOTES],
        "anatomy": list(ANATOMY),
        "tech_stack": list(TECH_STACK),
        "production_layer": list(PRODUCTION_LAYER),
        "architecture": stack.summary(readiness),
        "gaps": stack.gaps(),
        "replication": {
            "order": [c.name for c in COMPARTMENTS],
            "assignment": "compartment-agnostic: any workflow may staff any compartment",
            "decision": "necessity is decided per run by decide_compartments (hints, goal evidence, signals); default threshold 2.0",
            "rule": "achievement attestation -> necessity decision -> selected compartments only, in canonical order",
            "fail_closed": [
                "no achievement attestation -> no cascade",
                "closed stage gate -> DAG stops, downstream stages skipped",
                "no compartment above threshold -> no cascade",
                "first failed spawn -> cascade halts; remaining compartments skipped",
                "no funding/settlement in this engine",
            ],
            "decider_seam": "pass a custom decider (e.g. LLM/agent-backed) to decide_compartments; output is normalized and validated",
        },
        "docs": "workflows/README.md",
        "source": "workflows/catalogue.json",
    }


def describe(ref: Any, readiness: Optional[Dict[str, str]] = None, goal: Optional[str] = None) -> Optional[Dict[str, Any]]:
    wf = get_workflow(ref)
    if not wf:
        return None
    data = wf.to_dict(readiness)
    preview_goal = goal or f"preview {wf.name}"
    data["depth"] = stack.depth_readiness(wf, readiness)
    data["decision_preview"] = decide_compartments(wf, preview_goal)
    data["run_preview"] = build_run(wf, preview_goal, readiness=readiness)
    return data


def export_catalogue(path: Optional[Any] = None) -> str:
    target = CATALOGUE_PATH if path is None else Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = catalogue()
    payload["exported_at"] = _now()
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(target)


# ============================================================================
# CLI
# ============================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m orchestrator.workflows", description=__doc__)
    parser.add_argument("--list", action="store_true", help="list the 20 workflows")
    parser.add_argument("--select", metavar="GOAL", help="show which workflow matches a goal")
    parser.add_argument("--decide", metavar="GOAL", help="show the compartment necessity decision for a goal")
    parser.add_argument("--workflow", metavar="REF", help="workflow id/slug for --decide (default: selected)")
    parser.add_argument("--hints", metavar="KEYS", help="comma-separated compartment keys for --decide")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--show", metavar="REF", help="show one workflow by id/slug")
    parser.add_argument("--export", nargs="?", const=str(CATALOGUE_PATH), help="write catalogue.json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.export:
        print(f"wrote {export_catalogue(args.export)}")
        return 0
    if args.select:
        pick = select_workflow(args.select)
        wf = pick["workflow"]
        print(f"{wf.id:02d} {wf.name}  (score {pick['score']}, matched {pick['matched'] or 'fallback'})")
        for alt in pick.get("alternatives", []):
            print(f"    alt: {alt['id']:02d} {alt['name']} (score {alt['score']})")
        return 0
    if args.decide:
        wf = get_workflow(args.workflow) if args.workflow else select_workflow(args.decide)["workflow"]
        if not wf:
            print(f"no workflow matches {args.workflow!r}")
            return 1
        hints = [h.strip() for h in (args.hints or "").split(",") if h.strip()]
        decision = decide_compartments(wf, args.decide, hints=hints, threshold=args.threshold)
        print(f"workflow {wf.id:02d} {wf.name} · threshold {decision['threshold']}")
        for cand in decision["candidates"]:
            mark = "SELECT" if cand["selected"] else "  -   "
            print(f"  {mark} {cand['order']}. {cand['compartment']:<36} score={cand['score']:<5} {cand['rationale']}")
        print(f"selected: {decision['selected_compartments'] or 'none'}")
        if decision["reason"]:
            print(f"reason: {decision['reason']}")
        return 0
    if args.show:
        wf = get_workflow(args.show)
        if not wf:
            print(f"no workflow matches {args.show!r}")
            return 1
        print(json.dumps(wf.to_dict(), indent=2, ensure_ascii=False))
        return 0
    for wf in WORKFLOWS:
        readiness = workflow_readiness(wf)
        print(f"{wf.id:02d}  {wf.name:<46} [{readiness['status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
