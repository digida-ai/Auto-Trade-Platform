"""Real account records backed by the durable ledger.

An account is the KYC-bearing identity that owns wallets. At least one stable
identifier (phone, email or document) is required; no sample or generated
identities are ever inserted.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .ledger import Ledger, now_iso


class AccountError(ValueError):
    pass


class AccountService:
    def __init__(self, ledger: Optional[Ledger] = None):
        self.ledger = ledger or Ledger()

    def create(
        self,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        document: Optional[str] = None,
        country: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        identifiers = [v for v in (phone, email, document) if v and v.strip()]
        if not identifiers:
            raise AccountError("an account requires phone, email or document")
        record = {
            "id": account_id or uuid.uuid4().hex,
            "name": (name or "").strip() or None,
            "phone": (phone or "").strip() or None,
            "email": (email or "").strip() or None,
            "document": (document or "").strip() or None,
            "country": (country or "").strip() or None,
            "status": "active",
            "created_at": now_iso(),
        }
        cols = ", ".join(record)
        marks = ", ".join("?" for _ in record)
        try:
            with self.ledger.transaction() as conn:
                conn.execute(f"INSERT INTO accounts ({cols}) VALUES ({marks})", tuple(record.values()))
        except Exception as exc:
            raise AccountError(f"account could not be created: {exc}") from exc
        return record

    def get(self, account_id: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM accounts WHERE id = ?", (account_id,))

    def find_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM accounts WHERE phone = ?", (phone,))

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.ledger.all("SELECT * FROM accounts ORDER BY created_at LIMIT ?", (max(1, min(limit, 1000)),))
