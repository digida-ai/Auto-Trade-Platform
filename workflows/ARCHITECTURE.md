# Agentic Architecture

What the platform stands on, in layers. Every layer below is mapped onto a real
module of this repo. Vendor products (LangGraph, Qdrant, Docker, LangSmith,
Portkey, Langfuse, Langfuse, …) are listed under **analogues** for orientation
only: nothing in this platform imports, installs, or depends on them.

```
 ┌──────────────────────────────────────────────────────────────┐
 │ 6. Interface & UI            orchestrator/server.py, dashboard.html │
 │ 5. Runtime & hosting         orchestrator/worker.py, components.py │
 │ 4. Memory & knowledge        orchestrator/store.py, geo.py, skills.py │
 │ 3. Tools & protocols         orchestrator/mcp.py, tools.py, policy.py │
 │ 2. Agent frameworks          orchestrator/planner.py, router.py, admin.py, workflows.py │
 │ 1. Foundation models         freellmapi / grok-jev-router (components) │
 └──────────────────────────────────────────────────────────────┘
   + Protocol layer: MCP (implemented); A2A (not implemented)
   + Semantic layer: entities, relationships, glossary, context rules
   + Data governance: products, contracts, access, audit, privacy
   + Production layer: observability, evaluation, guardrails, identity, gateway
```

## 01. Foundation models — reasoning and planning

*Our realization.* `orchestrator/router.py` routes plans; model work is attested
by the caller, not performed by this engine.
*Analogues.* OpenAI, Anthropic, Gemini, OpenRouter, local weights.
*Obligation.* Provider-agnostic: no stage may assume a specific vendor; model
choice is recorded on the run.
*Gap.* There is no in-process model gateway.

## 02. Agent frameworks — plan, route, execute

*Our realization.* `orchestrator/planner.py`, `orchestrator/router.py`,
`orchestrator/admin.py`, `orchestrator/worker.py`, `orchestrator/workflows.py`.
*Analogues.* LangGraph, CrewAI, Pydantic AI.
*Obligation.* State lives in a run record, not in a process; every transition is
reported; a closed gate stops the DAG, downstream stages are skipped, never
faked.
*Gap.* Single-process orchestration; no durable execution across restarts
beyond run replay.

## 03. Tools & protocols — how the agent reaches the world

*Our realization.* `orchestrator/mcp.py` (one shared server with tools, data
resources, prompts, central rate limits), `orchestrator/tools.py`
(read/write/list/shell), `orchestrator/policy.py`.
*Analogues.* MCP, function calling, Composio, Firecrawl, Tavily.
*Obligation.* Every tool call is authorized by `policy.evaluate` before
execution; a denial is final and recorded as blocked; schemas are declared.
*Gap.* A2A is not implemented — see protocol layer below.

## 04. Memory & knowledge — what the agent remembers

*Our realization.* `orchestrator/store.py` (runs, reports, agent states),
`orchestrator/geo.py` (places, automata, work items, routes, sqlite),
`orchestrator/skills.py` (skills repository).
*Analogues.* Qdrant, Chroma, Redis, pgvector, LlamaIndex.
*Obligation.* Short-term state is scoped to a run; long-term state is a named
data product (see data products); retrieval returns provenance; compartment
stores are isolated, shared only through recorded handoffs.
*Gap.* No embedding index or vector store. Retrieval is metadata/keyword over
local stores; similarity search is designed (provenance, locality, permission
tiers, ANN index types) but not implemented.

## 05. Runtime & hosting — where agents run

*Our realization.* `orchestrator/server.py`, `orchestrator/worker.py`
(WorkerLifecycle: created → running → completed/killed/suspended),
`orchestrator/components.py` (live readiness).
*Analogues.* Docker, Kubernetes, Modal.
*Obligation.* Worker lifecycle is explicit; readiness is measured live;
spawning is capped and fail-closed.
*Gap.* No container orchestrator; replication is registry-level.

## 06. Interface & UI — how the goal arrives

*Our realization.* `orchestrator/server.py` JSON API + `dashboard.html`.
*Analogues.* Next.js, Gradio, Streamlit, Telegram/WhatsApp adapters.
*Obligation.* A trigger is evidence, never proof of achievement; every mutation
is an authenticated local call; the UI shows blockers verbatim.
*Gap.* Single local dashboard; no public or multi-tenant surface.

## Subplatforms are the environments

The twenty patterns talk about *where* work happens as **environments**:
the subplatform (component) is the scope in which the agents operate. Each
environment has a scope (the component directory), toolchains, services,
guardrails and a posture. This is not a metaphor; the data is real, derived
from `agents/specs/*.json` and `agents/registry.json`, and every environment
is served at `GET /stack` → `environments`.

| Environment | Component(s) | Scope | Posture |
|---|---|---|---|
| Trading (zero funding) | vibetrading | `vibetrading-main` | trading-only; live capital denied by default |
| Hedge trading | AutoHedge | `AutoHedge-main` | live capital denied without operator authorization |
| Trading bot | spider-ai--trading-bot | `spider-ai--trading-bot-main` | live capital denied; `MAX_ORDER_SIZE_USD`/`MAX_POSITION_SIZE_USD` are environment caps (mongodb required) |
| Payment rail | Pix-Payment-System | `Pix-Payment-System-main` | settlement/withdrawal denied by default |
| Financial terminal | FinceptTerminal | `FinceptTerminal-main` | live capital denied by default |
| Intake & document store | agentic-inbox | `agentic-inbox-main` | quiet intake; outbound writes gated (Cloudflare required) |
| Desktop autonomy | agent-desktop | `agent-desktop-main` | guarded shell: destructive patterns blocked, cwd must stay in scope |
| Multi-persona chat | LibreChat | `LibreChat-main` | tenant-scoped; audit log fail-closed (mongodb required) |
| Assistant/media | Open-LLM-VTuber | `Open-LLM-VTuber-main` | local LLM (ollama optional); generation gated |
| Retrieval & scraping | Scrapling | `Scrapling-main` | no outbound publishing |
| Evaluation | claude-ads | `claude-ads-main` | read/judge; denial final |
| Gateway | grok-jev-router | `grok-jev-router-main` | no tools; every verdict is an input, not an attestation |
| LLM gateway | freellmapi | `freellmapi-main` | proxy-first; webhook validation on; no spend, no raw-secret logging (Cloudflare required) |
| Media render | hyperframes | `hyperframes-main` | renders only; review before distribution (node+bun) |
| Image generation | Open-Higgsfield-AI | `Open-Higgsfield-AI-main` | generation-only; review before distribution |

Rules:

* **A stage runs in its components' environments.** `stack.stage_environment(workflow, stage)`
  returns the environment keys of the stage's components (fallback: the
  workflow's replication source). The run records them per stage
  (`run.steps[].output.environment`).
* **Guardrails live with the environment.** Specs carry
  `allowed_paths`, `denied_commands`, `spend`, `live_capital`; `policy.evaluate`
  enforces them per agent. The trading posture (zero funding, live-capital
  deny) is the platform default, not an opinion of the workflow.
* **Cross-environment transfer is mediated.** Handoffs go through the Admin
  Top Layer (recorded on the run), not direct agent-to-agent calls.
* **Vertical depth is real but bounded.** The four engineering layers
  (prompt, context, harness, loop) put budgets in front of environments:
  a stage's harness is limited to the environment's tools, and the loop
  stops at budgets.
* **A blocked environment is a blocked stage.** If every component of a
  stage is blocked, the stage is blocked even before the verifier check.

## Protocol layer

| Protocol | Status | Our realization | Gap |
|---|---|---|---|
| MCP (tools/resources/prompts) | **implemented** | `orchestrator/mcp.py`, one shared server, central rate limits | Tool execution is still local; no external MCP transport is wired |
| Function calling | **implemented** | `MCPTool.input_schema` + `policy.evaluate` | No schema validation of model-proposed arguments before dispatch |
| A2A (agent-to-agent) | **not implemented** | Router/Admin mediate handoffs | No peer discovery or capability negotiation; handoff is hierarchical by design, but it is not A2A |

## Semantic layer — shared meaning

Entities, relationships, glossary, context rules, explainability. All of it is
*designed here*; the graph is not built because no vector store exists yet.

* **Entities.** agent, component, workflow, compartment, stage, run, report,
  worker, skill, place, work item, route, data product.
* **Relationships.** agent → owns → component; agent → reports_to → admin;
  run → executes → workflow; run → contains → stage; stage → consumes →
  data product; stage → verified_by → agent; compartment → staffs →
  department agent + admin agent; replica → derived_from → source agent.
* **Glossary.** gate, attestation, compartment, replica, readiness
  (ready/needs_setup/blocked), data product — see `orchestrator/stack.py: SEMANTIC_LAYER`.
* **Context rules.** only governed products enter a stage's context; sensitive
  products need an explicit approval on the run; every retrieved item carries
  provenance (product, record id, timestamp); context is budgeted per stage.
* **Explainability.** pattern selection records matched keywords + alternatives;
  compartment selection records score + rationale; gates record the blocking
  component/verifier; cascades record why they were skipped/halted/capped.
* **Gaps.** no ontology/knowledge graph; the glossary is curated, not
  queryable; no business glossary data product.

## Production layer — runs under every agent

| Concern | Our realization | Gate it powers |
|---|---|---|
| Observability | `observatory.py` (watchdog + alerts), run/report records | Top Admin reads goal achievement |
| Evaluation | `orchestrator/stack.py: stage_depth` (per-stage verifier) + caller attestation | Evaluator-Optimizer (04) and every gate |
| Guardrails | `orchestrator/policy.py` | HITL (11), command safety, financial rules |
| Agent identity | `orchestrator/agent_specs.py` (constitution hash bound to every spec) | Only authorized agents act on high-stakes rails |
| LLM gateway | `freellmapi` + `grok-jev-router` (status: gap while dependencies are missing) | Semantic routing (18), parallelization (02) |

## Data products — named, owned, contracted, classified

`orchestrator/stack.py: DATA_PRODUCTS` — ten products, each with owner,
classification, contract keys, retention, consumers and a live probe:

| Product | Owner | Class | Consumers |
|---|---|---|---|
| Runs | orchestrator/admin | internal | dashboard, observability, workflow engine |
| Reports | admin | internal | audit, watchdog, lineage |
| Agent states | admin | internal | watchdog, hierarchy, dashboard |
| Agent specs | agents/registry | public | dispatch, policy, workflow engine |
| Registry | agents/registry | public | router, components, dashboard |
| Geo records | orchestrator/geo | sensitive | map overview, route planning |
| Skills | orchestrator/skills | internal | workers, dashboard |
| MCP capabilities | orchestrator/mcp | internal | workers, stages |
| Workflow catalogue | orchestrator/workflows | public | dashboard, API, docs |
| Compartment decisions | admin | internal | audit, replication, dashboard |

`GET /data/products` returns each with its live observation and a `governed`
flag (owner + classification + contract + availability).

## Governance

| Concern | Status | Realization |
|---|---|---|
| Access | **enforced** | RBAC: agent specs + registry scope (`policy.evaluate`, `component_scope`); ABAC: per-agent guardrails (`denied_commands`, `allowed_paths`, `spend`, `live_capital`) |
| Audit | **append_only** | reports JSONL + run records; policy lists `state/audit.log` as a protected path |
| Privacy | **partial** | every product has a classification; geo is sensitive; redaction in `admin._safe_meta` |
| Quality | **measured** | live readiness probes (`components.readiness`) |
| Lineage | **recorded** | `GET /runs/{id}/lineage`: goal → decision → stages → cascade → replicas |

## Engineering layers — every stage must specify all four

The four engineering layers of the reference architecture applied per stage
(`orchestrator/stack.py: KIND_PROFILES`). A stage is runnable only if the whole
contract is present; the engine reports incomplete stages and closes gates
whose independent verifier is unavailable.

1. **Prompt.** role, instruction, output schema, examples policy (governed data only).
2. **Context.** sources (named products), token budget, compression policy,
   citation requirement, provenance required.
3. **Harness.** tool classes authorized, policy gate, components, verifier,
   max tool calls.
4. **Loop.** success criteria, max iterations, token/time/tool budgets,
   stop conditions (success, budget exhaustion, policy denial, verifier
   reject limit, human required, upstream gate closed), escalation.

`orchestrator/workflows.py: build_run` records the depth of every stage on the
run (`run.steps[].output.depth*`, `run.result.depth`), and `execute_workflow`
refuses to continue a stage whose contract is incomplete.

## The agent loop

```
goal → interface + environment join → plan (pattern + DAG + depth) → reason → tools via MCP
     → memory → result (with provenance) → evaluate (gate/verifier/attestation)
     → continue/stop (budgets, stop conditions)
```

Each step has an explicit owner (repo module) and a fail-closed obligation
(`orchestrator/stack.py: AGENT_LOOP`). The "environment join" step is where
the agent boots into its subplatform environment: spec validated (constitution
hash), scope set, guardrails loaded.

## Execution flow, deep version

1. **Bootstrap** — components register capabilities (`admin.register_mcp_servers_for_agents`);
   specs validate against the constitution hash.
2. **Trigger** — `POST /workflows/execute` or an eagent report.
3. **Plan** — pattern selection (keyword, documented fallback = Plan and Execute),
   stage DAG, depth contracts.
4. **Execute** — stages in order; each runs against its depth contract; closed
   gates skip downstream stages; policy gates every tool call.
5. **Evaluate** — verifier result + caller attestation.
6. **Replicate** — necessity decision; compartment authority check (gates, data
   contracts, no-gate refusal); `replicate_agent` per selected compartment in
   canonical order; first failure halts.
7. **Report** — run record, reports stream, alerts, lineage.

## Honest gaps (also at `GET /stack` → `gaps`)

1. No in-process model gateway; model work is attested by the caller.
2. No A2A — handoffs are router/admin mediated.
3. No embedding index / vector store — retrieval is metadata and keyword.
4. No ontology/knowledge graph, no business glossary data product.
5. Replication is registry-level, not process-level.
6. No public multi-tenant surface.

## How to read it live

* `GET /stack` — 6 layers with status, agent loop, protocol, semantic layer,
  production, data products, governance, engineering contract.
* `GET /data/products` — products + live probes + governed flags.
* `GET /runs/{id}/lineage` — one run as a chain.
