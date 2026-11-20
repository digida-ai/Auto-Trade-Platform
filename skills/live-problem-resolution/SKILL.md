---
name: live-problem-resolution
description: Resolve problems live as they surface. No mocks, placeholders, hardcoded artifacts, or sleep-defaulting shims; diagnose real state, take corrective action, and verify outcome.
version: 1
tags: [live, resolution, incidents, operations]
---

# Live Problem Resolution

## Rules
- Operate on real platform state and real artifacts only.
- When an error/blocker surfaces, capture evidence, report it, and resolve it through concrete actions.
- Do not substitute fake outputs, placeholders, or simulated success.
- Gate spend/funding/settlement on realized and authorized resources; fail closed and report when unmet.

## Workflow
1. Detect and classify problem (config, access, dependency, runtime, data, rail, authorization).
2. Inspect actual state (runs, agent states, reports, files, skills, manifests, alerts).
3. Apply corrective action available in live context.
4. Re-run/verify and report outcome to admin.

## Blocked platforms at boot

1. `GET /bootup` and `GET /activation` on the orchestrator: confirm directives were
   issued as runs, not left as text.
2. `GET /components` and `GET /runs`: read the actual blocker from run output —
   truncated `node_modules`, missing toolchain, or unavailable service.
3. `npm run init` repairs the shared dependency tree (npm 12 needs
   `--allow-remote=all`, which the init script already supplies). Per-component
   repair is `POST /activate` with `targets: [name]`, or
   `node scripts/init-platform.js --component <name>`.
4. Verify with `GET /components/{name}` and `GET /runs/{id}`; only report ready
   when the re-check passes. Never mark a blocked component complete.
