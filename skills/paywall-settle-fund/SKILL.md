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

## Live settlement procedure (Python rail)

1. Configure the rail: `PAYMENT_API_BASE_URL`, `PAYMENT_API_KEY`,
   `PAYMENT_WEBHOOK_SECRET`, and `PIX_PAYMENT_API_KEY` for mutations. With no key,
   writes return 503 — do not work around it.
2. Start the live API: from `Pix-Payment-System-main/python`, run
   `python -m pix_payment`; confirm `GET /health` shows `rail_configured: true`.
3. Create the real identity and wallet: `POST /accounts` (phone/email/document),
   then `POST /wallets` with `account_id` and `currency`.
4. Request payment: `POST /payments/collect` with amount, currency, method,
   provider and payer MSISDN. Persist the returned payment id/reference.
5. Verify: either `POST /payments/{reference}/refresh-status` returns a success
   status from the rail, or an HMAC-signed body arrives at `POST /webhooks` and
   yields a `webhook_event_id`.
6. Settle: `POST /settlements` with `payment_id`, `wallet_id`, `authorized_by`
   (admin) and, when using a webhook, `evidence.webhook_event_id`. The wallet is
   credited once; a second call is idempotent.
7. Verify `GET /wallets/{id}/balance` and report the payment, settlement and
   balance evidence to admin. No verified status means no credit.
