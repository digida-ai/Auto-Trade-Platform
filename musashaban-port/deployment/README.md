# Sentry — the deploy, hosting and security sentry department agent

The **sentry** is a new Automaton department agent (registered in
`agents/registry.json`, spec `agents/specs/sentry.json`) that **watches** the
platform's deployment, hosting and security posture and reports findings to
the admin. It never moves money, deploys, or changes policy — findings are
audit-ready and wait for a human.

## What sentry reads

* `orchestrator/surface.py` — surfacing probes: IPFS daemon, Cloudflare
  tunnel binaries, Tailscale CLI (`cloudflared`, `tailscale`, `ipfs`).
* `orchestrator/sentry.py` — `get_sentry_status()`: deployment, hosting,
  security posture.
* `D:\Work\Dev\gapinvex\deploy\*` — hosting facts (gapinvex production
  config, service topology, security CORS/rate-limits).
* `orchestrator/observability.py` — active alerts, protected paths.

## Security posture surface

`musashaban_roles` (human-gated sentry roles from musashaban, here as a
reference list): `address_confirm`, `stock_release`, `dropship_dispatch`,
`delivery_confirm`, `ad_spend`, `payout_release` — each carries `risk`
(dangerous/caution) and `proof` (e.g. "completed on-chain payout is on
record", "never handed over automatically").

## Notes for operators

* S2/surfacing: IPFS/Cloudflare/Tailscale "available" means the CLI/daemon is
  present; readiness != deployed. Ticket runs the surface.
* `maintainers`: any change to sentry's rules needs a constitution-aware
  constitution bump and a re-run of `scripts/generate-agents.js --names=sentry`.
