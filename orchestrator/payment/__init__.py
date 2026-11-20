"""musashaban payment & settlement — Python port.

Source-of-truth TS modules: ``D:\\AI\\automation\\musashaban\\src\\payments\\``
(rudi pay, funding, withdrawal, sms-otp, with platform adaptation into
``orchestrator/store.py`` and ``orchestrator/payment/`` glue).

Structure:
  * ``accounts``   — operator accounts/config (created by the operator; see
    ``accounts.py`` for the captured automaton facts)
  * ``db``         — payment SQLite schema (``orchestrator/state/payment.db``)
  * ``rudipay``    — RudiPayManager: routers, faucets, routing, settlement, disputes
  * ``funding``    — funding requests/collections/development needs (port of funding.ts)
  * ``withdrawal`` — two-step withdrawal gate (port of withdrawal.ts)
  * ``otp``        — SMS OTP gate (port of sms-otp.ts)
  * ``settle``     — auto-settle surplus to ``autoSettleTo`` (fail-closed)

Platform glue: ``orchestrator/store.py`` (``ensure_payment_schema`` + payment_tx
table), ``orchestrator/admin.py`` (``record_funding`` + dispute routes), and
``orchestrator/server.py`` (``GET /admin/settlement/status``).
"""

from .accounts import load_accounts
from .db import PAYMENT_DB, connect_db
from .rudipay import RudiPay
from .funding import (
    create_funding_request,
    list_funding_requests,
    add_collection,
    cancel_funding,
    list_collections,
    create_development_need,
    list_development_needs,
    update_development_need,
)
from .withdrawal import (
    create_withdrawal,
    confirm_withdrawal,
    submit_withdrawal,
    confirm_manual_withdrawal,
    list_withdrawals,
)
from .otp import create_challenge, ingest_sms, authorize, list_challenges
from .settle import maybe_settle, funding_state_auto_threshold

__all__ = [
    "RudiPay",
    "load_accounts",
    "connect_db",
    "create_funding_request", "list_funding_requests", "add_collection", "cancel_funding",
    "list_collections", "create_development_need", "list_development_needs",
    "update_development_need",
    "create_withdrawal", "confirm_withdrawal", "submit_withdrawal",
    "confirm_manual_withdrawal", "list_withdrawals",
    "create_challenge", "ingest_sms", "authorize", "list_challenges",
    "maybe_settle", "funding_state_auto_threshold",
]
