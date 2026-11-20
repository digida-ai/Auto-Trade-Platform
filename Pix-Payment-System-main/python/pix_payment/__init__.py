"""Payment system — Python variant, adapted for African use cases.

Includes the original PIX flows (PixService, EfiPay, Credentials) and a
generalized layer for African rails: mobile money (M-Pesa, MTN MoMo, Airtel
Money, Orange Money, Wave, etc.), bank transfers, multi-currency, and
phone/MSISDN-first payments via a configurable gateway.
"""

from .credentials import Credentials
from .efi_client import EfiPay, ELException
from .pix_service import PixService
from .random_string import generate_random_string
from .gateway import PaymentGateway, PaymentGatewayError
from .african_service import AfricanPaymentService
from .models import (
    PaymentMethod,
    MobileMoneyProvider,
    PaymentRequest,
    Party,
    AFRICAN_CURRENCIES,
)

__all__ = [
    "Credentials",
    "EfiPay",
    "ELException",
    "PixService",
    "generate_random_string",
    "PaymentGateway",
    "PaymentGatewayError",
    "AfricanPaymentService",
    "PaymentMethod",
    "MobileMoneyProvider",
    "PaymentRequest",
    "Party",
    "AFRICAN_CURRENCIES",
]
__version__ = "0.2.0"
