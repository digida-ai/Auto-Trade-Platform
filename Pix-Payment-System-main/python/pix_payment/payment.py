"""Payment requests recorded durably and executed against the configured rail.

Every call is real: the gateway performs an HTTP request to the provider/aggregator
configured via PAYMENT_API_BASE_URL and friends. With no configuration the calls
fail closed; no synthetic success is ever recorded.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from .african_service import AfricanPaymentService
from .ledger import Ledger, now_iso, to_minor
from .models import PaymentRequest


class PaymentError(ValueError):
    pass


class PaymentService:
    STATUSES = {"requested", "pending", "settled", "failed", "refunded"}

    def __init__(self, ledger: Optional[Ledger] = None, service: Optional[AfricanPaymentService] = None):
        self.ledger = ledger or Ledger()
        self.service = service or AfricanPaymentService()

    def _record(self, request: PaymentRequest, action: str, authorized_by: Optional[str]) -> str:
        payment_id = uuid.uuid4().hex
        reference = request.reference or uuid.uuid4().hex
        payload = {
            "id": payment_id,
            "reference": reference,
            "method": request.method.value if hasattr(request.method, "value") else str(request.method),
            "provider": request.provider.value if hasattr(request.provider, "value") else request.provider,
            "amount_minor": to_minor(request.amount, request.currency),
            "currency": request.currency.upper(),
            "payer_account_id": None,
            "payee_account_id": None,
            "status": "requested",
            "request_payload": self.ledger.dump_json(request.to_payload()),
            "response_payload": None,
            "authorized_by": authorized_by,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        with self.ledger.transaction() as conn:
            conn.execute(
                """
                INSERT INTO payments
                  (id, reference, method, provider, amount_minor, currency,
                   payer_account_id, payee_account_id, status, request_payload,
                   response_payload, authorized_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload[k] for k in (
                    "id", "reference", "method", "provider", "amount_minor", "currency",
                    "payer_account_id", "payee_account_id", "status", "request_payload",
                    "response_payload", "authorized_by", "created_at", "updated_at",
                )),
            )
        return payment_id

    def _execute(self, action: str, request: PaymentRequest, authorized_by: Optional[str] = None) -> Dict[str, Any]:
        payment_id = self._record(request, action, authorized_by)
        try:
            response = getattr(self.service, action)(request)
        except Exception as exc:
            self._update(payment_id, status="failed", response_payload={"error": str(exc)})
            raise PaymentError(f"{action} failed against the configured rail: {exc}") from exc
        provider_ref = None
        if isinstance(response, dict):
            for key in ("id", "transactionId", "providerReference", "reference", "txid"):
                if response.get(key):
                    provider_ref = str(response[key])
                    break
        status = "pending"
        if isinstance(response, dict) and str(response.get("status", "")).lower() in ("settled", "success", "completed", "paid"):
            status = "pending"  # settlement is only credited by SettlementService after verification
        self._update(payment_id, status=status, response_payload=response, provider_reference=provider_ref)
        return self.get(payment_id)

    def collect(self, request: PaymentRequest, authorized_by: Optional[str] = None) -> Dict[str, Any]:
        return self._execute("collect", request, authorized_by)

    def disburse(self, request: PaymentRequest, authorized_by: Optional[str] = None) -> Dict[str, Any]:
        return self._execute("disburse", request, authorized_by)

    def transfer(self, request: PaymentRequest, authorized_by: Optional[str] = None) -> Dict[str, Any]:
        return self._execute("transfer", request, authorized_by)

    def _update(
        self,
        payment_id: str,
        status: Optional[str] = None,
        response_payload: Any = None,
        provider_reference: Optional[str] = None,
    ) -> None:
        sets, values = [], []
        if status is not None:
            if status not in self.STATUSES:
                raise PaymentError(f"invalid status: {status}")
            sets.append("status = ?")
            values.append(status)
        if response_payload is not None:
            sets.append("response_payload = ?")
            values.append(self.ledger.dump_json(response_payload))
        if provider_reference is not None:
            sets.append("provider_reference = ?")
            values.append(provider_reference)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(now_iso())
        values.append(payment_id)
        with self.ledger.transaction() as conn:
            conn.execute(f"UPDATE payments SET {', '.join(sets)} WHERE id = ?", tuple(values))

    def get(self, payment_id: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM payments WHERE id = ?", (payment_id,))

    def get_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM payments WHERE reference = ?", (reference,))

    def list(self, limit: int = 100) -> list:
        return self.ledger.all("SELECT * FROM payments ORDER BY created_at LIMIT ?", (max(1, min(limit, 1000)),))

    def refresh_status(self, reference: str) -> Dict[str, Any]:
        """Query the real rail for a payment's current state and persist it."""
        record = self.get_by_reference(reference)
        if not record:
            raise PaymentError(f"unknown payment reference: {reference}")
        try:
            response = self.service.status(reference)
        except Exception as exc:
            raise PaymentError(f"status check failed against the configured rail: {exc}") from exc
        raw = str(response.get("status", response.get("state", ""))) if isinstance(response, dict) else ""
        status = "pending"
        lowered = raw.lower()
        if lowered in ("settled", "success", "completed", "paid"):
            status = "settled"
        elif lowered in ("failed", "cancelled", "canceled", "expired"):
            status = "failed"
        elif lowered in ("refunded",):
            status = "refunded"
        self._update(record["id"], status=status, response_payload=response)
        return self.get(record["id"])
