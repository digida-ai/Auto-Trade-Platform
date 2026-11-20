---
name: sentry-monitoring
description: Sentry/monitoring skill for live platform health. Watches automaton states, runs, reports, stalled work, failures, blockers, and unresolved live problems; raises alerts so issues are surfaced and resolved as they occur.
version: 1
tags: [monitoring, sentry, watchdog, alerts, health, live]
---

# Sentry Monitoring

Use this skill to keep the platform observable and live issues visible.

## Purpose
- Detect stalled runs, failed/blocked agents, missing reports, and unresolved problems.
- Surface alerts in orchestrator/API/UI with actionable detail.
- Support live-action resolution: every surfaced problem must be addressed, not hidden.

## Checks
- **Stalled**: run or agent in `running/waking` with no progress/update beyond threshold.
- **Blocked/failed**: agents or steps in `blocked`/`failed` with captured errors/blockers.
- **Reporting gaps**: active work without recent reports to admin.
- **Paywall/settle/fund gates**: unmet realized/authorized conditions blocking live actions.
- **Readiness**: constitution/soul/access-method readiness and required rails/config not satisfied.

## Actions
- Run watchdog checks and persist alerts.
- Report alerts to admin and operator/audit surfaces.
- Drive problem-resolution workflow: identify cause, propose/execute live corrective action, verify cleared state.
