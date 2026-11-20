"""Admin-authorised settlement: verify a real payment, then credit a wallet once.

Settlement never invents money. It requires either a successful status returned
by the configured payment rail or a webhook bearing a valid HMAC signature made
with PAYMENT_WEBHOOK_SECRET. Without verified evidence it fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Dict, Optional

from .ledger import Ledger, now_iso, to_minor
from .payment import PaymentService
from .wallet import WalletService, WalletError


class SettlementError(ValueError):
    pass


SUCCESS_STATUSES = {"settled", "success", "successful", "completed", "paid"}


def verify_webhook(body: bytes, signature: Optional[str], secret: Optional[str] = None) -> bool:
    """Constant-time HMAC-SHA256 verification of a provider webhook body."""
    secret = secret or os.environ.get("PAYMENT_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    supplied = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, supplied)


class SettlementService:
    def __init__(self, ledger: Optional[Ledger] = None):
        self.ledger = ledger or Ledger()
        self.payments = PaymentService(self.ledger)
        self.wallets = WalletService(self.ledger)

    def _status_from_rail(self, reference: str) -> Dict[str, Any]:
        return self.payments.service.status(reference)

    def settle(
        self,
        payment_id: str,
        authorized_by: str,
        wallet_id: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not authorized_by or not authorized_by.strip():
            raise SettlementError("settlement requires an authorizer (admin only)")
        payment = self.payments.get(payment_id)
        if not payment:
            raise SettlementError(f"unknown payment: {payment_id}")
        existing = self.ledger.one("SELECT * FROM settlements WHERE payment_id = ?", (payment_id,))
        if existing:
            return existing

        provider_status = None
        rail_error = None
        try:
            provider_status = self._status_from_rail(payment["reference"])
        except Exception as exc:
            rail_error = str(exc)

        verified = False
        if isinstance(provider_status, dict):
            state = str(provider_status.get("status", provider_status.get("state", ""))).lower()
            verified = state in SUCCESS_STATUSES
        if not verified and evidence and evidence.get("webhook_event_id"):
            event = self.ledger.one(
                "SELECT * FROM webhook_events WHERE id = ? AND signature_valid = 1",
                (evidence["webhook_event_id"],),
            )
            if event:
                verified = True
        if not verified:
            raise SettlementError(
                "settlement refused: no verified provider success"
                + (f" (status check: {rail_error})" if rail_error else "")
            )

        amount_minor = int(payment["amount_minor"])
        currency = payment["currency"]
        wallet = self.wallets.get(wallet_id) if wallet_id else None
        if wallet_id and not wallet:
            raise SettlementError(f"unknown wallet: {wallet_id}")
        if not wallet:
            wallet = self.ledger.one(
                "SELECT * FROM wallets WHERE account_id = ? AND currency = ?",
                (payment["payee_account_id"], currency),
            )
        if not wallet:
            raise SettlementError("no destination wallet for this payment; create one for the payee and currency")

        proof = {
            "provider_status": provider_status,
            "rail_error": rail_error,
            "evidence": evidence or {},
            "verified_at": now_iso(),
        }
        record = {
            "id": uuid.uuid4().hex,
            "payment_id": payment_id,
            "wallet_id": wallet["id"],
            "amount_minor": amount_minor,
            "currency": currency,
            "authorized_by": authorized_by,
            "evidence": self.ledger.dump_json(proof),
            "created_at": now_iso(),
        }
        with self.ledger.transaction() as conn:
            entry_id = self.wallets._post(
                conn, wallet, "credit", amount_minor, currency,
                reference=f"settlement:{payment['reference']}", kind="settlement",
                authorized_by=authorized_by,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO settlements
                      (id, payment_id, wallet_id, amount_minor, currency, ledger_entry_id, authorized_by, evidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["id"], payment_id, wallet["id"], amount_minor, currency,
                        entry_id, authorized_by, record["evidence"], record["created_at"],
                    ),
                )
            except Exception as exc:
                raise SettlementError(f"settlement record rejected: {exc}") from exc
            conn.execute(
                "UPDATE payments SET status = 'settled', updated_at = ? WHERE id = ?",
                (now_iso(), payment_id),
            )
        return self.for_payment(payment_id)

    def get(self, settlement_id: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM settlements WHERE id = ?", (settlement_id,))

    def for_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM settlements WHERE payment_id = ?", (payment_id,))
