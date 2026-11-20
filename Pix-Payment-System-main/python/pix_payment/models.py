"""Payment models adapted for African use cases.

Generalizes the PIX-only flow to support mobile money (dominant across Africa),
bank transfers, and other rails alongside PIX, with multi-currency and
phone/MSISDN-first identifiers.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class PaymentMethod(str, Enum):
    PIX = "pix"
    MOBILE_MONEY = "mobile_money"
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"
    USSD = "ussd"
    CASH = "cash"


class MobileMoneyProvider(str, Enum):
    MPESA = "mpesa"                 # Kenya, Tanzania, etc.
    MTN_MOMO = "mtn_momo"           # Uganda, Ghana, Rwanda, Cameroon, etc.
    AIRTEL_MONEY = "airtel_money"   # Uganda, Kenya, Tanzania, Zambia, etc.
    ORANGE_MONEY = "orange_money"   # West/Central Africa
    WAVE = "wave"                   # Senegal, Côte d'Ivoire, etc.
    TIGO_PESA = "tigo_pesa"
    VODACOM_MPESA = "vodacom_mpesa" # DRC, Tanzania
    MOOV_MONEY = "moov_money"


# Common African currency codes (ISO 4217) used across mobile money rails.
AFRICAN_CURRENCIES = {
    "KES": "Kenya",
    "UGX": "Uganda",
    "TZS": "Tanzania",
    "RWF": "Rwanda",
    "NGN": "Nigeria",
    "GHS": "Ghana",
    "ZAR": "South Africa",
    "ZMW": "Zambia",
    "MWK": "Malawi",
    "ETB": "Ethiopia",
    "XOF": "West African CFA",
    "XAF": "Central African CFA",
    "MZN": "Mozambique",
    "BWP": "Botswana",
    "EGP": "Egypt",
    "MAD": "Morocco",
    "CDF": "DR Congo",
    "SLL": "Sierra Leone",
    "GNF": "Guinea",
    "XOF_SN": "Senegal/CFA",
}


@dataclass
class Party:
    """Payer or payee. Phone/MSISDN is the primary identifier for mobile money."""
    name: Optional[str] = None
    phone: Optional[str] = None      # MSISDN, e.g. +2567..., 2547...
    email: Optional[str] = None
    account: Optional[str] = None    # bank/wallet/account identifier
    country: Optional[str] = None    # ISO 3166-1 alpha-2, e.g. KE, UG, NG
    document: Optional[str] = None   # national ID/tax ID where required

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class PaymentRequest:
    """A payment/charge request generalized for African rails."""
    amount: float
    currency: str
    method: PaymentMethod = PaymentMethod.MOBILE_MONEY
    provider: Optional[MobileMoneyProvider] = None
    payer: Optional[Party] = None
    payee: Optional[Party] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    callback_url: Optional[str] = None
    country: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "amount": self.amount,
            "currency": self.currency,
            "method": self.method.value if isinstance(self.method, Enum) else self.method,
        }
        if self.provider:
            payload["provider"] = (
                self.provider.value if isinstance(self.provider, Enum) else self.provider
            )
        if self.payer:
            payload["payer"] = self.payer.to_dict()
        if self.payee:
            payload["payee"] = self.payee.to_dict()
        if self.reference:
            payload["reference"] = self.reference
        if self.description:
            payload["description"] = self.description
        if self.callback_url:
            payload["callback_url"] = self.callback_url
        if self.country:
            payload["country"] = self.country
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload
