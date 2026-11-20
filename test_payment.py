"""Runtime checks for the ported payment & settlement system (musashaban).

Run from the platform root:

    .venv\\Scripts\\python.exe test_payment.py
"""

from pathlib import Path

from orchestrator import admin, payment, agent_specs
from orchestrator.payment import accounts, funding, otp, settle as settlement, withdrawal
from orchestrator.payment.db import PAYMENT_DB, connect_db
from orchestrator.payment.rudipay import RudiPay, RudiPayError
from orchestrator.config import PLATFORM_ROOT


def _db() -> Path:
    return Path("orchestrator/state/payment.db")


def _clean_state():
    db = connect_db(PAYMENT_DB)
    for tbl, where in (
        ("rudipay_transactions", "buyer_id IN ('buyer1','seller1') OR seller_id IN ('buyer1','seller1')"),
        ("rudipay_settlements", "buyer_id IN ('buyer1','seller1') OR seller_id IN ('buyer1','seller1')"),
        ("payment_tx", "account IN ('buyer1','seller1')"),
        ("sms_otp_challenge", "subject_id IN (SELECT id FROM rudipay_transactions WHERE buyer_id = 'buyer1')"),
    ):
        try:
            db.execute(f"DELETE FROM {tbl} WHERE {where}")
        except Exception:
            pass
    db.commit()


def test_rudi_lifecycle():
    _clean_state()
    r = RudiPay()
    # Register a router as admin.
    actor = {"id": accounts.load_accounts()["settlement"]["rudipay"]["admins"][0],
             "roles": ["admin", "super_admin"]}
    try:
        router = r.register_routing_number(
            actor_id=actor["id"], phone="+256700677543", country_code="UG",
            two_factor_enabled=True, display_name="test router",
        )
    except RudiPayError as e:
        # Re-runs: the rail is already registered.
        if "already registered" not in str(e):
            raise
        router = next(x for x in r.list_routers() if x["phone"] == "+256700677543")
    assert router["status"] == "active" and router["phone"] == "+256700677543"

    # Preferred-router list (accounts) is enforced fail-closed.
    accounts_obj = accounts.load_accounts()
    assert not accounts.who_is_admin(accounts_obj, "0xnotanadmin")

    # Set the router balance via update (fail-closed: negative denied).
    r_before = r.get_router(router["id"])
    _neg = False
    try:
        r.update_router_wallet(router["id"], -10**9)
    except RudiPayError:
        _neg = True
    assert _neg, f"expected negative update denied; router balance was {r_before and r_before['wallet_balance']}"

    # Fund the router, create the txn.
    r.update_router_wallet(router["id"], 10**6)
    txn = r.create_transaction(buyer_id="buyer1", seller_id="seller1", amount=10,
                               currency="UGX", rail=router)

    # Buyer commits: buyer funds must exist (nothing → deny).
    try:
        r.buyer_commit(txn_id=txn["id"], buyer_id="buyer1")
        raise SystemExit("expected RudiPayError: no buyer funds")
    except RudiPayError:
        pass

    # Fund the buyer and commit.
    r.credit_buyer_wallet("buyer1", 10, "UGX", "deposit-test")
    r.buyer_commit(txn["id"], buyer_id="buyer1")
    r.seller_confirm(txn["id"], seller_id="seller1")
    # OTP required by default in complete_settlement: no challenge → closed.
    try:
        r.complete_settlement(txn["id"], router_id=router["id"], confirmation_code="x")
        raise SystemExit("expected RudiPayError: OTP gate")
    except RudiPayError:
        pass
    # Fulfill an OTP challenge for this txn (gateway-ingest simulation), then settle.
    ch = otp.create_challenge("rudipay_settle", phone=router["phone"], subject_id=txn["id"],
                              accounts=_otp_accounts())
    db = connect_db(PAYMENT_DB)
    db.execute("UPDATE sms_otp_challenge SET status='fulfilled' WHERE id=?", (ch["id"],))
    db.commit(); db.close()
    r.complete_settlement(txn["id"], router_id=router["id"], otp_challenge_id=ch["id"])
    final = r.get_txn(txn["id"])
    assert final["status"] == "completed", final
    assert r.platform_stats()["total_volume"] > 0


def _otp_accounts():
    a = accounts.load_accounts()
    p = a.setdefault("provisioning", {})
    p["settlement.otp.gatewaySecret"] = "gw-test-secret"
    p["settlement.otp.key"] = "key-test-secret"
    return a


def test_otp_gate():
    ch = otp.create_challenge("x", phone="+256700000000", subject_id="otp-gate-test",
                              accounts=_otp_accounts())
    assert otp.get_challenge(ch["id"])["status"] == "pending"
    # Simulate the gateway ingesting the SMS that fulfills the challenge.
    db = connect_db(PAYMENT_DB)
    db.execute("UPDATE sms_otp_challenge SET status='fulfilled' WHERE id=?", (ch["id"],))
    db.commit(); db.close()
    otp.authorize(ch["id"])
    assert otp.get_challenge(ch["id"])["status"] == "claimed"
    # burn again → fail closed
    try:
        otp.authorize(ch["id"])
        raise SystemExit("expected OtpError")
    except otp.OtpError:
        pass


def test_funding_and_needs():
    # funding request / collections
    req = funding.create_funding_request(
        purpose="ipfs+cloudflare+tailscale surfacing", detail="run ipfs daemon, tailscale",
        amount=500000, currency="UGX", requested_by="sentry-dept",
    )
    assert funding.get_funding_request(req["id"])["status"] == "open"
    funding.add_collection(req["id"], 250000, currency="UGX", rail="bank",
                           reference="partial-1", recorded_by="operator")
    req2 = funding.get_funding_request(req["id"])
    assert req2["collected_minor"] > 0 and req2["status"] == "open"
    funding.add_collection(req["id"], 300000, currency="UGX", rail="bank",
                           reference="partial-2", recorded_by="operator")
    req3 = funding.get_funding_request(req["id"])
    assert req3["status"] == "funded", req3

    # development needs
    need = funding.create_development_need("deployment improvements",
                                           "Sentry transparency layers", priority="high")
    assert funding.get_development_need(need["id"])["status"] == "open"
    funding.update_development_need(need["id"], status="doing")
    assert funding.get_development_need(need["id"])["status"] == "doing"


def test_withdrawal_gate():
    wd = withdrawal.create_withdrawal(amount=100, to="0x61f9fac0a44e0d975673cb97fc4a615b16398846",
                                      network="bank", currency="UGX")
    assert wd["status"] == "pending_approval"
    assert wd["approval_phrase"]
    # wrong phrase → fail closed
    try:
        withdrawal.confirm_withdrawal(wd["id"], "nope")
        raise SystemExit("expected ValueError")
    except ValueError:
        pass
    # right phrase
    confirmed = withdrawal.confirm_withdrawal(wd["id"], wd["approval_phrase"])
    assert confirmed["status"] == "approved"
    # submit to a provider path
    sub = withdrawal.submit_withdrawal(confirmed["id"], provider_ref="send-123", provider_status="pending")
    assert sub["status"] == "submitted"


def test_accounts_harmonized():
    accounts_obj = accounts.load_accounts()
    assert accounts_obj["creatorAddress"] == "0x61f9FAC0A44e0D975673CB97fc4A615B16398846"
    assert accounts_obj["walletAddress"] == "0x61f9FAC0A44e0D975673CB97fc4A615B16398846"
    assert accounts_obj["wallets"]["tron"] is None  # verified-but-unused others removed
    assert accounts_obj["settlement"]["spend"]["autoSettleTo"] == "0x61f9FAC0A44e0D975673CB97fc4A615B16398846"
    rpp = accounts_obj["settlement"]["rudipay"]["preferredRouterPhones"]
    assert "+256780135102" in rpp and "+256780698353" in rpp
    assert "+256700677543" in rpp and "+256731235987" in rpp and "+256707319020" in rpp
    assert accounts_obj["settlement"]["socials"]["whatsapp"] == "+256700677543"
    assert accounts_obj["settlement"]["bank"]["name"] == "EXIM Uganda"
    res = accounts.validate_accounts(accounts_obj)
    assert res["errors"] == []


def test_settlement_status():
    st = settlement.maybe_settle()
    assert st["configured"] is True  # autoSettleTo exists
    assert "state" in st


def test_sentry_status():
    from orchestrator import sentry
    status = sentry.get_sentry_status()
    assert status["department"] == "Sentry"
    assert status["dept_key"] == "sentry"
    assert status["department_agent"] == "sentry"
    assert status["deployment"]["surfaces"]["surfaces"]["ipfs"]["status"] in ("unavailable", "binary_present_no_daemon")
    assert status["deployment"]["surfaces"]["platform_local"]["host"]
    assert "gapinvex_production" in status["deployment"]["hosting"]
    assert sorted(status["security"]["protected_paths"]) == sorted(admin.policy.PROTECTED_PATHS)


def test_sentry_spec_valid():
    result = agent_specs.validate_all()
    assert result["valid"], result["missing_specs"]
    from orchestrator import registry
    assert "sentry" in {sp["name"] for sp in registry.subplatforms()}


def test_rudipay_faucet_flow():
    r = RudiPay()
    admin_id = accounts.load_accounts()["settlement"]["rudipay"]["admins"][0]
    # Non-admin registration is refused.
    try:
        r.create_funded_faucet("not-admin", "MTN", "+256700000000", "UG", wallet_balance=100)
        raise SystemExit("expected RudiPayError: no admin")
    except RudiPayError:
        pass
    f = r.create_funded_faucet(admin_id, "MTN", "+256700000000", "UG", wallet_balance=100)
    assert f["wallet_balance"] == 100
    assert r.get_faucet_max_route(f["id"]) == 10  # 10% cap of 100, USD scale 0? UGX=0 → 100*0.1=10
    # faucet with 0 cannot route (fail-closed)
    f2 = r.create_funded_faucet(admin_id, "Airtel", "+256700000001", "UG", wallet_balance=0)
    assert r.get_faucet_max_route(f2["id"]) == 0
    # negative faucet balance is refused
    try:
        r.create_funded_faucet(admin_id, "Airtel", "+256700000002", "UG", wallet_balance=-5)
        raise SystemExit("expected RudiPayError: negative balance")
    except RudiPayError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
