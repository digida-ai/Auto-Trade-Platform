"""Wallet balances derived from an append-only double-entry ledger.

A balance is never stored directly: it is the sum of credit minus debit entries.
Every entry requires an authorizer and a stable reference, so value cannot be
created silently. Debits fail closed on insufficient funds.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from .account import AccountService
from .ledger import Ledger, from_minor, now_iso, to_minor


class WalletError(ValueError):
    pass


class WalletService:
    KINDS = {"settlement", "payout", "transfer", "adjustment", "refund"}

    def __init__(self, ledger: Optional[Ledger] = None):
        self.ledger = ledger or Ledger()
        self.accounts = AccountService(self.ledger)

    def create(self, account_id: str, currency: str) -> Dict[str, Any]:
        if not self.accounts.get(account_id):
            raise WalletError(f"account {account_id} does not exist")
        currency = currency.upper()
        to_minor(1, currency)  # validates the currency is usable
        record = {
            "id": uuid.uuid4().hex,
            "account_id": account_id,
            "currency": currency,
            "created_at": now_iso(),
        }
        try:
            with self.ledger.transaction() as conn:
                conn.execute(
                    "INSERT INTO wallets (id, account_id, currency, created_at) VALUES (?, ?, ?, ?)",
                    (record["id"], record["account_id"], record["currency"], record["created_at"]),
                )
        except Exception as exc:
            raise WalletError(f"wallet could not be created: {exc}") from exc
        return record

    def get(self, wallet_id: str) -> Optional[Dict[str, Any]]:
        return self.ledger.one("SELECT * FROM wallets WHERE id = ?", (wallet_id,))

    def list(self, account_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        if account_id:
            return self.ledger.all(
                "SELECT * FROM wallets WHERE account_id = ? ORDER BY created_at LIMIT ?",
                (account_id, max(1, min(limit, 1000))),
            )
        return self.ledger.all("SELECT * FROM wallets ORDER BY created_at LIMIT ?", (max(1, min(limit, 1000)),))

    def balance(self, wallet_id: str) -> Dict[str, Any]:
        wallet = self.get(wallet_id)
        if not wallet:
            raise WalletError(f"wallet {wallet_id} does not exist")
        row = self.ledger.one(
            """
            SELECT
              COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount_minor ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN direction = 'debit'  THEN amount_minor ELSE 0 END), 0)
              AS minor
            FROM ledger_entries WHERE wallet_id = ?
            """,
            (wallet_id,),
        )
        minor = int(row["minor"]) if row else 0
        return {
            "wallet_id": wallet_id,
            "account_id": wallet["account_id"],
            "currency": wallet["currency"],
            "amount_minor": minor,
            "amount": from_minor(minor, wallet["currency"]),
        }

    def entries(self, wallet_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self.ledger.all(
            "SELECT * FROM ledger_entries WHERE wallet_id = ? ORDER BY created_at, id LIMIT ?",
            (wallet_id, max(1, min(limit, 1000))),
        )

    # --- internal posting, used only by settlement/payout services --------
    @staticmethod
    def _post(
        conn,
        wallet: Dict[str, Any],
        direction: str,
        amount_minor: int,
        currency: str,
        reference: str,
        kind: str,
        authorized_by: str,
    ) -> str:
        if direction not in ("credit", "debit"):
            raise WalletError("direction must be credit or debit")
        if kind not in WalletService.KINDS:
            raise WalletError(f"unsupported ledger kind: {kind}")
        if not authorized_by or not authorized_by.strip():
            raise WalletError("a ledger entry requires an authorizer")
        if not reference or not reference.strip():
            raise WalletError("a ledger entry requires an idempotency reference")
        if currency.upper() != wallet["currency"]:
            raise WalletError(f"currency {currency} does not match wallet {wallet['currency']}")
        if direction == "debit":
            available = conn.execute(
                """
                SELECT COALESCE(SUM(CASE WHEN direction = 'credit' THEN amount_minor ELSE -amount_minor END), 0) AS minor
                FROM ledger_entries WHERE wallet_id = ?
                """,
                (wallet["id"],),
            ).fetchone()["minor"]
            if int(available) < amount_minor:
                raise WalletError("insufficient funds")
        entry_id = uuid.uuid4().hex
        try:
            conn.execute(
                """
                INSERT INTO ledger_entries
                  (id, wallet_id, direction, amount_minor, currency, reference, kind, authorized_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id, wallet["id"], direction, amount_minor, currency.upper(),
                    reference, kind, authorized_by, now_iso(),
                ),
            )
        except Exception as exc:
            # A duplicate reference means the operation already posted.
            if "UNIQUE constraint failed" in str(exc):
                existing = conn.execute(
                    "SELECT id FROM ledger_entries WHERE reference = ? AND wallet_id = ? AND direction = ? AND kind = ?",
                    (reference, wallet["id"], direction, kind),
                ).fetchone()
                if existing:
                    return existing["id"]
            raise WalletError(f"ledger entry rejected: {exc}") from exc
        return entry_id
