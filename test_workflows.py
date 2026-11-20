"""Offline checks for the agentic workflow catalogue (no server, no state writes).

Run from the platform root:

    .venv\\Scripts\\python.exe test_workflows.py
"""

import json
import tempfile
from pathlib import Path

from orchestrator import admin
from orchestrator import stack
from orchestrator import workflows as wf


def test_shape():
    assert len(wf.WORKFLOWS) == 20, "catalogue must hold 20 workflows"
    ids = [w.id for w in wf.WORKFLOWS]
    slugs = [w.slug for w in wf.WORKFLOWS]
    assert ids == list(range(1, 21)), ids
    assert len(set(slugs)) == 20, slugs
    for w in wf.WORKFLOWS:
        assert w.stages, w.slug
        assert w.replication_source, w.slug
        assert w.department_kind and w.admin_kind, w.slug
        data = w.to_dict()
        assert data["compartment_agnostic"] is True, w.slug
        assert "compartments" not in data, f"{w.slug} must not hardcode compartments"


def test_component_names_are_real():
    from orchestrator.registry import subplatforms

    registered = {sp.get("name") for sp in subplatforms()}
    for w in wf.WORKFLOWS:
        for name in w.referenced_components():
            assert name in registered, f"{w.slug} references unregistered component {name}"
        assert w.replication_source in registered


def test_selection():
    cases = {
        "summarize 400 support tickets": 10,
        "build an app that debugs itself": 19,
        "verify this math proof": 15,
        "debate both sides of the merger": 13,
        "remember the client history": 17,
        "run autonomous desktop actions": 6,
    }
    for goal, expected in cases.items():
        picked = wf.select_workflow(goal)
        assert picked["workflow"].id == expected, (goal, picked["workflow"].slug)
    fallback = wf.select_workflow("zzz qqq")
    assert fallback["fallback"] is True
    assert fallback["workflow"].id == 9


def test_gates_fail_closed():
    stage = wf.Stage("s", "Stage", ("freellmapi", "agentic-inbox"))
    assert wf.gate_decision(stage, {"freellmapi": "ready", "agentic-inbox": "ready"})["allowed"] is True
    closed = wf.gate_decision(stage, {"freellmapi": "blocked", "agentic-inbox": "ready"})
    assert closed["allowed"] is False
    assert closed["blocked"] == ["freellmapi"]
    setup = wf.gate_decision(stage, {"freellmapi": "needs_setup", "agentic-inbox": "ready"})
    assert setup["allowed"] is True and setup["needs_setup"] == ["freellmapi"]


def test_decision_by_necessity():
    rag = wf.get_workflow("rag")

    # No evidence: nothing is spawned, and the reason says why.
    empty = wf.decide_compartments(rag, "zzz qqq")
    assert empty["selected"] == [], empty
    assert "no compartment met the necessity threshold" in empty["reason"]

    # Goal evidence selects only the implicated compartments.
    policy = wf.decide_compartments(rag, "audit the trading policy for legal risk")
    assert "politics_governance" in policy["selected"], policy
    assert "religious_mystical" not in policy["selected"], policy

    # Explicit hints are a caller/agent decision and are honored.
    hinted = wf.decide_compartments(rag, "zzz", hints=["science_research", "religious_mystical"])
    assert hinted["selected"] == ["science_research", "religious_mystical"], hinted
    assert hinted["unknown_hints"] == []

    bad = wf.decide_compartments(rag, "zzz", hints=["atlantis"])
    assert bad["selected"] == []
    assert bad["unknown_hints"] == ["atlantis"]


def test_roles_derive_for_any_pair():
    for workflow in wf.WORKFLOWS:
        for comp in wf.COMPARTMENTS:
            roles = wf.derive_roles(workflow, comp)
            assert comp.short in roles["department"]
            assert comp.short in roles["admin"]
            assert roles["department"].endswith("Department Agent")
            assert roles["admin"].endswith("Admin Agent")
    cot = wf.get_workflow("cot-cove")
    science = wf.COMPARTMENT_BY_KEY["science_research"]
    roles = wf.derive_roles(cot, science)
    assert roles["department"] == "Science Research Verification Department Agent"
    assert roles["admin"] == "Science Research Proof Review Admin Agent"


def test_custom_decider_is_normalized():
    rag = wf.get_workflow("rag")

    def decider(workflow, goal, **kwargs):
        return {
            "decider": "test-agent",
            "candidates": [
                {"compartment_key": "science_research", "score": 9, "selected": True, "rationale": ["agent call"]},
                {"compartment_key": "atlantis", "score": 9, "selected": True},
            ],
        }

    decision = wf.decide_compartments(rag, "anything", decider=decider)
    assert decision["decider"] == "test-agent"
    assert decision["selected"] == ["science_research"]
    assert [c["compartment_key"] for c in decision["candidates"]] == ["science_research"]
    assert decision["candidates"][0]["department"].startswith("Science Research")


def test_build_run_is_compartment_agnostic():
    rag = wf.get_workflow("rag")
    ready = {name: "ready" for name in rag.referenced_components()}
    decision = wf.decide_compartments(rag, "zzz qqq", hints=["science_research"])
    run = wf.build_run(rag, "zzz qqq", readiness=ready, decision=decision)
    assert len(run["nodes"]) == len(rag.stages)
    assert run["nodes"][0]["depends_on"] == []
    assert run["nodes"][1]["depends_on"] == [run["nodes"][0]["node_id"]]
    assert [c["compartment_key"] for c in run["replication_cascade"]] == ["science_research"]
    assert run["decision"]["selected"] == ["science_research"]
    assert run["readiness"]["status"] == "ready"


def test_catalogue_and_export():
    data = wf.catalogue(readiness={"freellmapi": "ready"})
    assert data["count"] == 20
    assert len(data["compartments"]) == 5
    assert len(data["anatomy"]) == 7
    assert len(data["tech_stack"]) == 6
    assert len(data["production_layer"]) == 5
    assert data["replication"]["order"][0] == "Social and Welfare"
    assert "no achievement attestation" in " ".join(data["replication"]["fail_closed"])
    with tempfile.TemporaryDirectory() as tmp:
        path = wf.export_catalogue(Path(tmp) / "catalogue.json")
        exported = json.loads(Path(path).read_text(encoding="utf-8"))
        assert exported["count"] == 20
        assert exported["workflows"][0]["compartment_agnostic"] is True


# ---------------------------------------------------------------------------
# Admin integration: sequential fail-closed gates + attestation-gated cascade
# ---------------------------------------------------------------------------

class _Harness:
    def __init__(self, readiness, fail_compartment=None):
        self.readiness = readiness
        self.fail_compartment = fail_compartment
        self.replicas = []
        self.saved = {}

    def install(self):
        self._orig = {
            "readiness": admin._component_status_map,
            "init": admin.init_automaton_states,
            "new_run": admin.new_run,
            "save_run": admin.save_run,
            "transition": admin.transition,
            "create": admin.create_worker_for_agent,
            "complete": admin.complete_worker_for_agent,
            "replicate": admin.replicate_agent,
        }
        admin._component_status_map = lambda: dict(self.readiness)
        admin.init_automaton_states = lambda: None
        admin.new_run = self._new_run
        admin.save_run = self._save_run
        admin.transition = lambda *a, **k: None
        admin.create_worker_for_agent = lambda **k: {"worker_id": "w-test"}
        admin.complete_worker_for_agent = lambda *a, **k: None
        admin.replicate_agent = self._replicate

    def restore(self):
        admin._component_status_map = self._orig["readiness"]
        admin.init_automaton_states = self._orig["init"]
        admin.new_run = self._orig["new_run"]
        admin.save_run = self._orig["save_run"]
        admin.transition = self._orig["transition"]
        admin.create_worker_for_agent = self._orig["create"]
        admin.complete_worker_for_agent = self._orig["complete"]
        admin.replicate_agent = self._orig["replicate"]

    def _new_run(self, task, targets, meta=None):
        return {"id": "run-test", "task": task, "targets": targets, "status": "queued",
                "steps": [], "meta": meta or {}, "result": None, "error": None}

    def _save_run(self, run):
        self.saved[run["id"]] = run

    def _replicate(self, source_name, purpose, compartment, reports_to):
        if self.fail_compartment == compartment:
            return {"ok": False, "error": "spawn refused"}
        self.replicas.append(compartment)
        return {"ok": True, "replica_name": f"replica-{len(self.replicas)}"}


def _run_with(readiness, **kwargs):
    harness = _Harness(readiness, fail_compartment=kwargs.pop("fail_compartment", None))
    harness.install()
    try:
        run = admin.execute_workflow(**kwargs)
    finally:
        harness.restore()
    return run, harness


def test_no_attestation_no_cascade():
    rag = wf.get_workflow("rag")
    ready = {name: "ready" for name in rag.referenced_components()}
    run, harness = _run_with(
        ready, goal="zzz qqq", workflow="rag",
        mode="plan", achieved=False, replicate=True,
        compartments=["science_research"], max_replicas=5,
    )
    result = run["result"]
    assert run["status"] == "completed", run
    assert result["gates_open"] is True
    assert result["cascade_state"] == "skipped"
    assert "attestation" in result["cascade_halt_reason"]
    assert harness.replicas == []
    assert result["replication_cascade"][0]["state"] == "skipped"


def test_attested_cascade_spawns_only_decided():
    rag = wf.get_workflow("rag")
    ready = {name: "ready" for name in rag.referenced_components()}
    run, harness = _run_with(
        ready, goal="zzz qqq", workflow="rag",
        mode="attest", achieved=True, replicate=True,
        compartments=["science_research"], max_replicas=5,
    )
    result = run["result"]
    assert result["achievement_attested"] is True
    assert result["cascade_state"] == "spawned"
    assert harness.replicas == ["Science and Research"]
    assert result["replicas_spawned"] == 1
    # Business and Trade was not implicated, so it was never spawned.
    assert "Business and Trade" not in harness.replicas


def test_closed_gate_skips_downstream():
    rag = wf.get_workflow("rag")
    ready = {name: "ready" for name in rag.referenced_components()}
    ready["freellmapi"] = "blocked"  # the final generation stage
    run, harness = _run_with(
        ready, goal="zzz qqq", workflow="rag",
        mode="plan", achieved=True, replicate=True,
        compartments=["science_research"], max_replicas=5,
    )
    statuses = [s["status"] for s in run["steps"]]
    assert statuses == ["completed", "completed", "blocked"], statuses
    assert run["status"] == "failed"
    assert run["result"]["gates_open"] is False
    assert harness.replicas == []


def test_first_failed_spawn_halts_cascade():
    rag = wf.get_workflow("rag")
    ready = {name: "ready" for name in rag.referenced_components()}
    run, harness = _run_with(
        ready, goal="zzz qqq", workflow="rag",
        mode="plan", achieved=True, replicate=True,
        compartments=["social_welfare", "business_trade"],
        max_replicas=5, fail_compartment="Social and Welfare",
    )
    cascade = run["result"]["replication_cascade"]
    assert cascade[0]["state"] == "failed"
    assert cascade[1]["state"] == "skipped"
    assert "halted" in cascade[1]["reason"]
    assert run["result"]["cascade_state"] == "halted"
    assert harness.replicas == []


# ---------------------------------------------------------------------------
# Architecture: the deeper reference stack mapped onto this repo
# ---------------------------------------------------------------------------

def test_stack_layers_realized():
    snap = stack.stack_snapshot()
    by_key = {l["key"]: l["status"] for l in snap["layers"]}
    # All six layers are realized by repo modules; none needs a vendor tool.
    assert set(by_key) == {
        "foundation_models", "agent_frameworks", "tools_protocols",
        "memory_knowledge", "runtime_hosting", "interface_ui",
    }, by_key
    assert all(s == "realized" for s in by_key.values()), by_key
    for layer in snap["layers"]:
        assert any(o["exists"] for o in layer["ours"]), layer["key"]
        # Vendor analogues are orientation only: nothing asserts on them.
        assert all(a for a in layer["analogues"]), layer["key"]


def test_stack_protocol_and_semantic():
    snap = stack.stack_snapshot()
    assert snap["protocol"]["mcp"]["status"] == "implemented"
    assert snap["protocol"]["function_calling"]["status"] == "implemented"
    # A2A is a known gap, surfaced as one.
    assert snap["protocol"]["a2a"]["status"] == "not_implemented"
    assert snap["semantic"]["entities"] and snap["semantic"]["relationships"]
    assert len(snap["semantic"]["glossary"]) >= 4
    gap_layers = {g["layer"] for g in snap["gaps"]}
    assert "protocol:a2a" in gap_layers
    assert any("vector" in g["gap"] for g in snap["gaps"] if g["layer"] == "semantic")
    assert any("no in-process model gateway" in g["gap"] for g in snap["gaps"] if g["layer"] == "Foundation models")


def test_stack_production_and_governance():
    snap = stack.stack_snapshot()
    assert {c["concern"] for c in snap["production"]} >= {
        "Observability", "Evaluation", "Guardrails", "Agent identity",
    }
    products = snap["data_products"]
    assert len(products) >= 10
    assert all(p["classification"] and p["contract"] for p in products)
    governance = snap["governance"]
    assert governance["access_model"]["status"] == "enforced"
    assert governance["lineage"]["status"] == "recorded"
    assert governance["audit"]["status"] in ("append_only", "missing")


def test_stage_depth_contract():
    for w in wf.WORKFLOWS:
        assert stack.depth_readiness(w, {})["status"] == "ready", w.slug
    rag = wf.get_workflow("rag")
    for stage in rag.stages:
        depth = stack.stage_depth(rag, stage)
        assert depth["status"] == "specified"
        assert depth["prompt"]["role"] and depth["prompt"]["output_schema"]
        assert depth["context"]["token_budget"] > 0
        assert depth["harness"]["components"] and depth["harness"]["verifier"]
        assert depth["loop"]["success_criteria"]
        assert depth["loop"]["max_iterations"] >= 1
        assert depth["loop"]["budget"]["tokens"] > 0
        assert depth["loop"]["stop_conditions"]
    depth = stack.depth_readiness(rag, {})
    assert depth["status"] == "ready"
    for node in depth["stages"]:
        assert node["status"] == "ready", node
        assert node["kind"]
        # Every gate has an independent verifier (admin when the stage has none).
        if node["stage"] in {s.key for s in rag.stages if s.gate}:
            assert node["verifier_mode"] in ("component", "admin"), node


def test_environments():
    envs = stack.ENVIRONMENTS
    assert len(envs) >= 10
    by = {e["component"]: e for e in envs}
    assert set(by) >= {"vibetrading", "agentic-inbox", "agent-desktop", "Pix-Payment-System"}
    assert by["Pix-Payment-System"]["kind"] == "payment_settlement"
    assert by["vibetrading"]["kind"] == "trading_zero_funding"
    assert by["agentic-inbox"]["kind"] == "intake_document_store"
    # Every registered subplatform exists as an environment.
    from orchestrator.registry import subplatforms
    assert {sp["name"] for sp in subplatforms()} <= set(by)


def test_stage_environment():
    rag = wf.get_workflow("rag")
    envs = [stack.stage_environment(rag, s) for s in rag.stages]
    assert all(envs), "every stage must name its environment"
    flat = {e for es in envs for e in es}
    assert flat <= set(stack.ENV_BY_COMPONENT)
    assert "Scrapling" in flat and "agentic-inbox" in flat and "freellmapi" in flat
    par = wf.get_workflow(2)
    assert "AutoHedge" in stack.stage_environment(par, par.stages[0])
    assert stack.stage_environment(par, par.stages[1]) == ["grok-jev-router"]


def test_environment_profiles():
    profiles = stack.environments({"vibetrading": "blocked"})
    by = {p["component"]: p for p in profiles}
    assert by["vibetrading"]["runtime_status"] == "blocked"
    assert by["vibetrading"]["scope"] == "vibetrading-main"
    assert by["agentic-inbox"]["spec"]["exists"]
    assert "mongodb" in by["LibreChat"]["spec"]["services"]
    assert by["spider-ai--trading-bot"]["spec"]["toolchains"] == {"rust": "stable"}


def test_authority_has_environments():
    rag = wf.get_workflow("rag")
    ready = {n: "ready" for n in rag.referenced_components()}
    item = next(c for c in wf.decide_compartments(rag, "zzz qqq", hints=["science_research"])["candidates"] if c["selected"])
    auth = stack.compartment_authority(rag, item, item, ready)
    assert auth["environments"]
    assert all(e["scope"] and e["posture"] for e in auth["environments"])
    assert "environment_transfer" in auth


def test_compartment_authority():
    rag = wf.get_workflow("rag")
    ready = {n: "ready" for n in rag.referenced_components()}
    decision = wf.decide_compartments(rag, "zzz qqq", hints=["science_research"])
    item = next(c for c in decision["candidates"] if c["selected"])
    authority = stack.compartment_authority(rag, item, item, ready)
    assert authority["spawn_allowed"] is True
    assert authority["department"].startswith("Science Research")

    # Parallelization: the aggregate gate is attested by a component.
    par = wf.get_workflow(2)
    par_ready = {n: "ready" for n in par.referenced_components()}
    par_item = next(c for c in wf.decide_compartments(par, "zzz qqq", hints=["business_trade"])["candidates"] if c["selected"])
    par_auth = stack.compartment_authority(par, par_item, par_item, par_ready)
    assert par_auth["spawn_allowed"] is True
    assert any(g["mode"] == "component" for g in par_auth["gates"])
    blocked = dict(par_ready); blocked["grok-jev-router"] = "blocked"
    par_auth2 = stack.compartment_authority(par, par_item, par_item, blocked)
    assert par_auth2["spawn_allowed"] is False
    assert any("grok-jev-router" in b for b in par_auth2["blockers"])

    # A workflow with no gate can never be attested (admin attests, engine
    # never accepts the spawn because nothing proves the outcome).
    class NoGate:
        stages = ()

    auth3 = stack.compartment_authority(NoGate(), item, item, {})
    assert auth3["spawn_allowed"] is False
    assert any("no verification gate" in b for b in auth3["blockers"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")

