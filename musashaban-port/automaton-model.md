# Agent Automaton Model (ported, adapted)

Every agent in the AutoTrading Ideal Platform (admin and every eagent) is modeled as an **automaton**: a stateful, rule/soul-guided entity that runs a perceive/think→act→observe→report cycle under the constitution.

## 1. Automaton structure

- **Identity**: name/id, subplatform/component (or admin/platform), role, soul, constitution binding.
- **State**: explicit lifecycle state (see below) plus run/step context.
- **Inputs**: tasks/directives from admin (or primary initiative at admin), environment/tool results, reports from subordinate agents (admin only).
- **Outputs**: actions, tool executions, artifacts, and **reports to admin**.
- **Memory/state store**: run history, reports, metrics, and persisted agent state.
- **Policy**: constitution, 0-funding/fail-closed gates, access boundaries.

## 2. States and transitions

Aligned to automaton/survival lifecycle concepts (adapted to orchestrated, task-driven agents):

| State | Meaning | Typical transitions |
|---|---|---|
| `idle` | Ready, no active work | → `waking` on assignment/trigger |
| `waking` | Initializing context/plan for a task | → `running` or → `blocked`/`failed` |
| `running` | Executing think/act/observe steps | → `reporting`/`completed`, → `blocked`, → `failed`, → `sleeping` (yield/wait) |
| `reporting` | Emitting report to admin | → `completed` or → `running` (next step) |
| `blocked` | Gated (missing funds/credentials/access/approval/input) | → `running` when unblocked, → `failed`/`idle` |
| `completed` | Task/run objectives satisfied | → `idle` |
| `failed` | Unrecoverable error or constitutional/safety stop | → `idle` (with incident report) |
| `sleeping` | Paused/waiting (scheduled or backoff) | → `waking`/`running` on wake/trigger |

**Admin-specific overlays**: `planning` (decomposing initiative), `erecting_paywall`, `settling`, `funding` — treated as `running` sub-states with mandatory reporting/audit. Survival/funding overlays (e.g. `low_compute`/`critical` in source) map to **0-funding/blocked** gates in this platform: no spend without realized/authorized resources.

## 3. Reporting (mandatory, every agent → admin)

On each significant transition/step, an agent reports to admin:
- `agent`, `reports_to: "admin"` (admin reports to operator/audit log), `run_id/step`
- `state` (from→to), `timestamp`
- `task/intent`, `actions`, `evidence/output` (bounded), `result`
- `errors/blockers`, `resource_needs`, `funding/paywall/settlement signals`
- `constitution_check`/notes where relevant

Admin **aggregates** all eagent reports, maintains global state, resolves blockers, and issues directives.

## 4. Roles and authority

- **Admin Automaton (root/platform orchestrator)**: plans and routes work, supervises all eagents, receives all reports, **erects paywalls**, **performs/authorizes settlement**, and **allocates funding** per policy and realized resources. Sole authority to move value/enable paid rails (subject to constitution, config, and operator authorization). Drives 0-funding bootstrap.
- **Eagent Automatons (per subplatform)**: execute domain work, stay within scope and 0-funding/access constraints, surface opportunities/blockers (including revenue/paywall signals), and **always report to admin**. No unilateral spend/funding/paywall erection outside admin-directed, authorized workflows.

## 5. 0-funding operation

- Start in unfunded, fail-closed posture: only real zero-capital actions by default; no simulated or stand-in modes.
- Progression: discover value/capability → (optional) erect paywall/offer → realize revenue/settlement → fund next actions. Each transition is stateful, gated, and reported.
- Trading: live operation only; real-capital execution only when explicitly funded and authorized.

## 6. Implementation hooks (platform)

- Orchestrator tracks per-agent automaton state and appends reports to run/state stores.
- Dashboard/API expose agent states, transitions, and report streams.
- Registry/specs declare automaton model, reporting line, and admin paywall/settlement/funding authority.
