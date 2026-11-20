# African use cases — usage & configuration (Java / payment-system)

The original `payment-system` module is a Spring Boot PIX app (Efí/EfiPay). To
adapt it for African markets, use the same request/response patterns with a
mobile-money/bank provider or aggregator configured per country. The Python and
Rust variants in `../python` and `../rust` include ready-to-use African models
and a configurable gateway; this document provides the equivalent config and
usage template for the Java side.

## Concepts

- **Collect (request-to-pay):** push a prompt to the customer's mobile money
  phone (MSISDN) to approve payment. Primary flow for M-Pesa, MTN MoMo, Airtel
  Money, Orange Money, Wave, etc.
- **Disburse (payout):** send funds to a wallet or bank account.
- **Identifiers:** phone/MSISDN first (e.g. 2547..., 25677...), plus optional
  account/document/country.
- **Currencies:** KES, UGX, TZS, RWF, NGN, GHS, ZAR, ZMW, MWK, ETB, XOF, XAF, MZN, etc.
- **Provider config:** base URL, API key/credentials, and endpoint mapping per
  market (M-Pesa Daraja, MTN MoMo API, Airtel, or an aggregator).

## Configuration template (`application-african.yml` or env)

```yaml
payments:
  provider: mtn-momo        # mpesa | mtn-momo | airtel-money | orange-money | wave | aggregator
  base-url: ${PAYMENT_API_BASE_URL:https://sandbox.momodeveloper.mtn.com}
  api-key: ${PAYMENT_API_KEY:}
  callback-url: ${PAYMENT_CALLBACK_URL:https://merchant.example/webhooks/payment}
  default-country: ${DEFAULT_COUNTRY:UG}
  default-currency: ${DEFAULT_CURRENCY:UGX}
  endpoints:
    collect: /collection/v1_0/requesttopay
    disburse: /disbursement/v1_0/transfer
    status: /collection/v1_0/requesttopay/{reference}
```

Example env (`.env` / deployment secrets, never committed):

```env
PAYMENT_API_BASE_URL=https://sandbox.momodeveloper.mtn.com
PAYMENT_API_KEY=your-api-key
PAYMENT_CALLBACK_URL=https://merchant.example/webhooks/payment
DEFAULT_COUNTRY=UG
DEFAULT_CURRENCY=UGX
# Provider-specific (examples)
MOMO_SUBSCRIPTION_KEY=...
MPESA_CONSUMER_KEY=...
MPESA_CONSUMER_SECRET=...
MPESA_SHORTCODE=174379
MPESA_PASSKEY=...
```

## Usage template (Java)

```java
// Example: mobile money collect (request-to-pay), e.g. MTN MoMo UGX
// Adapt to your service/client classes and provider SDK/HTTP client.

public class AfricanCollectExample {
    public static void main(String[] args) {
        String baseUrl = System.getenv().getOrDefault(
            "PAYMENT_API_BASE_URL", "https://sandbox.momodeveloper.mtn.com");
        String apiKey = System.getenv("PAYMENT_API_KEY");

        // Build a collect/request-to-pay payload (phone/MSISDN-first)
        Map<String, Object> payer = Map.of(
            "name", "Kato Musa",
            "phone", "256771234567",   // MSISDN
            "country", "UG"
        );
        Map<String, Object> payload = Map.of(
            "amount", 50000.0,
            "currency", "UGX",
            "method", "mobile_money",
            "provider", "mtn_momo",
            "payer", payer,
            "reference", "ORDER-UG-2001",
            "description", "Wallet top-up",
            "callback_url", "https://merchant.example/webhooks/momo"
        );

        // POST to {baseUrl}/collection/v1_0/requesttopay with auth headers
        // using your HTTP client (RestTemplate/WebClient/OkHttp) per provider spec.
        // Handle provider response and poll/check status by reference.
    }
}
```

## M-Pesa (Kenya, KES) collect example payload

```json
{
  "amount": 1500.0,
  "currency": "KES",
  "method": "mobile_money",
  "provider": "mpesa",
  "payer": { "name": "Jane Wanjiku", "phone": "254712345678", "country": "KE" },
  "reference": "ORDER-KE-1001",
  "description": "Order payment",
  "callback_url": "https://merchant.example/webhooks/payment",
  "metadata": { "order_id": "1001" }
}
```

## Notes

- Map `collect`/`disburse`/`status` to the exact endpoints and auth scheme of
  your provider (Daraja, MoMo API, Airtel, aggregator) and market.
- PIX remains available in the original Java Pix service for Brazil; use the
  African flow for mobile money/bank rails in African markets.
- Ensure credentials, KYC, and regulatory compliance per country/provider before
  going live.
