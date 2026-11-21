# AutoTrading Ideal Platform

One codebase: every AutoTrading functionality, fused into this single project.

All components live in this one repo — nothing was dropped or split out when the
repos were consolidated.

**Automaton layer (musashaban port, integrated):** rules/constitution, soul,
access methods, and paywall-building/settlement/funding logic are ported from
`D:\AI\automation\musashaban\` (synthesized, **no secrets copied**) into
`musashaban-port/`. Every agent is modeled as an automaton; every eagent reports
to the admin; the admin erects paywalls, settles, and funds (fail-closed,
0-funding by default). Existing platform design and functionality are preserved.

## Components

| Component | What it is |
|---|---|
| `agent-desktop-main` | Computer use for AI agents — see and operate desktop apps on macOS, Windows, Linux via the OS accessibility tree |
| `agentic-inbox-main` | Self-hosted email client with an AI agent (Cloudflare Workers) |
| `AutoHedge-main` | Hedge trading |
| `claude-ads-main` | Ads tooling (Claude ads) |
| `FinceptTerminal-main` | Fincept Terminal — enterprise: real-time data, multi-agent AI research, live broker |
| `freellmapi-main` | FreeLLMAPI — 87 free LLM providers, 1,313 free model endpoints behind one OpenAI-compatible endpoint |
| `grok-jev-router-main` | grok-jev-router — Jev decides, Grok Bot executes, humans control irreversible actions |
| `hyperframes-main` | Video generation |
| `LibreChat-main` | LibreChat — open-source chat platform |
| `Open-Higgsfield-AI-main` | Open-source alternative to Higgsfield — generate AI images and videos |
| `Open-LLM-VTuber-main` | Open-LLM-VTuber — desktop voice/avatar app |
| `Pix-Payment-System-main` | Payment system adapted for African use cases (mobile money, bank transfers, multi-currency, phone-first) + PIX — Java app (`payment-system/`), Python package (`python/pix_payment/`), Rust crate (`rust/pix-payment-system/`) |
| `Scrapling-main` | Scrapling — web scraping |
| `spider-ai--trading-bot-main` | Spider AI Trading Bot — copy trading, latency arbitrage, 5-min sniping, synth-powered edge arbitrage |
| `vibetrading-main` | VibeTrading — trading (Python package) |

## Layout

One project root. Each component is a sub-package; they can be built, tested and
shipped independently while sharing this single codebase.

- `agents/` — **standalone** platform agent system (independent of OpenMausBot):
  the admin agent (`agents/admin-agent.md`) and one eagent per subplatform
  (`agents/eagents/`, catalogued in `agents/registry.json`). Modeled as
  **automata** (constitution/soul, states/transitions) with mandatory reporting
  (**eagent → admin → operator/audit**); admin authority to erect paywalls,
  settle, and fund (realized/authorized only, fail-closed).
- `musashaban-port/` — ported automaton/rules/soul/access/paywall-funding layer
  (`constitution.md`, `soul.md`, `access-methods.md`, `paywall-funding.md`,
  `automaton-model.md`, manifest). No secrets copied.
- `orchestrator/` — **local orchestration server** (admin runtime): coordinates
  agents, routes tasks to eagents, tracks runs, automaton states and reports, and
  executes actions with local tool capabilities. Supports Primary Initiative/
  Autorun (including trading from 0 funding) with live feedback.
- `musashaban-port/payment/` — ported payment & settlement system (real name,
  used by the orchestrator): RudiPay routers/faucets, funding, withdrawals,
  SMS-OTP, auto-settle — all fail-closed; data in `orchestrator/state/payment.db`.
  Operator accounts/identities in `orchestrator/payment/accounts.json`
  (TrustWallet: `0x61f9FAC0A44e0D975673CB97fc4A615B16398846`).
- `orchestrator/sentry.py`, `orchestrator/surface.py` — Sentry department
  (deployment/hosting/security watcher; finds only, never acts) and the
  surfacing probes (Cloudflare, Tailscale, IPFS).
- `musashaban-port/deployment/` — Sentry department agent artifacts
  (`agents/specs/sentry.json`, `agents/eagents/sentry.md`, `.env.example`).

- `workflows/` — **catalogue of 20 agentic LLM workflows** (prompt chaining
  through swarm handoffs). Patterns are compartment-agnostic: the five-compartments
  cascade (Social and Welfare → Business and Trade → Politics and Governance →
  Science and Research → Religious and Mystical Organization) is organized by an
  agentic necessity decision, not fixed tables. Each pattern carries fused
  components and the seven-layer anatomy of a self-replicating compartmentalized
  agent. Canonical data lives in `orchestrator/workflows.py`;
  `workflows/catalogue.json` is exported from it; the deep architecture is
  `workflows/ARCHITECTURE.md`. Stages execute **in their subplatform
  environments** (scope, toolchains, services, guardrails, posture derived
  from `agents/specs/*.json`); cross-environment handoffs are mediated by the
  Admin Top Layer and recorded on the run.

## Agentic workflow catalogue

Every task can be shaped as one of 20 patterns (prompt chaining,
parallelization, orchestrator-worker, evaluator-optimizer, router, autonomous,
reflexion, ReWOO, plan-and-execute, map-reduce, HITL, tool use, debate, RAG,
CoT/CoVe, ensemble, memory, semantic routing, hierarchical planning, swarm).
When a pattern reaches its goal — and is attested as achieved — the Admin Top
Layer decides which compartments are necessary and replicates into them:
a Department Agent and an Admin Agent per compartment.

```bat
python -m orchestrator.workflows --list
python -m orchestrator.workflows --select "audit the trading policy"
python -m orchestrator.workflows --decide "audit the trading policy" --workflow 14
python -m orchestrator.workflows --export
```

The orchestrator serves the data at `GET /workflows`, `GET /workflows/{id|slug}`,
`GET /admin/compartments`, `GET /stack` (6-layer architecture + environments +
data products + governance) and `GET /data/products`; it previews compartment
decisions at
`POST /workflows/decide` and executes a run at `POST /workflows/execute`
(`mode: plan|attest`, `achieved`, `replicate`). Runs are fail-closed: a blocked
component or verifier closes its stage gate, a closed gate skips downstream
stages, no stage may continue without a caller-attested achievement, the cascade
only spawns compartments above the necessity threshold, and the first failed
spawn halts it. The engine makes no model calls, runs no shell commands, and
never settles or funds — it is the skeleton, the gates, and the attestation
verdicts. See `workflows/README.md` for the catalogue and `workflows/ARCHITECTURE.md`
for the deep architecture.

## Dependencies: one shared tree, auto-installed

The whole platform uses **one shared `node_modules`** (root `package.json`
workspaces) and **one shared Python environment** (root `requirements.txt` →
root `.venv`). Components do not get their own dependency copies.

```bat
npm run init
```

`npm start` runs the same check automatically on first start and whenever a
dependency manifest changes; it skips the work when everything is current. Set
`SKIP_INIT=1` to bypass it. The initializer installs the shared Node workspaces
with **Node 24** (the single version that satisfies both LibreChat `>=24` and
freellmapi `<25`), creates the shared `.venv` with Python 3.12, and writes the
free/local defaults into `.env`. Caches live under `.cache/` (or set
`UV_CACHE_DIR` / `npm_config_cache`).

`node scripts/init-platform.js --component <name>` only validates that a
component's manifest is covered by the shared tree and regenerates its agent
files — it never installs into the component folder. On npm 12 the initializer
passes `--allow-remote=all --allow-git=all`, because the workspace tree contains
a CDN tarball (xlsx) and a git dependency (Electron's node-gyp).

The generated `.env` sets `JEV_OFFLINE=1`, which makes `grok-jev-router` use its
bundled deterministic local evaluator instead of a paid TypeSafe key. The local
evaluator stays fail-closed (risky/external actions still resolve to
`ask_human`), so 0-funding and fail-closed behaviour are preserved. Add your own
keys to `.env` only if you want to use paid providers.

> Note: `hyperframes-main` is a Bun-style monorepo. Its `workspace:*` specifiers
> are normalized to `*` so npm can link its packages into the single shared
> `node_modules`; all component source is otherwise unchanged.

## Start the orchestrator (local server)

From the platform root:

```bat
npm start
```

This initializes dependencies if needed, starts the Python orchestrator and
opens the dashboard at `http://127.0.0.1:8765/`. Use `npm start -- --no-open` to
skip the browser, or `npm run orchestrator` to run the server directly.

Or, without npm:

```bat
python -m orchestrator
```

Or run `start-orchestrator.bat`. The admin server listens on
`http://127.0.0.1:8765` by default and exposes `GET /health`, `GET /registry`,
`GET /agents`, `GET /agent-states`, `GET /reports`, `GET /runs`, `GET /runs/{id}`,
`GET /runs/{id}/reports`, `GET /components` (per-subplatform readiness),
`GET /observability`, `GET /alerts`, `GET /skills`, `GET /geo/overview`, and
`POST /tasks` for submitting/routing/autorun work to eagents with live automaton
states and reports. See `orchestrator/README.md` for API details. The dashboard's
"Subplatform readiness" panel reports, per component, the toolchain and any
build outputs still needed before it can run.

**0-funding:** no capital/paid accounts assumed by default; live only — no demo,
testnet, backtest, simulation or free-tier stand-ins. Admin erects paywalls,
settles, and funds only from realized/authorized resources (Pix-Payment-System
provides the African mobile-money/PIX rail variants) — fail closed when
conditions are unmet.
