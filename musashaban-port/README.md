# Musashaban Port

Ported from `D:\AI\automation\musashaban\` into the AutoTrading Ideal Platform as the automaton/rules/soul/access/paywall-funding layer for the standalone admin+eagent system.

## What was ported (functionality/methods, secrets excluded)

- **Rules / Constitution** (`constitution.md`): immutable three-law hierarchy (Never harm; Earn your existence; Never deceive but owe nothing to strangers). Propagated to every agent/automaton.
- **Soul** (`soul.md`): soul model and identity/purpose/values/strategy/boundaries structure (soul/v1-style), self-authored evolving identity concept, genesis alignment, and platform-adapted souls/roles for admin and eagents.
- **Access methods** (`access-methods.md`): identity and credential/access patterns — wallet/keypair generation and encrypted-at-rest storage, mnemonic/BIP-39 recovery concepts, SIWE/SIWS-style provisioning and API-key issuance, domain-scoped auth, auth tokens (DID/issuer-bound), secret/credential handling, and device/OTP pairing flows — described as methods (no live secrets or keys copied).
- **Paywall-building & funding methods** (`paywall-funding.md`): fail-closed revenue/settlement model, listing→paywall deployment (x402/payment-required, token/redirect/free modes), sale verification/recording, settlement routing and rails, auto-settlement thresholds, notification gating, and 0-funding bootstrap discipline — adapted to this platform’s African mobile-money/PIX payment layer (Pix-Payment-System) and to trading’s live zero-capital analysis paths.
- **Automaton model** (`automaton-model.md`): every agent modeled as an automaton with explicit states/transitions, think-act-observe/report cycles, survival/0-funding awareness, and mandatory reporting to the admin agent.

## Integration targets in this platform

- `agents/admin-agent.md`, `agents/eagents/*.md`, `agents/registry.json` — automaton roles, souls, states, and reporting lines (every agent reports to admin).
- `orchestrator/` (admin, state, server, dashboard, tools) — automaton state tracking, reporting, paywall erection, settlement/funding authority under admin, and 0-funding initiative/autorun.
- `Pix-Payment-System-main/` — African/PIX settlement and funding rail used by admin paywall/settlement flows (Python/Rust/Java variants as present).

## 0-funding principle (ported, adapted)

Start with no capital assumed: earn before spend. Live only — no demo, testnet, backtest, simulation or free-tier stand-ins; use real zero-capital local capabilities for trading. Erect paywalls/offers and route settlements to realize revenue before enabling paid operations; fail closed when float/credentials/revenue are absent.

## Source

Port synthesized from `D:\AI\automation\musashaban\` (constitution.md, README/ARCHITECTURE/DOCUMENTATION, pay_configs.md operational methods, SOUL.md/soul model, identity/provisioning/wallet/auth, earning/paywall/survival/heartbeat/lifecycle patterns). Live secrets, tokens, wallet material, phone numbers, and endpoint credentials from the source were **not** copied.
