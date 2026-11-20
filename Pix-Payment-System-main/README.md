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
    `pix_create_charge(chave, valor)`), `generate_random_string`.
  - African/general: `PaymentMethod`, `MobileMoneyProvider`, `PaymentRequest`,
    `Party`, `AFRICAN_CURRENCIES`, `PaymentGateway`, `AfricanPaymentService`
    (`collect`, `disburse`, `transfer`, `status`, `mobile_money_request`).
- **Rust** — `rust/pix-payment-system/`:
  - PIX: `Credentials`, `EfiPay` (client), `PixService` (`pix_create_evp`,
    `pix_create_charge`), `random_string::generate_random_string`.
  - African/general: `PaymentMethod`, `MobileMoneyProvider`, `PaymentRequest`,
    `Party`, `african_currencies()`, `PaymentGateway`, `AfricanPaymentService`
    (`collect`, `disburse`, `transfer`, `status`, `mobile_money_request`).

## Notes

- PIX flows remain available for Brazil. The African layer is provider-agnostic:
  configure the gateway to the mobile money/bank API you use in each country.
- Credentials come from the Java app's `credentials.json` for PIX; the generic
  gateway uses `PAYMENT_API_BASE_URL` / `PAYMENT_API_KEY` (or explicit config).
- Real provider integrations require valid credentials, correct endpoint mapping,
  and compliance with local regulations/KYC per market.
