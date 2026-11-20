# The Catalogue of Agentic LLM Workflows

Twenty workflow patterns, each with a **hierarchical self-replicating mechanism**:
when a workflow reaches its goal, the Admin Top Layer decides which compartments
are *necessary* and spawns a new organization — Social and Welfare → Business and
Trade → Politics and Governance → Science and Research → Religious and Mystical
Organization — giving each compartment a **Department Agent** and an **Admin
Agent** that gates it.

This directory is the human-readable catalogue. The canonical data lives in
`orchestrator/workflows.py`; `workflows/catalogue.json` is a generated
machine-readable export. The engine serves it at `GET /workflows`,
`GET /workflows/{id|slug}`, `GET /admin/compartments`, and `GET /stack`; it
executes runs at `POST /workflows/execute` and previews compartment decisions at
`POST /workflows/decide`.

> **Scope note.** This is an orchestration catalogue, not an autonomy claim. The
> engine builds the DAG, gates, and cascade; it makes no model calls, runs no
> shell commands, and never settles or funds. Real work goes through
> `POST /tasks` and `orchestrator/policy.py`. Gates are fail-closed: a blocked
> component closes its stage, and a closed gate or missing achievement attestation
> stops the cascade.

## Compartments are a decision, not an assignment

Each pattern is **compartment-agnostic**: it describes how work gets done, not
where. Occupancy is a per-run agentic decision call
(`orchestrator/workflows.py: decide_compartments`): explicit hints, goal
evidence against each compartment's remit, and context signals score 0–5; only
compartments at or above the necessity threshold (default 2.0) spawn. The five
compartments form the topology; the decision decides occupants. Nothing
replicates without an attestation and a decided compartment.

A custom decider (an LLM- or agent-backed seam) can replace the heuristic —
its output is validated and normalized before use.

## How to read an entry

| Field | Meaning |
|---|---|
| **Description** | The control-flow pattern. |
| **Best suited for** | Where the pattern earns its keep. |
| **Stages** | The stage DAG, with the components each stage is built from. |
| **Achievement** | Where the pattern detects goal achievement (must be attested by the caller). |
| **Fused components** | The real subplatforms that staff the stages, with their declared posture. |

Live readiness (from `GET /components`) always wins over the declared posture.

## Part 1 — The original nine

### 01. Prompt Chaining

**Description.** Decomposes a complex task into sequential steps where each LLM call processes the output of the previous step.

**Best suited for.** Chatbot applications, tool-using AI agents.

**Stages.** `intake` — agentic-inbox · `reason` — freellmapi · `validate` — grok-jev-router *(gate)* · `reason2` — freellmapi · `retrieve` — Scrapling.

**Achievement.** The terminal LLM call's output is accepted by the gate verifier.

**Fused components.** agentic-inbox (ready); grok-jev-router (needs `TYPESAFE_API_KEY`, or `JEV_OFFLINE=1`); freellmapi (blocked: npm install); Scrapling (needs orjson via uv sync).

---

### 02. Parallelization

**Description.** Runs multiple LLM calls simultaneously and combines or aggregates the outputs.

**Best suited for.** Guardrails, automated evaluations.

**Stages.** `fanout` — vibetrading, AutoHedge, Open-LLM-VTuber, Scrapling · `aggregate` — grok-jev-router *(gate)*.

**Achievement.** The Aggregator's threshold result is attested.

**Fused components.** vibetrading (needs pandas), AutoHedge (needs python-dotenv + full requirements), Open-LLM-VTuber (needs a Python venv/deps), Scrapling.

---

### 03. Orchestrator-Worker

**Description.** A central LLM breaks down tasks and delegates them to independent worker LLMs.

**Best suited for.** Agentic RAG, coding agents.

**Stages.** `intake` — agentic-inbox · `orchestrate` — grok-jev-router · `workers` — agent-desktop, freellmapi, LibreChat, Open-Higgsfield-AI · `synthesize` — Open-LLM-VTuber *(gate)*.

**Achievement.** The orchestrator's synthesized result is attested.

**Fused components.** agentic-inbox (task intake), grok-jev-router, agent-desktop (worker shell), freellmapi (worker pool), LibreChat (blocked), Open-Higgsfield-AI (blocked), Open-LLM-VTuber (synthesizer).

---

### 04. Evaluator-Optimizer

**Description.** One model generates output while another evaluates it and suggests improvements in a continuous loop.

**Best suited for.** Code-generation agents, real-time data monitoring.

**Stages.** `generate` — Open-LLM-VTuber, freellmapi · `evaluate` — claude-ads, grok-jev-router *(gate)*.

**Achievement.** The evaluator's acceptance is the attestation point.

**Fused components.** Generator: Open-LLM-VTuber + freellmapi. Evaluator: claude-ads + grok-jev-router.

---

### 05. Router

**Description.** Classifies an input and directs it to a specialized workflow or task path.

**Best suited for.** Customer support agents, multi-agent debate.

**Stages.** `intake` — agentic-inbox · `route` — grok-jev-router *(gate)* · `paths` — LibreChat, Scrapling.

**Achievement.** The selected path's output is attested.

**Fused components.** grok-jev-router (core router); agentic-inbox (intake); Scrapling (classification support); LibreChat (multi-path conversation).

---

### 06. Autonomous Workflow

**Description.** An LLM performs actions based on environmental feedback in a continuous cycle.

**Best suited for.** Autonomous embodied agents, computer-using agents (CUA).

**Stages.** `observe` — agent-desktop · `act` — FinceptTerminal, Pix-Payment-System · `feedback` — Open-Higgsfield-AI *(gate)*.

**Achievement.** The environment feedback loop's terminal state is attested.

**Fused components.** agent-desktop (environment/action shell), FinceptTerminal (financial environment), Pix-Payment-System (transactional environment), Open-Higgsfield-AI (visual environment).

---

### 07. Reflexion

**Description.** Agents learn from mistakes and revise responses iteratively through self-reflection.

**Best suited for.** Complex data monitoring, full-stack app agents.

**Stages.** `respond` — Open-LLM-VTuber · `monitor` — AutoHedge, vibetrading · `revise` — claude-ads *(gate)*.

**Achievement.** The revisor's accepted revision is attested.

**Fused components.** AutoHedge + vibetrading (monitoring), Open-LLM-VTuber (reflection conversations), claude-ads (revision/critique).

---

### 08. ReWOO (Reasoning WithOut Observation)

**Description.** Separates planning from execution for efficient task solving; planning happens before tool outputs are seen.

**Best suited for.** Research agents, multi-step question answering.

**Stages.** `plan` — grok-jev-router · `tasks` — Scrapling, agentic-inbox, freellmapi · `solve` — Open-LLM-VTuber *(gate)*.

**Achievement.** The solver's final answer is attested.

**Fused components.** Planner: grok-jev-router. Workers/tools: Scrapling, agentic-inbox, freellmapi. Solver: Open-LLM-VTuber.

---

### 09. Plan and Execute

**Description.** An agent generates subtasks, specialized agents solve them, and results are sent back to a planner.

**Best suited for.** Business process automation, data pipeline orchestration.

**Stages.** `plan` — grok-jev-router, agentic-inbox · `tasks` — vibetrading, AutoHedge, hyperframes, Open-Higgsfield-AI, Pix-Payment-System · `update` — grok-jev-router *(gate)*.

**Achievement.** The planner's updated task state is attested.

**Fused components.** Planner: grok-jev-router + agentic-inbox. Task agents: vibetrading, AutoHedge, hyperframes, Open-Higgsfield-AI, Pix-Payment-System.

---

## Part 2 — The eleven that complete the twenty

### 10. Map-Reduce

**Description.** Splits a large document or dataset into chunks (Map), processes them independently, then summarizes or combines the results (Reduce).

**Best suited for.** Summarizing large documents, analyzing extensive datasets, sentiment analysis across many reviews.

**Stages.** `split` — agentic-inbox · `map` — Scrapling, freellmapi, vibetrading · `reduce` — Open-LLM-VTuber *(gate)*.

**Achievement.** The reducer's combined result is attested.

**Fused components.** Splitter: agentic-inbox. Map: Scrapling, freellmapi, vibetrading. Reduce: Open-LLM-VTuber.

---

### 11. Human-in-the-Loop (HITL)

**Description.** The agent pauses at critical junctures to request human approval, feedback, or manual intervention before proceeding.

**Best suited for.** High-stakes financial transactions, medical diagnosis assistance, legal document review, sensitive content moderation.

**Stages.** `act` — agentic-inbox · `pause` — LibreChat · `review` — claude-ads *(gate)* · `resume` — FinceptTerminal.

**Achievement.** The recorded human approval is the attestation; the engine never assumes it.

**Fused components.** LibreChat (pause-and-review surface), FinceptTerminal (financial approval), claude-ads (audit review).

---

### 12. Tool Use (Function Calling)

**Description.** The LLM is given access to external APIs, databases, or calculators; it selects a tool, formats input, and uses the result.

**Best suited for.** Real-time weather, stock queries, booking flights, SQL agents.

**Stages.** `select` — grok-jev-router · `execute` — Pix-Payment-System, FinceptTerminal, Scrapling · `observe` — agentic-inbox · `synthesize` — freellmapi *(gate)*.

**Achievement.** The tool result and synthesized answer are attested.

**Fused components.** Selection: grok-jev-router. Executable tools: Pix-Payment-System, FinceptTerminal, Scrapling.

---

### 13. Multi-Agent Debate

**Description.** Two or more agents debate opposing viewpoints; a judge agent or consensus mechanism determines the final answer.

**Best suited for.** Complex reasoning tasks, fact-checking, reducing hallucinations, policy analysis.

**Stages.** `pro` — Open-LLM-VTuber · `con` — freellmapi · `rounds` — LibreChat · `judge` — claude-ads *(gate)*.

**Achievement.** The judge's verdict is attested.

**Fused components.** Pro: Open-LLM-VTuber. Con: freellmapi. Multi-persona orchestration: LibreChat. Judge: claude-ads.

---

### 14. Retrieval-Augmented Generation (RAG)

**Description.** Retrieves relevant documents from a vector store based on the query and uses that context to generate a grounded answer.

**Best suited for.** Enterprise search, customer support knowledge bases, legal research.

**Stages.** `embed` — Scrapling · `retrieve` — agentic-inbox · `generate` — freellmapi, Open-LLM-VTuber *(gate)*.

**Achievement.** The grounded answer's citations are attested.

**Fused components.** Embedding/retrieval: Scrapling + agentic-inbox as the document store. Generation: freellmapi, Open-LLM-VTuber.

---

### 15. Chain of Thought / Chain of Verification

**Description.** The agent thinks step by step; in CoVe it drafts an answer, writes verification questions, answers them, then produces a verified response.

**Best suited for.** Mathematical problem solving, logical reasoning, complex code debugging.

**Stages.** `reason` — Open-LLM-VTuber · `draft` — freellmapi · `verify` — claude-ads, grok-jev-router *(gate)*.

**Achievement.** The verification pass's result is attested.

**Fused components.** Reasoning: Open-LLM-VTuber. Draft: freellmapi. Verification: claude-ads + grok-jev-router.

---

### 16. Ensemble / Mixture of Agents

**Description.** Different models answer the same prompt simultaneously; a synthesizer compares outputs to find the most robust answer.

**Best suited for.** High-accuracy tasks, creative brainstorming, reducing model-specific bias.

**Stages.** `members` — freellmapi, Open-LLM-VTuber, claude-ads · `synthesize` — grok-jev-router *(gate)*.

**Achievement.** The synthesizer's chosen answer is attested.

**Fused components.** Members: freellmapi, Open-LLM-VTuber, claude-ads. Synthesizer: grok-jev-router.

---

### 17. Memory-Augmented Agent

**Description.** The agent uses short-term (conversation) and long-term (vector) memory, deciding what to store and retrieve.

**Best suited for.** Personalized assistants, long-term project management, therapy bots, tutoring systems.

**Stages.** `retrieve` — agentic-inbox · `reason` — Open-LLM-VTuber · `store` — Scrapling · `shortterm` — LibreChat *(gate)*.

**Achievement.** The persisted memory record is attested.

**Fused components.** Short-term: LibreChat. Long-term: agentic-inbox. LLM: Open-LLM-VTuber. Memory update: Scrapling.

---

### 18. Semantic Routing

**Description.** Routes queries by vector-embedding similarity rather than keyword classification, often to dedicated sub-agents.

**Best suited for.** Large-scale customer service, multi-domain knowledge bases.

**Stages.** `embed` — Scrapling · `match` — grok-jev-router *(gate)* · `agents` — agentic-inbox, freellmapi, Open-LLM-VTuber.

**Achievement.** The routed agent's answer is attested.

**Fused components.** Embedding: Scrapling. Similarity: grok-jev-router. Sub-agents: agentic-inbox, freellmapi, Open-LLM-VTuber.

---

### 19. Hierarchical Planning

**Description.** A top-level agent makes a high-level plan; sub-planners break it into granular tasks that execution agents handle.

**Best suited for.** Software development, complex event planning, supply-chain logistics.

**Stages.** `plan` — grok-jev-router · `subplan` — agentic-inbox, claude-ads · `execute` — agent-desktop, Open-Higgsfield-AI, vibetrading, AutoHedge · `aggregate` — grok-jev-router *(gate)*.

**Achievement.** The aggregator's completion report is attested.

**Fused components.** High-level planner: grok-jev-router. Sub-planners: agentic-inbox, claude-ads. Execution: agent-desktop, Open-Higgsfield-AI, vibetrading, AutoHedge.

---

### 20. Swarm Intelligence (Handoffs)

**Description.** Decentralized agents operate independently and hand off control to one another; no central orchestrator decides who speaks next.

**Best suited for.** Dynamic customer-service flows, complex simulations, game AI.

**Stages.** `agents` — agentic-inbox, freellmapi, LibreChat · `handoff` — grok-jev-router · `consensus` — Open-LLM-VTuber *(gate)*.

**Achievement.** The swarm's emergent terminal state is attested; there is no self-elected authority.

**Fused components.** Independent agents: agentic-inbox, freellmapi, LibreChat. Handoff: grok-jev-router. Consensus: Open-LLM-VTuber.

---

## The architecture we stand on

The six layers below are mapped onto this repo's real modules; vendor names
(LangGraph, Qdrant, Docker, …) are orientation only — nothing depends on them.
See `workflows/ARCHITECTURE.md` for the full picture.

| Layer | Our realization | Status |
|---|---|---|
| 1 Foundation models | `orchestrator/router.py`, `freellmapi`, `grok-jev-router` | realized / gap |
| 2 Agent frameworks | `orchestrator/planner.py`, `router.py`, `admin.py`, `worker.py`, `workflows.py` | realized |
| 3 Tools & protocols | `orchestrator/mcp.py`, `orchestrator/tools.py`, `orchestrator/policy.py` | realized / gap |
| 4 Memory & knowledge | `orchestrator/store.py`, `geo.py`, `skills.py` | realized / gap |
| 5 Runtime & hosting | `orchestrator/server.py`, `components.py` | realized / gap |
| 6 Interface & UI | `server.py`, `dashboard.html` | realized |

The agent loop is the contract every stage runs against: goal → interface →
plan → reason → tools (MCP) → memory → result → evaluate → continue/stop, with
explicit budgets and stop conditions. The gaps are honest: there is no in-process
model gateway, no A2A peer protocol, no embedding index.

### Per-stage engineering depth

Every stage carries a depth contract (role, context sources, tool
authorization, verifier, success criteria, budget, stop conditions) in
`run.steps[].output` and `GET /workflows/{id}`. Fail-closed rules:

* A gate closes when its **independent verifier** is blocked, even if the
  producer is healthy.
* A stage with no gate is attested only by caller attestation (`achieved=True`).
* Replication requires: the DAG completed, an attestation was supplied, and
  ≥1 compartment passed the necessity threshold.
* The first failed spawn halts the cascade; the rest are skipped.

### Lineage

`GET /runs/{id}/lineage` explains any run: goal → selected pattern → decided
compartments → stage outcomes → cascade states.

## Using it

```bat
rem list all 20 with declared readiness
python -m orchestrator.workflows --list

rem what pattern matches a goal?
python -m orchestrator.workflows --select "audit the trading policy for legal risk"

rem what compartments does this goal need?
python -m orchestrator.workflows --decide "audit the trading policy" --workflow rag

rem show one pattern
python -m orchestrator.workflows --show 14

rem regenerate catalogue.json from the canonical Python data
python -m orchestrator.workflows --export
```

```bat
rem architecture: 6 layers, loop, protocol/semantic, data products, governance
curl http://127.0.0.1:8765/stack
curl http://127.0.0.1:8765/data/products

rem catalogue, one pattern, compartments
curl http://127.0.0.1:8765/workflows
curl http://127.0.0.1:8765/workflows/rag
curl http://127.0.0.1:8765/admin/compartments

rem API: build/attest a run (no model calls, no shell, no funding)
curl -X POST http://127.0.0.1:8765/workflows/execute ^
  -H "Content-Type: application/json" ^
  -d "{\"goal\":\"summarize 400 support tickets\",\"workflow\":10,\"mode\":\"attest\",\"achieved\":true,\"replicate\":true,\"max_replicas\":5}"
```

`mode: "plan"` records the DAG, gate decisions and depth contracts.
`mode: "attest"` also creates and completes one worker per completed stage.
Replication is fail-closed: if any gate is closed, if nothing was attested, or
if no compartment is decided necessary, the cascade is skipped and the run
reports exactly what must be fixed.

## Appendix — illustrative staffing from the source catalogue

These department/admin pairs were in the source catalogue. The engine no longer
assigns them: each pattern may staff any compartment on necessity. They are kept
here as illustration of the pattern language; any use as a fixed table is a
design reversal the team must decide on, not an engine behavior.

| Workflow | Social and Welfare | Business and Trade | Politics and Governance | Science and Research | Religious and Mystical Organization |
|---|---|---|---|---|---|
| 01 Prompt Chaining | Community Support / Welfare Allocation | Supply Chain / Trade Compliance | Legislative Drafting / Electoral Oversight | Experimental Design / Peer Review | Ritual Coordination / Doctrine Interpretation |
| 02 Parallelization | Housing Assistance / Caseworker | Market Analysis / Contract Review | Public Consultation / Policy Audit | Data Collection / Grant Allocation | Pilgrimage Logistics / Sacred Calendar |
| 03 Orchestrator-Worker | Elder Care / Benefits Eligibility | Logistics Routing / Tariff Calculation | Constituency Services / Lobbying Disclosure | Simulation Running / Ethics Board | Meditation Instruction / Initiation Rites |
| 04 Evaluator-Optimizer | Child Protection / Foster Care | Quality Assurance / Inventory | Campaign Strategy / Voter Registration | Hypothesis Testing / Lab Safety | Sermon Preparation / Congregation Records |
| 05 Router | Food Bank Coordination / Volunteer Scheduling | Vendor Onboarding / Payment Reconciliation | Municipal Planning / Zoning Variance | Field Study / Publication Ethics | Charity Collection / Clergy Ordination |
| 06 Autonomous Workflow | Disaster Relief / Shelter Allocation | Fleet Management / Fuel Logistics | Emergency Powers / Curfew Enforcement | Autonomous Rover / Sample Curation | Sacred Site Maintenance / Pilgrim Safety |
| 07 Reflexion | Mental Health Triage / Crisis Line | Fraud Detection / Dispute Resolution | Ethics Committee / Conflict of Interest | Replication Study / Data Integrity | Exegesis / Heresy Review |
| 08 ReWOO | Unemployment Assistance / Job Matching | Import/Export / Customs Documentation | Treaty Negotiation / Diplomatic Protocol | Literature Review / Citation Management | Theological Debate / Canon Law |
| 09 Plan and Execute | Public Health Outreach / Vaccination Scheduling | Procurement / Supplier Diversity | Budget Allocation / Procurement Oversight | Clinical Trial / IRB Compliance | Festival Planning / Donation Audit |
| 10 Map-Reduce | Demographic Survey / Resource Distribution | Customer Feedback / Product Recall | Census Analysis / Redistricting | Genomic Sequencing / Biobank | Scriptural Concordance / Liturgical Calendar |
| 11 HITL | Child Custody / Human Review | Merger Approval / Regulatory Filing | Judicial Sentencing / Clemency Review | Human Trials / Informed Consent | Excommunication / Reconciliation |
| 12 Tool Use | Benefits Calculator / Eligibility API | Currency Exchange / Payment Gateway | Voting Machine Interface / Audit Log | Telescope Control / Observation Scheduling | Prayer Request / Pastoral Care |
| 13 Multi-Agent Debate | Domestic Policy Debate / Welfare Reform | Antitrust Debate / Merger Review | Constitutional Debate / Impeachment Inquiry | Peer Review Debate / Journal Editorial | Interfaith Dialogue / Ecumenical Council |
| 14 RAG | Social Services Knowledge / Case Law Retrieval | Market Intelligence / Competitor Analysis | Legislative Archive / Statutory Interpretation | Patent Search / Prior Art | Sacred Text Retrieval / Doctrinal Reference |
| 15 CoT/CoVe | Poverty Alleviation / Impact Assessment | Financial Audit / Risk Assessment | Policy Impact / Cost-Benefit | Mathematical Proof / Theorem Verification | Moral Theology / Casuistry |
| 16 Ensemble | Homelessness Strategy / Multi-Model Needs Assessment | Investment Strategy / Portfolio Diversification | Election Forecasting / Polling Aggregation | Climate Modeling / Multi-Model Consensus | Prophecy Interpretation / Sign Verification |
| 17 Memory-Augmented | Foster Care History / Longitudinal Case Memory | CRM / Client History | Voter History / Constituent Memory | Longitudinal Study / Cohort Memory | Confession Records / Spiritual Journey |
| 18 Semantic Routing | Emergency Housing / Semantic Triage | Product Support / Semantic Escalation | Constituent Services / Semantic Intake | Interdisciplinary Research / Semantic Grant Matching | Pastoral Counseling / Semantic Referral |
| 19 Hierarchical Planning | Community Development / Sub-Plan Coordination | Enterprise Resource Planning / Sub-Plan Budget | National Strategy / Sub-Plan Legislative | Research Program / Sub-Plan Grant | Missionary Strategy / Sub-Plan Logistics |
| 20 Swarm | Community Mediation / Case Escalation | Partner Network / Handoff Reconciliation | Coalition Coordination / Consensus Audit | Distributed Experiment / Peer Consensus | Congregation Network / Doctrinal Consensus |

## Source

Patterns (mechanisms, achievement points, fused components, the appendix
table), the five-compartment cascade, and the seven-layer anatomy were supplied
as the *Catalogue of Agentic LLM Workflows, Expanded with Hierarchical
Self-Replicating Mechanisms and Fused Components* and encoded in
`orchestrator/workflows.py`. The per-workflow compartment tables were retained
in the appendix above as illustration; the engine moved to necessity-based
staffing in `orchestrator/workflows.py: decide_compartments`.
