---
name: paywall-settle-fund
description: Admin-governed live workflows to erect paywalls, settle, and fund from realized and authorized resources only (fail-closed, 0-funding bootstrap compatible).
version: 1
tags: [paywall, settlement, funding, admin, economy, live]
---

# Paywall, Settle, Fund

## Authority
- Admin only. Eagents report signals/opportunities/blockers; they do not unilaterally erect paywalls, settle, or fund.

## Rules
- Fail closed on missing authorization, credentials, float, or realized resources.
- 0-funding bootstrap is live: create real value/offers, collect and settle realized revenue, then fund authorized actions.
- Use configured rails (e.g. Pix-Payment-System African/PIX variants) only when valid and authorized.
- Record and report every action with evidence and status.
