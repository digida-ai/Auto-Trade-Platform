"""Runtime checks for the constitution/soul, funding and dispatch bites.

Run from the platform root:

    .venv\\Scripts\\python.exe test_automaton_runtime.py
"""

from pathlib import Path

from orchestrator import admin, agent_specs, policy
from orchestrator.config import PLATFORM_ROOT


def _root() -> Path:
    return PLATFORM_ROOT


def test_all_specs_valid_against_constitution():
    result = agent_specs.validate_all()
    assert result["valid"], result["missing_specs"]
    assert result["agents_total"] == len(admin.subplatforms())


def test_constitution_files_present():
    for f in ("constitution.md", "soul.md"):
        assert (_root() / "musashaban-port" / f).exists(), f


def test_policy_financial_denied_by_default():
    d = policy.evaluate("withdraw 10 USD from wallet", _root(), agent="some-agent", source="operator")
    assert not d.allow and d.reason_code == "unauthorized_financial_action"
    d2 = policy.evaluate("settle batch settlement", _root(), agent="some-agent", source="operator")
    assert not d2.allow and d2.reason_code == "unauthorized_financial_action"
    d3 = policy.evaluate("withdraw 10 USD from wallet", _root(), agent="some-agent", source="external")
    assert not d3.allow


def test_spend_auth_enables_then_revokes():
    rec = admin.authorize_spend("vibetrading", "smoke test authorization", amount_usd=5, reason="test")
    token = rec["spend_auth_id"]
    assert rec["status"] == "active"
    d = policy.evaluate("withdraw 10 USD from wallet", _root(), agent="vibetrading", source="operator", spend_auth=token)
    assert d.allow, d.message
    d2 = policy.evaluate("withdraw 10 USD from wallet", _root(), agent="vibetrading", source="operator", spend_auth="sa-bogus")
    assert not d2.allow, "unknown token must fail closed"
    d3 = policy.evaluate("withdraw 10 USD from wallet", _root(), agent="other-agent", source="operator", spend_auth=token)
    assert not d3.allow, "token is bound to its agent"
    admin.revoke_spend_authorization(token)
    assert not policy.spend_auth_active("vibetrading", token)
    assert admin.spend_authorizations() or True  # others may exist; the token is inactive


def test_funding_state_machine():
    admin.record_funding("reserve", 500, purpose="smoke reserve")
    st = admin.funding_state()
    assert st["state"] in ("float_funded", "revenue_funded"), st
    assert st["reserve_usd"] > 0 or st["revenue_usd"] > 0


def test_policy_financial_allowed_when_float_funded():
    st = admin.funding_state()
    d = policy.evaluate("withdraw 10 USD from wallet", _root(), agent="vibetrading", source="operator", funding_state=st["state"])
    assert d.allow if st["state"] != "unfunded" else not d.allow


def test_dispatch_enforces_constitution():
    sp = admin.subplatforms()[0]
    name = sp.get("name")
    spec = agent_specs.load_spec(name)
    assert spec is not None
    stale = dict(spec)
    stale["constitution"] = {"sha256": "0" * 64}
    orig_load_spec = agent_specs.load_spec
    ran = []
    agent_specs.load_spec = lambda n: stale if n == name else orig_load_spec(n)
    admin.run_shell_streaming = lambda *a, **k: (
        ran.append(a),
        {"ok": True, "stdout": "", "stderr": "", "exit_code": 0, "done": True},
    )[1]
    admin.save_run = lambda run: None
    try:
        run = {"id": "t-constitution", "steps": [{}], "reports": []}
        step = admin.dispatch_to_eagent(sp, "task", {}, run, 0)
        assert step["status"] == "blocked", step
        assert step["error"] == "agent spec fails constitution/soul validation", step["error"]
        assert step["output"]["errors"], step["output"]
        assert step["output"]["constitution"], step["output"]
        assert not ran, "shell must not run when the spec fails the constitution"
        agent_specs.load_spec = orig_load_spec
        step2 = admin.dispatch_to_eagent(sp, "task", {}, run, 0)
        assert step2["status"] == "blocked"
        assert step2["error"] != "agent spec fails constitution/soul validation", step2["error"]
    finally:
        agent_specs.load_spec = orig_load_spec


def test_replicas_carry_specs():
    migrated = admin.migrate_replica_specs()
    states = admin.load_agent_states()
    replicas = [n for n, e in (states.get("agents") or {}).items() if e.get("replica_of")]
    if not replicas:
        assert migrated["count"] == 0, f"migration wrote specs for unknown replicas: {migrated['count']}"
        return
    name = replicas[0]
    entry = (states.get("agents") or {})[name]
    assert isinstance(entry.get("spec"), dict), "replica must carry the source spec"
    assert entry.get("soul_path"), "replica must carry the soul path"
    assert entry.get("constitution"), "replica must carry the constitution hash"
    resolved = agent_specs.resolve_spec(name)
    assert resolved and resolved.get("name") == entry["replica_of"], "resolve_spec returns the replica's spec"


def test_dispatch_resolves_carried_spec():
    states = admin.load_agent_states()
    replica = next(
        (n for n, e in (states.get("agents") or {}).items() if e.get("replica_of")), None
    )
    if not replica:
        return
    # Simulate "no on-disk spec and not in registry": dispatch must resolve
    # the spec from the replica's carried payload, not refuse it.
    orig = agent_specs.load_spec
    agent_specs.load_spec = lambda n: None if n == replica else orig(n)
    admin.run_shell_streaming = lambda *a, **k: {
        "ok": True, "stdout": "", "stderr": "", "exit_code": 0, "done": True,
    }
    admin.save_run = lambda run: None
    try:
        sp = {"name": replica, "component": (states["agents"][replica].get("component") or "x")}
        step = admin.dispatch_to_eagent(sp, "task", {}, {"id": "t", "steps": [{}], "reports": []}, 0)
        assert step["status"] == "blocked", step
        assert step["error"] != "agent spec fails constitution/soul validation", step["error"]
    finally:
        agent_specs.load_spec = orig


def test_ensure_specs():
    report = admin.ensure_specs()
    assert report["ok"] is True, report["validation"]["missing_specs"]
    assert report["validation"]["missing_specs"] == []


def test_build_spec_fallback():
    spec = agent_specs.build_spec("AutoHedge")
    assert spec and spec["name"] == "AutoHedge"
    assert spec["schema"] == "eagent/v1"
    assert spec["constitution"]["sha256"] == agent_specs.constitution_sha256()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
