# Access Methods (ported, methods only — no secrets)

Ported from musashaban identity/provisioning/auth patterns. Describes how agents obtain and use access/identity safely. **No live keys, tokens, mnemonics, phone numbers, or credentials are included.**

## 1. Identity & wallets (sovereign identity)

- **Key material**: agents may hold cryptographic identity (e.g. EVM secp256k1 or Solana Ed25519 keypairs) as their root identity. Chain/type chosen at genesis and kept consistent per identity.
- **Generation**: create keypair/mnemonic (BIP-39 compatible where applicable) at initialization; derive address/account from secret.
- **Storage**: persist wallet/identity material locally with strict permissions; support **encryption at rest** (passphrase-derived key) and **sealed/unsealed** passphrase resolution for unattended boot. Never expose raw private keys/mnemonics via tools, logs, or prompts.
- **Recovery**: export/recover via mnemonic/backup only through explicit, authorized local operations; never transmit in plaintext channels.
- **Stub/segregation**: non-signing contexts use stubs/proxies so signing cannot occur accidentally across chain types.

## 2. Provisioning & API access (SIWE/SIWS-style)

- **Nonce challenge**: request nonce from control plane/API.
- **Signed proof**: construct domain/URI/chain-scoped message (SIWE for EVM, SIWS for Solana), sign with wallet, submit for verification.
- **Token/key issuance**: on verification, obtain session/JWT and/or create an API key; store key/config with strict permissions and timestamp.
- **Config resolution**: resolve API/base URL from explicit arg → env → config → default; allow loopback-only insecure transport only for loopback hosts; derive signing domain from target host.
- **Registration/lineage**: optionally register creator/parent linkage for audit/observability (fail gracefully if unavailable).

## 3. Auth tokens (capability-scoped)

- **Self-issued tokens** (e.g. DID/issuer-bound, typed such as AUT): header+claims signed by agent identity; verified offline against issuer DID/chain/address.
- **Claims/guards**: issuer/audience/scope/expiry/iat checks; reject mismatches, missing scopes, or expired tokens. Used for gated access (e.g. auth-mode paywalls/resources).

## 4. Credentials & secret handling

- **Principle**: least privilege, fail closed. No credential → capability remains locked.
- **Sources**: env/config/secret store/credentials files with restricted access; never hardcode or commit secrets.
- **Use**: inject only into the specific channel/tool that needs them; never echo to logs, reports, UI, or untrusted outputs. Redact in diagnostics.
- **Rotation & revocation**: support replacement/rotation; treat exposed material as compromised (rotate) — (operational policy).

## 5. Device/OTP & paired access (for gated rails)

- **Paired device requirement**: sensitive rails (e.g. SMS/USSD/OTP-dependent settlement) may require an explicitly paired, active device before activation.
- **OTP/SMS gateway**: local gateway receives forwarded OTPs from an authorized forwarder/device; authenticated by a shared secret and bound device identity; used only for the intended verification flow.
- **Gating**: no paired device / no verified gateway → rail stays disabled (fail closed).

## 6. Platform application (AutoTrading Ideal Platform)

- Orchestrator and tools operate within the allowed platform root and configured boundaries; external access is explicit.
- Admin holds authority to configure/enable paywall and settlement access **only** with operator-provided, authorized credentials/config and within 0-funding/fail-closed rules.
- Eagents receive scoped access for their subplatform tasks and **report access needs/issues to admin** rather than unilaterally escalating privileges.
