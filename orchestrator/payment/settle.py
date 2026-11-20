r"""Settlement state machine — port of the musashaban spend/settlement wiring.

Every deposit to (or movement out of) the platform is recorded through
:func:`orchestrator.store.record_funding` (the durable funding ledger), and
:func:`maybe_settle` closes surplus revenue onto the operator's auto-settle
address when the platform funding state is not ``unfunded`` — never without
both the config and a recorded spend authorization (fail-closed).
"""

from __future__ import annotations

from typing import Any, Dict

from .accounts import load_accounts, otp_secrets
from .withdrawal import create_withdrawal, get_withdrawal, submit_withdrawal


def funding_state_auto_threshold(state: Dict[str, Any]) -> Dict[str, Any]:
    """Which states may auto-settle, and the expected invariants."""
    return {
        "unfunded": False,
        "float_funded": True,
        "revenue_funded": True,
    }.get((state or {}).get("state"), False)


def maybe_settle(*, automation=None, db=None, min_surplus_usd: float = 0.01) -> Dict[str, Any]:
    """Settle surplus revenue to ``autoSettleTo`` if state + config + auth allow it.

    Fail-closed: anything missing leaves a visible gap in the report.
    """
    from orchestrator import admin
    from .db import PAYMENT_DB

    accounts = load_accounts()
    settle_to = (accounts.get("settlement", {}).get("spend", {}) or {}).get("autoSettleTo", "")
    state = admin.funding_state()
    report: Dict[str, Any] = {"configured": bool(settle_to), "state": state.get("state"),
                              "settle_to": settle_to, "attempted": False}
    if not settle_to:
        report["reason"] = "autoSettleTo not configured"
        return report
    if not funding_state_auto_threshold(state):
        report["reason"] = f"funding state is {state.get('state') or 'unfunded'}; 0-funding posture holds"
        return report
    surplus = (state.get("revenue_usd") or 0) - (state.get("spend_usd") or 0) - (state.get("reserve_usd") or 0)
    if surplus <= 0 or surplus < min_surplus_usd:
        report["reason"] = "no surplus to settle"
        return report
    if not otp_secrets(accounts)["gatewaySecret"]:
        report["reason"] = "OTP gateway secret not provisioned"
        return report
    report["attempted"] = True
    report["amount_usd"] = round(surplus, 2)
    # Operator-visible, two-step: the platform never pays without approval.
    withdrawal = create_withdrawal(
        amount=surplus,
        to=settle_to,
        network="bank",
        currency="USD",
        note=f"auto-settle surplus from funding ledger ({state.get('state')})",
    )
    submitted = submit_withdrawal(withdrawal["id"])
    report["withdrawal_id"] = withdrawal["id"]
    report["withdrawal_status"] = submitted.get("status")
    report["reason"] = "surplus recorded for settlement"
    return report
