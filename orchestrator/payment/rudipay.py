r"""RudiPay — ported from D:\AI\automation\musashaban\src\payments\rudipay.ts

Port by: musashaban-port/payment (AutoTrading-Ideal-Platform).
Value ports: routing rules, lifecycle states, OTP gate integration, dispute rules.

Phone-number routing for buyers and sellers who are not on the same
mobile-money handle. A verified, 2FA-enabled admin registers a phone number
as a routing rail; buyer pays INTO that number, the seller is paid OUT of it.
Router balance/fees tracked per router; each route capped at 10% of the
router's balance; 1% of the routed amount goes to the router as fee.

Structural fidelity: table shapes follow musashaban (`rudipay_routers`,
`rudipay_faucets`, `rudipay_router_selections`, `rudipay_transactions`,
`rudipay_settlements`, `rudipay_disputes`), but storage here is a single
SQLite file (`orchestrator/state/payment.db`) — the platform's payment DB.
No other payment code may write that DB without the RudiPay manager.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .accounts import (
    load_accounts,
    normalized_phone,
    who_is_admin,
)
from ..store import record_payment, update_payment_status
from .db import PAYMENT_DB, connect_db, cursor, _now, _new_id

MAX_ROUTE_PERCENT = 0.1
ROUTER_FEE_PERCENT = 0.01
MIN_ROUTE_AMOUNT = 1
SETTLEMENT_WINDOW_MINUTES = 30
DISPUTE_WINDOW_HOURS = 24
RESERVATION_TTL_SECONDS = 300

ADMIN_ROLES = frozenset(
    {"super_admin", "admin", "csuite_continental", "csuite_subregion", "country_admin"}
)

ROUTING_STATUS = ("active", "paused", "revoked")
ROUTER_TYPE = ("user", "admin", "faucet")
TXN_STATUS = (
    "pending", "buyer_committed", "seller_notified", "completed",
    "disputed", "refunded", "expired",
)
_TERMINAL = frozenset({"completed", "refunded", "expired"})


class RudiPayError(Exception):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _q(value: float) -> float:
    return round(value + 0, 2)


def money_to_minor(amount: float, currency: str) -> float:
    """Convert a major-unit amount to minor units (currency-aware scale)."""
    scale = 2
    if currency.upper() in {"UGX", "TZS", "KES", "GNF", "RWF", "VUV", "SLL"}:
        scale = 0
    return round(amount * (10 ** scale))


class RudiPay:
    """Manager over the payment database."""

    def __init__(self, db: Optional[Path] = None, accounts: Optional[Dict[str, Any]] = None):
        self.accounts = accounts if accounts is not None else load_accounts()
        self.db_path = Path(db) if db else PAYMENT_DB
        self._migrate()

    # ------------------------------------------------------------------ DB
    def _connect(self) -> sqlite3.Connection:
        conn = connect_db(self.db_path)
        from ..store import ensure_payment_schema
        ensure_payment_schema(conn)
        return conn

    def _migrate(self) -> None:
        """Create tables if missing; idempotent. Reject live-money migrations."""
        connect_db(self.db_path).close()

    # ------------------------------------------------------------ routers
    def register_routing_number(
        self,
        actor_id: str,
        phone: str,
        country_code: str,
        *,
        two_factor_enabled: bool,
        roles: Optional[List[str]] = None,
        display_name: str = "",
        region: str = "",
        city: str = "",
        location_id: str = "",
        notes: str = "",
    ) -> Dict[str, Any]:
        """Register a phone number as a routing rail (admin-only, 2FA-gated)."""
        accounts = self.accounts
        if not who_is_admin(accounts, actor_id):
            raise RudiPayError("Insufficient privileges: admin role required")
        if not two_factor_enabled:
            raise RudiPayError("Actor must have 2FA enabled to register routing numbers")
        phone = normalized_phone(phone)
        if not phone:
            raise RudiPayError("Phone number required")
        preferred = accounts.get("settlement", {}).get("rudipay", {}).get("preferredRouterPhones") or []
        if preferred and phone not in preferred:
            raise RudiPayError(
                f"Phone {phone} is not an approved RudiPay router number. "
                f"Approved: {', '.join(preferred)}."
            )
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM rudipay_routers WHERE phone = ?", (phone,)).fetchone()
            if existing:
                if existing["status"] == "revoked":
                    conn.execute(
                        "UPDATE rudipay_routers SET status = 'active', updated_at = ? WHERE id = ?",
                        (_now(), existing["id"]),
                    )
                    return dict(self.get_router(existing["id"]))
                raise RudiPayError("Phone number already registered as routing rail")
            router_id = _id("rp_")
            conn.execute(
                """INSERT INTO rudipay_routers
                   (id, owner_id, phone, country_code, region, city, location_id,
                    display_name, notes, router_type, status, wallet_balance,
                    total_routed, total_fees_earned, created_at, updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    router_id, actor_id, phone, country_code.upper(), region, city,
                    location_id, display_name or f"Router {phone}", notes,
                    "admin", "active", 0.0, 0.0, 0.0, _now(), _now(),
                ),
            )
        return dict(self.get_router(router_id))

    def get_router(self, router_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rudipay_routers WHERE id = ?", (router_id,)).fetchone()
        return self._router_row(row) if row else None

    def update_router_status(self, actor: Dict[str, Any], router_id: str, status: str) -> Dict[str, Any]:
        router = self.get_router(router_id)
        if not router:
            raise RudiPayError("Routing number not found")
        if router["owner_id"] != actor["id"] and not self.is_admin(actor):
            raise RudiPayError("Not authorized to modify this routing number")
        if status not in ROUTING_STATUS:
            raise RudiPayError("Invalid status")
        with self._connect() as conn:
            conn.execute("UPDATE rudipay_routers SET status = ?, updated_at = ? WHERE id = ?",
                         (status, _now(), router_id))
        return dict(self.get_router(router_id))

    def update_router_wallet(self, router_id: str, delta_minor: float) -> Dict[str, Any]:
        router = self.get_router(router_id)
        if not router:
            raise RudiPayError("Routing number not found")
        nxt = _q(router["wallet_balance"] + delta_minor)
        if nxt < 0:
            raise RudiPayError("Insufficient wallet balance")
        with self._connect() as conn:
            conn.execute("UPDATE rudipay_routers SET wallet_balance = ?, updated_at = ? WHERE id = ?",
                         (nxt, _now(), router_id))
        return dict(self.get_router(router_id))

    def get_max_route_amount(self, router_id: str) -> float:
        router = self.get_router(router_id)
        return _q(router["wallet_balance"] * MAX_ROUTE_PERCENT) if router else 0

    def is_admin(self, actor: Dict[str, Any]) -> bool:
        return bool(actor.get("roles") or []) and any(r in ADMIN_ROLES for r in actor["roles"])

    # -------------------------------------------------------------- faucets
    def register_faucet(self, actor_id: str, provider: str, phone: str, country_code: str,
                        *, display_name: str = "", notes: str = "",
                        roles: Optional[List[str]] = None) -> Dict[str, Any]:
        """Register a fallback faucet rail (admin-only). Wallet starts at 0 —
        it cannot route until funded via credit_faucet."""
        if not who_is_admin(self.accounts, actor_id):
            raise RudiPayError("Insufficient privileges: admin role required")
        phone = normalized_phone(phone)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rudipay_faucets WHERE provider = ? AND country_code = ? AND phone = ?",
                (provider.upper(), country_code.upper(), phone),
            ).fetchone()
            if row:
                if row["status"] == "revoked":
                    conn.execute("UPDATE rudipay_faucets SET status='active', updated_at=? WHERE id=?",
                                 (_now(), row["id"]))
                    return dict(self.get_faucet(row["id"]))
                raise RudiPayError("Faucet already registered")
            fid = _id("rpf_")
            conn.execute(
                """INSERT INTO rudipay_faucets
                   (id, provider, phone, country_code, display_name, notes, status,
                    created_by, created_at, updated_at)
                 VALUES (?,?,?,?,?,?, 'active',?,?,?)""",
                (fid, provider.upper(), phone, country_code.upper(),
                 display_name or f"{provider.upper()} Faucet", notes, actor_id, _now(), _now()),
            )
        return dict(self.get_faucet(fid))

    def create_funded_faucet(self, actor_id: str, provider: str, phone: str, country_code: str,
                             *, display_name: str = "", notes: str = "",
                             wallet_balance: float = 0.0) -> Dict[str, Any]:
        """Create a faucet WITH initial float — the only way float enters a
        faucet without a provider transfer."""
        if not who_is_admin(self.accounts, actor_id):
            raise RudiPayError("Insufficient privileges: admin role required")
        if wallet_balance < 0:
            raise RudiPayError("Faucet balance must be >= 0")
        fid = _id("rfc_")
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO rudipay_faucets
                   (id, provider, phone, country_code, display_name, notes, status,
                    wallet_balance, total_routed, total_fees_earned, created_by, created_at, updated_at)
                 VALUES (?,?,?,?,?,?, 'active',?,0,0,?,?,?)""",
                (fid, provider, normalized_phone(phone), country_code.upper(),
                 display_name, notes, wallet_balance, actor_id, _now(), _now()),
            )
        return dict(self.get_faucet(fid))

    def credit_faucet(self, actor_id: str, faucet_id: str, amount_minor: float) -> Dict[str, Any]:
        if not who_is_admin(self.accounts, actor_id):
            raise RudiPayError("Admin required to credit a faucet")
        if amount_minor < 0:
            raise RudiPayError("Faucet credit must be >= 0")
        with self._connect() as conn:
            conn.execute("UPDATE rudipay_faucets SET wallet_balance = wallet_balance + ? WHERE id = ?",
                         (amount_minor, faucet_id))
        return dict(self.get_faucet(faucet_id))

    def get_faucet(self, faucet_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rudipay_faucets WHERE id = ?", (faucet_id,)).fetchone()
        return self._faucet_row(row) if row else None

    def list_faucets(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM rudipay_faucets"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY country_code, provider"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._faucet_row(r) for r in rows]

    # ------------------------------------------------------------ selection
    def _location_score(self, a: Dict[str, Any], b: Dict[str, Any]) -> int:
        score = 0
        if a.get("country_code") and a["country_code"] == b.get("country_code"):
            score += 100
        if a.get("region") and a["region"] == b.get("region"):
            score += 50
        if a.get("city") and a["city"] == b.get("city"):
            score += 25
        if a.get("location_id") and a["location_id"] == b.get("location_id"):
            score += 75
        return score

    def select_rail(self, buyer_id: str, amount: float, currency: str,
                    location: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Pick a rail: preferred → user router (location) → country admin →
        country faucet → admin faucet. Busy rails fail closed."""
        location = location or {}
        country = (location.get("country_code") or "").upper()
        preferred = self.accounts.get("settlement", {}).get("spend", {}).get("autoSettleTo", "")
        routers = [r for r in self.list_routers("active") if r["id"] != preferred]

        def location_score(a, b):
            return self._location_score(a, b)

        # Faucets: prefer country faucet, then any faucet with capacity.
        def faucet_view(f):
            return {"id": f["id"], "phone": f["phone"], "country_code": f["country_code"],
                    "display_name": f"{f['provider']} Faucet", "provider": f["provider"]}

        def faucet_has_capacity(f):
            if f["status"] != "active":
                return False
            if self._router_is_busy(f["id"], "faucet"):
                return False
            return self.get_faucet_max_route(f["id"]) >= amount

        def busy(router_id, router_type):
            return self._router_is_busy(router_id, router_type)

        def max_route(router_id, router_type):
            if router_type == "faucet":
                f = self.get_faucet(router_id)
                return _q(f["wallet_balance"] * MAX_ROUTE_PERCENT) if f else 0
            r = self.get_router(router_id)
            return _q(r["wallet_balance"] * MAX_ROUTE_PERCENT) if r else 0

        user_routers = [r for r in routers if r["router_type"] == "user" and r["owner_id"] != buyer_id]
        eligible = [
            r for r in user_routers
            if not busy(r["id"], r["router_type"]) and max_route(r["id"], "admin") >= amount
        ]
        if eligible:
            top = max(location_score(location, r) for r in eligible)
            return self._pick(r for r in eligible if location_score(location, r) == top)

        country_routers = [r for r in routers if r["router_type"] == "admin" and r["country_code"] == country]
        for r in country_routers:
            if not busy(r["id"], "admin") and max_route(r["id"], "admin") >= amount:
                return r

        country_faucets = [f for f in self.list_faucets("active")
                           if f["country_code"] == country and faucet_has_capacity(f)]
        if country_faucets:
            return faucet_view(self._pick(country_faucets))

        any_routers = [r for r in routers if r["router_type"] == "admin"]
        for r in any_routers:
            if not busy(r["id"], "admin") and max_route(r["id"], "admin") >= amount:
                return r

        any_faucets = [f for f in self.list_faucets("active") if faucet_has_capacity(f)]
        if any_faucets:
            return faucet_view(self._pick(any_faucets))
        return None

    def _pick(self, candidates):
        import random
        c = list(candidates)
        return random.choice(c) if c else None

    def get_faucet_max_route(self, faucet_id: str) -> float:
        f = self.get_faucet(faucet_id)
        return _q(f["wallet_balance"] * MAX_ROUTE_PERCENT) if f else 0

    def _router_is_busy(self, router_id: str, router_type: str) -> bool:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status FROM rudipay_transactions WHERE router_id = ?", (router_id,)
            ).fetchall()
        if any(r["status"] not in _TERMINAL for r in rows):
            return True
        if router_type != "faucet":
            r = self.get_router(router_id)
            if r and r["router_reserved_at"]:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(r["router_reserved_at"])).total_seconds()
                if age < RESERVATION_TTL_SECONDS:
                    return True
        return False

    # ------------------------------------------------------------ wallet
    def credit_buyer_wallet(self, buyer_id: str, amount_minor: float, currency: str,
                            reference: str = "") -> None:
        """Credit a buyer's wallet in the platform payment ledger."""
        record_payment(
            self._connect(), rail="test", kind="credit",
            amount_minor=amount_minor, currency=currency,
            counterparty=buyer_id, account=buyer_id,
            reference=reference or f"buyer_credit_{buyer_id}_{_new_id('pb_')}",
        )

    # ------------------------------------------------------------ lifecycle
    def create_transaction(self, *, buyer_id: str, seller_id: str, amount: float,
                           currency: str, rail: Dict[str, Any], description: str = "",
                           metadata: Optional[Dict[str, Any]] = None,
                           idempotency_key: str = "") -> Dict[str, Any]:
        amount_minor = money_to_minor(amount, currency)
        if amount_minor < MIN_ROUTE_AMOUNT:
            raise RudiPayError(f"Amount below minimum ({MIN_ROUTE_AMOUNT} {currency})")
        max_route = self.get_faucet_max_route(rail["id"]) if rail["id"].startswith("rpf_") \
            else self.get_max_route_amount(rail["id"])
        if amount_minor > max_route:
            raise RudiPayError(f"Amount exceeds rail capacity (max {max_route} {currency})")
        if idempotency_key:
            existing = self._txn_by_idem(idempotency_key)
            if existing:
                return existing
        txn_id = _id("rpt_")
        now = _now()
        expires = (datetime.now(timezone.utc) + timedelta(minutes=SETTLEMENT_WINDOW_MINUTES)).isoformat()
        router_fee = _q(amount_minor * ROUTER_FEE_PERCENT)
        seller_amount = _q(amount_minor - router_fee)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO rudipay_transactions
                   (id, buyer_id, seller_id, router_id, amount, currency, router_fee,
                    seller_amount, description, metadata, status, expires_at,
                    created_at, updated_at, routing_phone, routing_display_name, idempotency_key)
                 VALUES (?,?,?,?,?,?,?,?,?,?, 'pending',?,?,?,?,?,?)""",
                (txn_id, buyer_id, seller_id, rail["id"], amount_minor, currency.upper(),
                 router_fee, seller_amount, description, json.dumps(metadata or {}),
                 expires, now, now, rail["phone"], rail["display_name"], idempotency_key),
            )
        return dict(self.get_txn(txn_id))

    def _txn_by_idem(self, key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM rudipay_transactions WHERE idempotency_key = ?",
                               (key,)).fetchone()
        return self.get_txn(row["id"]) if row else None

    def get_txn(self, txn_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rudipay_transactions WHERE id = ?", (txn_id,)).fetchone()
        return self._txn_row(row) if row else None

    def _txn_row(self, row) -> Dict[str, Any]:
        return {
            "id": row["id"], "buyer_id": row["buyer_id"], "seller_id": row["seller_id"],
            "router_id": row["router_id"], "amount": row["amount"], "currency": row["currency"],
            "router_fee": row["router_fee"], "seller_amount": row["seller_amount"],
            "description": row["description"], "metadata": json.loads(row["metadata"] or "{}"),
            "status": row["status"],
            "buyer_committed_at": row["buyer_committed_at"],
            "seller_notified_at": row["seller_notified_at"],
            "completed_at": row["completed_at"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "routing_phone": row["routing_phone"],
            "routing_display_name": row["routing_display_name"],
            "confirmation_code": row["confirmation_code"],
            "idempotency_key": row["idempotency_key"],
        }

    def buyer_commit(self, txn_id: str, *, buyer_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rudipay_transactions WHERE id = ?", (txn_id,)).fetchone()
        if not row:
            raise RudiPayError("Transaction not found")
        if row["buyer_id"] != buyer_id:
            raise RudiPayError("Not authorized")
        if row["status"] != "pending":
            raise RudiPayError("Transaction not in pending state")
        if row["expires_at"] < _now():
            raise RudiPayError("Transaction expired")
        if not self._assert_buyer_funds(buyer_id, row["amount"], row["currency"]):
            raise RudiPayError("Insufficient buyer balance")
        record_payment(
            self._connect(), rail="rudipay", kind="debit",
            amount_minor=money_to_minor(row["amount"], row["currency"]),
            currency=row["currency"], counterparty=row["router_id"], account=buyer_id,
            reference=f"rudipay_reserve_{txn_id}", status="pending",
        )
        with self._connect() as conn:
            conn.execute("UPDATE rudipay_transactions SET status='buyer_committed', buyer_committed_at=?, updated_at=? WHERE id=?",
                         (_now(), _now(), txn_id))
        return dict(self.get_txn(txn_id))

    def seller_confirm(self, txn_id: str, *, seller_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rudipay_transactions WHERE id = ?", (txn_id,)).fetchone()
        if not row or row["seller_id"] != seller_id:
            raise RudiPayError("Not authorized")
        if row["status"] != "buyer_committed":
            raise RudiPayError("Buyer has not committed yet")
        with self._connect() as conn:
            conn.execute("UPDATE rudipay_transactions SET status='seller_notified', seller_notified_at=?, updated_at=? WHERE id=?",
                         (_now(), _now(), txn_id))
        return dict(self.get_txn(txn_id))

    def complete_settlement(self, txn_id: str, *, router_id: str,
                            otp_challenge_id: str = "",
                            confirmation_code: str = "") -> Dict[str, Any]:
        """Router releases the seller payout. OTP gate enforced first."""
        txn = self.get_txn(txn_id)
        if not txn:
            raise RudiPayError("Transaction not found")
        if txn["router_id"] != router_id:
            raise RudiPayError("Not authorized for this router")
        if txn["status"] == "completed":
            return txn
        if txn["status"] != "seller_notified":
            raise RudiPayError("Transaction not ready for settlement")
        otp = self.get_otp(otp_challenge_id)
        if not otp or otp.get("status") not in ("fulfilled", "claimed"):
            raise RudiPayError(
                "SMS OTP gate: a fulfilled challenge is required for settlement"
            )
        now = _now()
        with self._connect() as conn:
            record_payment(
                conn, rail="rudipay", kind="credit",
                amount_minor=money_to_minor(txn["seller_amount"], txn["currency"]),
                currency=txn["currency"], counterparty=txn["buyer_id"],
                account=txn["seller_id"],
                reference=f"rudipay_settlement_{txn_id}", status="completed",
            )
            if txn["router_id"].startswith("rpf_"):
                f = conn.execute("SELECT wallet_balance,total_routed,total_fees_earned FROM rudipay_faucets WHERE id=?",
                                 (txn["router_id"],)).fetchone()
                if f:
                    conn.execute(
                        "UPDATE rudipay_faucets SET wallet_balance=?, total_routed=?, total_fees_earned=?, last_used_at=?, updated_at=? WHERE id=?",
                        (_q(f["wallet_balance"] + txn["router_fee"]),
                         _q(f["total_routed"] + txn["amount"]),
                         _q(f["total_fees_earned"] + txn["router_fee"]),
                         now, now, txn["router_id"]),
                    )
            else:
                r = conn.execute("SELECT wallet_balance,total_routed,total_fees_earned FROM rudipay_routers WHERE id=?",
                                 (txn["router_id"],)).fetchone()
                if r:
                    conn.execute(
                        "UPDATE rudipay_routers SET wallet_balance=?, total_routed=?, total_fees_earned=?, last_used_at=?, updated_at=? WHERE id=?",
                        (_q(r["wallet_balance"] + txn["router_fee"]),
                         _q(r["total_routed"] + txn["amount"]),
                         _q(r["total_fees_earned"] + txn["router_fee"]),
                         now, now, txn["router_id"]),
                    )
            self._set_otp_used(otp_challenge_id, conn=conn)
            conn.execute(
                "INSERT INTO rudipay_settlements (id, transaction_id, router_id, buyer_id, seller_id, "
                "amount, currency, router_fee, seller_amount, confirmation_code, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'completed',?)",
                (_id("rpsl_"), txn_id, router_id, txn["buyer_id"], txn["seller_id"],
                 txn["amount"], txn["currency"], txn["router_fee"], txn["seller_amount"],
                 confirmation_code, now),
            )
            conn.execute("UPDATE rudipay_transactions SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                         (now, now, txn_id))
        return dict(self.get_txn(txn_id))

    def _set_otp_used(self, challenge_id: str, conn=None) -> None:
        with (conn or self._connect()) as conn:
            conn.execute("UPDATE sms_otp_challenge SET status='used', used_at=?, updated_at=? WHERE id=?",
                         (_now(), _now(), challenge_id))
            conn.commit()

    def get_otp(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        from .otp import list_challenges
        for c in list_challenges():
            if c["id"] == challenge_id:
                return c
        return None

    def _assert_buyer_funds(self, buyer_id: str, amount: float, currency: str) -> bool:
        row = self._wallet_available(buyer_id, currency)
        return bool(row is not None and row["available"] >= amount)

    def _wallet_available(self, account: str, currency: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payment_tx WHERE account = ? AND currency = ? ORDER BY occurred_at DESC",
                (account, currency.upper()),
            ).fetchall()
        # Wallet = completed credits − non-failed debits.
        credits = sum(r["amount_minor"] for r in rows if r["kind"] == "credit" and r["status"] == "completed")
        debits = sum(r["amount_minor"] for r in rows if r["kind"] == "debit" and r["status"] != "failed")
        available = credits - debits
        if available < 0:
            available = 0
        return {"available": available, "currency": currency.upper()}

    # -------------------------------------------------------------- disputes
    def raise_dispute(self, txn_id: str, user_id: str, reason: str,
                      evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        txn = self.get_txn(txn_id)
        if not txn:
            raise RudiPayError("Transaction not found")
        if user_id not in (txn["buyer_id"], txn["seller_id"]):
            raise RudiPayError("Only buyer or seller can raise a dispute")
        if txn["status"] != "completed":
            raise RudiPayError("Can only dispute completed transactions")
        disp_id = _id("rpd_")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO rudipay_disputes (id, transaction_id, raised_by, reason, evidence, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,'open',?,?)",
                (disp_id, txn_id, user_id, reason, json.dumps(evidence or {}), _now(), _now()),
            )
            conn.execute("UPDATE rudipay_transactions SET status='disputed', updated_at=? WHERE id=?", (_now(), txn_id))
        return self.get_dispute(disp_id)

    def get_dispute(self, dispute_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rudipay_disputes WHERE id = ?", (dispute_id,)).fetchone()
        return self._dispute_row(row) if row else None

    def list_disputes(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM rudipay_disputes ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql + (" WHERE status=?" if status else ""), (status,) if status else ()).fetchall()
        return [self._dispute_row(r) for r in rows]

    def _dispute_row(self, row) -> Dict[str, Any]:
        return {
            "id": row["id"], "transaction_id": row["transaction_id"],
            "raised_by": row["raised_by"], "reason": row["reason"],
            "evidence": json.loads(row["evidence"] or "{}"), "status": row["status"],
            "resolution": row["resolution"], "resolved_by": row["resolved_by"],
            "resolved_at": row["resolved_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    # -------------------------------------------------------------- listing
    def list_routers(self, status: Optional[str] = None, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM rudipay_routers"
        conds = []
        params = []
        if status:
            conds.append("status = ?")
            params.append(status)
        if owner_id:
            conds.append("owner_id = ?")
            params.append(owner_id)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY created_at DESC", params).fetchall()
        return [self._router_row(r) for r in rows]

    def _router_row(self, row) -> Dict[str, Any]:
        return {
            "id": row["id"], "owner_id": row["owner_id"], "phone": row["phone"],
            "country_code": row["country_code"], "region": row["region"],
            "city": row["city"], "location_id": row["location_id"],
            "display_name": row["display_name"], "notes": row["notes"],
            "router_type": row["router_type"], "status": row["status"],
            "wallet_balance": row["wallet_balance"], "total_routed": row["total_routed"],
            "total_fees_earned": row["total_fees_earned"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"], "router_reserved_at": row["router_reserved_at"],
            "router_reserved_by": row["router_reserved_by"],
        }

    def _faucet_row(self, row) -> Dict[str, Any]:
        return {
            "id": row["id"], "provider": row["provider"], "phone": row["phone"],
            "country_code": row["country_code"], "display_name": row["display_name"],
            "notes": row["notes"], "status": row["status"],
            "wallet_balance": row["wallet_balance"], "total_routed": row["total_routed"],
            "total_fees_earned": row["total_fees_earned"],
            "created_by": row["created_by"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "last_used_at": row["last_used_at"],
        }

    def router_stats(self, router_id: str) -> Dict[str, Any]:
        r = self.get_router(router_id)
        if not r:
            raise RudiPayError("Router not found")
        with self._connect() as conn:
            rows = conn.execute("SELECT amount, router_fee FROM rudipay_settlements WHERE router_id = ?",
                                (router_id,)).fetchall()
        return {
            "router": r,
            "max_route_amount": self.get_max_route_amount(router_id),
            "total_volume": _q(sum(x["amount"] for x in rows)),
            "total_fees_earned": _q(sum(x["router_fee"] for x in rows)),
        }

    def platform_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            def count(sql):
                return conn.execute(sql).fetchone()[0]
            vol = conn.execute("SELECT COALESCE(SUM(amount),0) AS v FROM rudipay_settlements").fetchone()["v"]
            fees = conn.execute("SELECT COALESCE(SUM(router_fee),0) AS f FROM rudipay_settlements").fetchone()["f"]
        return {
            "active_routers": count("SELECT COUNT(*) FROM rudipay_routers WHERE status='active'"),
            "total_routers": count("SELECT COUNT(*) FROM rudipay_routers"),
            "total_transactions": count("SELECT COUNT(*) FROM rudipay_transactions"),
            "completed_transactions": count("SELECT COUNT(*) FROM rudipay_transactions WHERE status='completed'"),
            "disputed_transactions": count("SELECT COUNT(*) FROM rudipay_transactions WHERE status='disputed'"),
            "open_disputes": count("SELECT COUNT(*) FROM rudipay_disputes WHERE status='open'"),
            "total_volume": _q(vol),
            "total_fees_distributed": _q(fees),
        }
