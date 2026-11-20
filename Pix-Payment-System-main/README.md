# Payment System (PIX + African use cases)

Originally a PIX payment system (Brazil instant payments). Adapted to **African
use cases** while keeping the PIX implementation options.

## African adaptation

The system now supports common African payment patterns across the Python and
Rust variants (alongside the original Java PIX app):

- **Mobile money** (dominant rail): M-Pesa, MTN MoMo, Airtel Money, Orange
  Money, Wave, Tigo Pesa, Moov Money and similar providers — phone/MSISDN-first
  flows.
- **Collections (request-to-pay)**: push a payment prompt to a customer's mobile
  money number to collect funds.
- **Disbursements/payouts**: send funds to a mobile wallet or bank account.
- **Bank transfers** and configurable rails via a generic HTTP gateway.
- **Multi-currency**: African currencies such as KES, UGX, TZS, RWF, NGN, GHS,
  ZAR, ZMW, MWK, ETB, XOF, XAF, MZN, BWP, CDF, etc.
- **Party model**: payer/payee identified by phone (MSISDN), name, country,
  account, or document — suited to mobile money and local KYC norms.
- **Configurable provider integration**: set `base_url`/API key/endpoints to
  target your provider or aggregator (e.g. M-Pesa/Daraja, MTN MoMo API, Airtel,
  Flutterwave/Paystack-style gateways) per market.

## Implementation variants

- **Java** — `payment-system/` (Spring Boot app). Original PIX version
  (PixService/controller largely commented out as downloaded).
- **Python** — `python/pix_payment/`:
  - PIX: `Credentials`, `EfiPay`, `PixService` (`pix_create_evp()`,
    `pix_create_charge(chave, valor, devedor[, info_adicionais])`,
    `pix_generate_qr_code(id, output_path)`). No sample debtors or fixed output
    paths remain.
  - African/general: `PaymentMethod`, `MobileMoneyProvider`, `PaymentRequest`,
    `Party`, `AFRICAN_CURRENCIES`, `PaymentGateway`, `AfricanPaymentService`
    (`collect`, `disburse`, `transfer`, `status`, `mobile_money_request`).
  - **Live money layers**: `Ledger` (SQLite, minor units, double-entry),
    `AccountService` (KYC identities), `WalletService` (balances derived from
    entries; overdrafts rejected), `PaymentService` (durable requests executed
    against the configured rail), `SettlementService` (admin-authorised credit,
    idempotent), `verify_webhook` (HMAC-SHA256), and a standard-library HTTP API
    (`python -m pix_payment`).
- **Rust** — `rust/pix-payment-system/`:
  - PIX: `Credentials`, `EfiPay` (client), `PixService` (`pix_create_evp`,
    `pix_create_charge`), `random_string::generate_random_string`.
  - African/general: `PaymentMethod`, `MobileMoneyProvider`, `PaymentRequest`,
    `Party`, `african_currencies()`, `PaymentGateway`, `AfricanPaymentService`
    (`collect`, `disburse`, `transfer`, `status`, `mobile_money_request`).

## Live API (Python)

Run from `Pix-Payment-System-main/python`:

```powershell
python -m pix_payment
```

Endpoints (JSON): `GET /health`, `POST /accounts`, `GET /accounts`,
`POST /wallets` (`account_id`, `currency`), `GET /wallets/{id}/balance`,
`POST /payments/collect|disburse|transfer`, `GET /payments/{id}`,
`POST /payments/{reference}/refresh-status`, `POST /settlements`,
`POST /webhooks` (raw body + `X-Payment-Signature`).

Configuration (environment):

- `PAYMENT_API_BASE_URL`, `PAYMENT_API_KEY`, `PAYMENT_WEBHOOK_SECRET` — real rail
  and webhook secret.
- `PAYMENT_API_HOST` / `PAYMENT_API_PORT` — bind address (default `127.0.0.1:8790`).
- `PIX_PAYMENT_API_KEY` — required for every mutating call. Without it the API
  serves health and reads but returns `503` on writes.
- `PIX_PAYMENT_DB` — ledger path (default `python/pix_payment/payment-ledger.sqlite3`).

Fail-closed rules: no rail configured ⇒ payment calls fail; settlement needs a
verified provider success or a valid HMAC webhook event and an explicit
`authorized_by`; a wallet is credited at most once per payment; debits cannot
exceed the ledger balance; no sample identities or balances are inserted.

## Notes

- PIX flows remain available for Brazil. The African layer is provider-agnostic:
  configure the gateway to the mobile money/bank API you use in each country.
- Credentials come from the Java app's `credentials.json` for PIX; the generic
  gateway uses `PAYMENT_API_BASE_URL` / `PAYMENT_API_KEY` (or explicit config).
- Real provider integrations require valid credentials, correct endpoint mapping,
  and compliance with local regulations/KYC per market.
