"""Example: mobile money collect (request-to-pay) for African use cases.

Shows how to configure the gateway for a provider/aggregator and collect
payment from a customer's mobile money wallet (phone/MSISDN-first).

Configure base_url/api_key and endpoint mapping to match your provider
(e.g. M-Pesa Daraja, MTN MoMo API, Airtel, or an aggregator).
"""

import os

# When running from this folder, ensure the package is importable:
#   set PYTHONPATH=..   (Windows)  or  export PYTHONPATH=..
from pix_payment import (
    AfricanPaymentService,
    MobileMoneyProvider,
    Party,
    PaymentGateway,
    PaymentMethod,
    PaymentRequest,
)


def mpesa_collect_example():
    """Collect via M-Pesa (Kenya, KES)."""
    gateway = PaymentGateway(
        base_url=os.environ.get("PAYMENT_API_BASE_URL", "https://api.sandbox.provider.example"),
        api_key=os.environ.get("PAYMENT_API_KEY", "your-api-key"),
        endpoints={
            # Map logical actions to your provider's paths.
            "collect": "/mpesa/collect",
            "disburse": "/mpesa/disburse",
            "transfer": "/transfers",
            "status": "/transactions/status",
        },
    )
    service = AfricanPaymentService(gateway)

    request = AfricanPaymentService.mobile_money_request(
        amount=1500.00,
        currency="KES",
        phone="254712345678",  # MSISDN
        provider=MobileMoneyProvider.MPESA,
        country="KE",
        name="Jane Wanjiku",
        reference="ORDER-KE-1001",
        description="Order payment",
        callback_url="https://merchant.example/webhooks/payment",
        metadata={"order_id": "1001"},
    )

    # Sends a request-to-pay prompt to the customer's phone.
    # response = service.collect(request)
    # print(response)
    return request.to_payload()


def mtn_momo_collect_example():
    """Collect via MTN MoMo (Uganda, UGX)."""
    gateway = PaymentGateway(
        base_url=os.environ.get("MOMO_API_BASE_URL", "https://sandbox.momodeveloper.mtn.com"),
        api_key=os.environ.get("MOMO_API_KEY"),
        headers={
            # Provider-specific headers (example)
            # "X-Target-Environment": "sandbox",
            # "Ocp-Apim-Subscription-Key": os.environ.get("MOMO_SUBSCRIPTION_KEY", ""),
        },
        endpoints={
            "collect": "/collection/v1_0/requesttopay",
            "disburse": "/disbursement/v1_0/transfer",
            "status": "/collection/v1_0/requesttopay/{reference}",
        },
    )
    service = AfricanPaymentService(gateway)

    request = PaymentRequest(
        amount=50000.0,
        currency="UGX",
        method=PaymentMethod.MOBILE_MONEY,
        provider=MobileMoneyProvider.MTN_MOMO,
        payer=Party(name="Kato Musa", phone="256771234567", country="UG"),
        country="UG",
        reference="ORDER-UG-2001",
        description="Wallet top-up",
        callback_url="https://merchant.example/webhooks/momo",
        metadata={"purpose": "topup"},
    )

    # response = service.collect(request)
    # print("status:", service.status("ORDER-UG-2001"))
    return request.to_payload()


if __name__ == "__main__":
    print("M-Pesa collect payload:", mpesa_collect_example())
    print("MTN MoMo collect payload:", mtn_momo_collect_example())
