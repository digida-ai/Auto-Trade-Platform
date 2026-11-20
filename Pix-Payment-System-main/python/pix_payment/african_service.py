"""Payment service adapted for African use cases.

Supports mobile money collection (request-to-pay/push to customer phone),
disbursement/payouts, bank transfers and status checks through a configurable
gateway, while keeping PIX flows available via PixService.
"""

from typing import Any, Dict, Optional

from .gateway import PaymentGateway
from .models import PaymentMethod, PaymentRequest, Party, MobileMoneyProvider
from .random_string import generate_random_string


class AfricanPaymentService:
    """Generalized payment service for African rails.

    Configure with a PaymentGateway pointing at your provider/aggregator
    (e.g. mobile money API, payment aggregator) and optional endpoint mapping.
    """

    def __init__(self, gateway: Optional[PaymentGateway] = None):
        self.gateway = gateway or PaymentGateway()

    def collect(self, request: PaymentRequest) -> Dict[str, Any]:
        """Request payment from a customer (mobile money collect / request-to-pay).

        Typical African mobile money flow: push a prompt to the payer's phone
        (MSISDN) to approve and pay.
        """
        if request.payer and not request.payer.phone and request.method == PaymentMethod.MOBILE_MONEY:
            raise ValueError("Mobile money collect requires payer.phone (MSISDN)")
        payload = request.to_payload()
        if not payload.get("reference"):
            payload["reference"] = generate_random_string(12)
        return self.gateway.request("collect", payload)

    def disburse(self, request: PaymentRequest) -> Dict[str, Any]:
        """Send money to a recipient (payout/disbursement), e.g. to a wallet or bank."""
        if request.payee and not (request.payee.phone or request.payee.account):
            raise ValueError("Disbursement requires payee.phone or payee.account")
        payload = request.to_payload()
        if not payload.get("reference"):
            payload["reference"] = generate_random_string(12)
        return self.gateway.request("disburse", payload)

    def transfer(self, request: PaymentRequest) -> Dict[str, Any]:
        """Bank or wallet transfer."""
        payload = request.to_payload()
        if not payload.get("reference"):
            payload["reference"] = generate_random_string(12)
        return self.gateway.request("transfer", payload)

    def status(self, reference: str) -> Dict[str, Any]:
        """Check transaction/payment status by reference or provider id."""
        endpoint_action = "status"
        # If the gateway maps status with {id}, resolve here; otherwise send in body.
        endpoints = self.gateway.endpoints or {}
        if "{id}" in endpoints.get("status", "") or "{reference}" in endpoints.get("status", ""):
            # swap in a resolved action key by sending payload; keep simple:
            payload = {"reference": reference}
        else:
            payload = {"reference": reference}
        return self.gateway.request(endpoint_action, payload)

    @staticmethod
    def mobile_money_request(
        amount: float,
        currency: str,
        phone: str,
        provider: MobileMoneyProvider,
        country: Optional[str] = None,
        name: Optional[str] = None,
        reference: Optional[str] = None,
        description: Optional[str] = None,
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PaymentRequest:
        """Helper to build a mobile money collect request (phone-first)."""
        return PaymentRequest(
            amount=amount,
            currency=currency,
            method=PaymentMethod.MOBILE_MONEY,
            provider=provider,
            payer=Party(name=name, phone=phone, country=country),
            country=country,
            reference=reference,
            description=description,
            callback_url=callback_url,
            metadata=metadata or {},
        )
