# Platform agents — Admin + eagents (standalone, automaton model)

The unifying denominator of the AutoTrading Ideal Platform is a **standalone
agent system** (independent of OpenMausBot). Every agent is modeled as an
**automaton** (stateful, rule/soul-guided, perceive/think→act→observe→report)
and **every eagent reports to the admin agent**.

## Ported layer (musashaban)

Rules/constitution, soul, access methods, and paywall-building/settlement/funding
logic are ported from `D:\AI\automation\musashaban\` (synthesized, no secrets
copied) into `../musashaban-port/`:

- `../musashaban-port/constitution.md` — immutable three laws (I > II > III)
- `../musashaban-port/soul.md` — soul model (soul/v1) and admin/eagent roles
- `../musashaban-port/access-methods.md` — identity/provisioning/credential/device methods (no secrets)
- `../musashaban-port/paywall-funding.md` — fail-closed paywall, settlement, funding, 0-funding bootstrap
- `../musashaban-port/automaton-model.md` — states/transitions and mandatory reporting
- `../musashaban-port/port-manifest.json` — manifest

## Agents

- **Admin agent** (`admin-agent.md`) — the central orchestrator and runtime brain
  (Admin Automaton). n8n-inspired workflow engine: triggers, planned workflows,
  eagent nodes, and a broad native tool/capability surface. Routes tasks, runs
  and supervises workflows, collects **reports from all eagents**, and operates
  the platform end-to-end. **Admin authority:** erects paywalls, settles, and
  funds (from realized/authorized resources only; fail-closed, 0-funding by
  default). Reports to operator/audit log.
- **eagents** (`eagents/`) — one per subplatform (15), each an **eagent
  automaton** owning its subplatform's domain, executed/managed by the admin
  within the platform root. Each **reports to admin** on every run/step; no
  unilateral spend/settlement/funding/paywall erection.

## Reporting and 0-funding

- Mandatory reporting line: **eagent → admin → operator/audit** (states,
  transitions, actions, evidence, errors/blockers, funding/paywall/settlement signals).
- **0-funding principle:** no capital assumed; earn before spend. Live only — no
  demo, testnet, backtest, simulation or free-tier stand-ins; fail closed on
  paid/real-capital actions until authorized and funded.

`registry.json` lists every subplatform with its eagent and component directory
plus automaton/reporting/paywall-funding/admin-authority metadata. The system is
designed to run independently of any external bot framework.
