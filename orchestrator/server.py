import json
import os
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .config import HOST, PORT, ensure_dirs, PLATFORM_ROOT
from . import admin, agent_specs, components, geo, observability, skills
from .admin import (
    bootup_initiative, _watchdog_loop,
    register_mcp_servers_for_agents,
    create_worker_for_agent, complete_worker_for_agent, suspend_worker_for_agent,
    contribute_skill, use_skill,
    execute_plan_via_chain, get_orchestration_status,
)

DASHBOARD_PATH = Path(__file__).resolve().parent / "dashboard.html"


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind (and steal) a port
    # that is already in use, so a duplicate `npm start` looks like it worked
    # while requests go to either process. Disable reuse there so a duplicate
    # fails fast with a clear "port in use" message.
    allow_reuse_address = os.name != "nt"


def _json(handler: BaseHTTPRequestHandler, status: int, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def _html(handler: BaseHTTPRequestHandler, status: int, html: str):
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "AutoTradingOrchestrator/0.1"

    def log_message(self, fmt, *args):
        # concise logging
        print(f"[orchestrator] {self.address_string()} - {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/health":
            return _json(self, 200, {
                "status": "ok",
                "service": "autotrading-ideal-platform-orchestrator",
                "admin": "standalone-automaton",
                "model": "musashaban-port (constitution/soul/access/paywall-funding/automaton)",
                "authority": {"admin": ["erect_paywalls", "settle", "fund"], "reporting": "eagent->admin"},
                "platform_root": str(PLATFORM_ROOT),
            })

        if path == "/registry":
            try:
                return _json(self, 200, admin.registry_info())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/agents" or path == "/status":
            try:
                return _json(self, 200, admin.agent_status())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/agents/specs":
            return _json(self, 200, {"agents": agent_specs.summary()})

        if path == "/agents/specs/status":
            result = agent_specs.validate_all()
            states = admin.load_agent_states()
            replicas = [
                {
                    "name": n,
                    "replica_of": e.get("replica_of"),
                    "state": e.get("state"),
                    "spec_carried": isinstance(e.get("spec"), dict),
                    "constitution": ((e.get("constitution") or "")[:12] or None),
                }
                for n, e in (states.get("agents") or {}).items() if e.get("replica_of")
            ]
            return _json(self, 200, {
                "valid": result.get("valid"),
                "agents_total": result.get("agents_total"),
                "agents_valid": result.get("agents_valid"),
                "missing_specs": result.get("missing_specs", []),
                "replicas": replicas,
            })

        if path == "/agents/validate":
            try:
                result = agent_specs.validate_all()
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            return _json(self, 200 if result.get("valid") else 409, result)

        if path == "/agent-states":
            try:
                return _json(self, 200, admin.get_agent_states())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/reports":
            try:
                return _json(self, 200, admin.reports(run_id=None, limit=100))
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/components":
            try:
                return _json(self, 200, components.readiness())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path.startswith("/components/"):
            name = path[len("/components/"):].strip("/")
            try:
                info = components.component_readiness(name)
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            if not info:
                return _json(self, 404, {"error": "component not found", "name": name})
            return _json(self, 200, info)

        if path == "/observability":
            try:
                data = observability.observability_status()
                data["alerts"] = observability.get_alerts(active_only=True).get("alerts", [])
                return _json(self, 200, data)
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/alerts":
            try:
                active_only = (query.get("active", ["0"])[0] or "").lower() in ("1", "true", "yes")
                return _json(self, 200, observability.get_alerts(active_only=active_only))
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/skills":
            try:
                return _json(self, 200, skills.skills_summary())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path.startswith("/skills/"):
            skill_id = path[len("/skills/"):].strip("/")
            try:
                info = skills.get_skill(skill_id)
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            if not info:
                return _json(self, 404, {"error": "skill not found", "id": skill_id})
            return _json(self, 200, info)

        if path == "/geo/overview":
            try:
                return _json(self, 200, geo.map_overview())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/geo/places":
            try:
                category = query.get("category", [None])[0]
                limit = int(query.get("limit", ["200"])[0])
                return _json(self, 200, {"places": geo.list_places(category=category, limit=limit)})
            except Exception as e:
                return _json(self, 400, {"error": str(e)})

        if path == "/geo/automata":
            try:
                limit = int(query.get("limit", ["200"])[0])
                return _json(self, 200, {"locations": geo.list_automaton_locations(limit=limit)})
            except Exception as e:
                return _json(self, 400, {"error": str(e)})

        if path == "/geo/work-items":
            try:
                status = query.get("status", [None])[0]
                limit = int(query.get("limit", ["200"])[0])
                return _json(self, 200, {"work_items": geo.list_work_items(status=status, limit=limit)})
            except Exception as e:
                return _json(self, 400, {"error": str(e)})

        if path == "/geo/routes":
            try:
                status = query.get("status", [None])[0]
                limit = int(query.get("limit", ["200"])[0])
                return _json(self, 200, {"routes": geo.list_routes(status=status, limit=limit)})
            except Exception as e:
                return _json(self, 400, {"error": str(e)})

        if path == "/runs":
            try:
                return _json(self, 200, {"runs": admin.history(limit=50)})
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path.startswith("/runs/") and path.endswith("/reports"):
            # /runs/{id}/reports
            middle = path[len("/runs/"):-len("/reports")].strip("/")
            run_id = middle
            try:
                return _json(self, 200, admin.reports(run_id=run_id, limit=500))
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path.startswith("/runs/") and path.endswith("/lineage"):
            run_id = path[len("/runs/"):-len("/lineage")].strip("/")
            return _json(self, 200, admin.lineage(run_id))

        if path.startswith("/runs/"):
            run_id = path.split("/runs/", 1)[1].strip("/")
            run = admin.get_status(run_id)
            if not run:
                return _json(self, 404, {"error": "run not found", "id": run_id})
            return _json(self, 200, run)

        if path == "/api":
            return _json(self, 200, {
                "service": "AutoTrading Ideal Platform — Local Orchestrator",
                "admin": "standalone automaton (n8n-inspired workflow engine)",
                "model": "automaton (musashaban port): constitution/soul, states, reporting eagent->admin, admin paywall/settle/fund, 0-funding fail-closed",
                "endpoints": {
                    "GET /health": "health check",
                    "GET /registry": "platform registry, eagents, automaton model/authority",
                    "GET /agents": "live per-eagent automaton status (state, reports_to, active run)",
                    "GET /agents/specs": "per-eagent specs from agents/specs (schema, toolchain, env, guardrails, constitution binding)",
                    "GET /agents/specs/status": "spec validation status: all agents valid? replicas and whether they carry specs",
                    "POST /admin/specs/generate": "heal missing/stale specs via generate-agents.js --names=<n> (fallback: Python spec builder)",
                    "GET /agent-states": "persisted automaton states for admin and eagents",
                    "GET /reports": "recent reports (eagent->admin, admin->operator_audit)",
                    "GET /runs": "recent orchestration runs",
                    "GET /runs/{id}": "run status/result (poll for live progress; includes steps and report refs)",
                    "GET /runs/{id}/reports": "reports for a specific run",
                    "GET /components": "per-subplatform readiness (toolchain, deps, manifests, blockers)",
                    "GET /components/{name}": "readiness for one subplatform",
                    "GET /observability": "watchdog status and active alerts",
                    "GET /alerts": "alerts (?active=1 for active only)",
                    "GET /skills": "discovered platform skills",
                    "GET /skills/{id}": "one skill with instructions",
                    "GET /geo/overview": "map/manifest counts overview",
                    "GET /geo/places": "places (?category=&limit=)",
                    "GET /geo/automata": "automaton locations",
                    "GET /geo/work-items": "work items (?status=&limit=)",
                    "GET /geo/routes": "routes (?status=&limit=)",
                    "GET /workflows": "catalogue of 20 agentic workflows + compartments + anatomy (live readiness)",
                    "GET /workflows/{id|slug}": "one workflow with run preview",
                    "GET /admin/compartments": "five compartments and their replicated agents",
                    "GET /bootup": "last bootup survey + automation scope (control plane automated; component execution gated)",
                    "GET /admin/funding-state": "platform funding state (unfunded/float_funded/revenue_funded + amounts)",
                    "GET /admin/settlement/status": "RudiPay + funding/withdrawal state (port of musashaban payment/settlement)",
                    "GET /sentry/status": "Sentry department posture: deployment, hosting, security",
                    "GET /surface/status": "surfacing probes (Cloudflare, Tailscale, IPFS) + platform local",
                    "GET /admin/spend-authorizations": "recorded operator spend authorizations (tokens used by policy.evaluate)",
                    "POST /admin/spend-authorization": "record an operator spend authorization for an agent {agent, purpose, amount_usd?, approved_by?}",
                    "POST /admin/spend-authorization/revoke": "revoke a spend authorization {spend_auth_id}",
                    "GET /stack": "agentic architecture: 6 layers, agent loop, protocol/semantic layers, data products, governance, engineering layers",
                    "GET /data/products": "named data products (owner, classification, contract) with live probes",
                    "GET /runs/{id}/lineage": "lineage chain: goal -> decision -> stages -> cascade",
                    "POST /workflows/select": "pick a workflow pattern for a goal {goal}",
                    "POST /workflows/decide": "compartment necessity decision {goal, workflow?, compartments?, threshold?}",
                    "POST /workflows/execute": "build/attest a run and cascade {goal, workflow?, mode?, achieved?, replicate?, max_replicas?, compartments?, threshold?}",
                    "POST /tasks": "submit task {task, target?, command?, cwd?, timeout?, autorun?} (async, returns run id)",
                    "POST /alerts/watchdog": "run the watchdog and persist alerts",
                    "POST /geo/places": "upsert place",
                    "POST /geo/automata": "upsert automaton location",
                    "POST /geo/work-items": "upsert work item",
                    "POST /geo/routes": "plan route",
                    "POST /geo/manifests": "record manifest {kind, data}",
                },
            })


        if path == "/admin/directives":
            try:
                return _json(self, 200, admin.generate_profit_directives())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/admin/hierarchy":
            try:
                return _json(self, 200, admin.get_agent_hierarchy())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/workflows":
            try:
                return _json(self, 200, admin.workflow_catalogue())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path.startswith("/workflows/"):
            ref = path[len("/workflows/"):].strip("/")
            try:
                info = admin.workflow_detail(ref)
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            if not info:
                return _json(self, 404, {"error": "workflow not found", "ref": ref})
            return _json(self, 200, info)

        if path == "/admin/compartments":
            try:
                return _json(self, 200, admin.compartment_hierarchy())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/bootup":
            try:
                return _json(self, 200, admin.last_bootup_report())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/admin/funding-state":
            try:
                return _json(self, 200, admin.funding_state())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/admin/settlement/status":
            try:
                return _json(self, 200, admin.settlement_status())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/sentry/status":
            try:
                from orchestrator import sentry
                return _json(self, 200, sentry.get_sentry_status())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/surface/status":
            try:
                from orchestrator import surface
                return _json(self, 200, surface.surface_status())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/admin/spend-authorizations":
            try:
                auths = admin.spend_authorizations()
                return _json(self, 200, {"count": len(auths), "authorizations": auths})
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/stack":
            try:
                return _json(self, 200, admin.stack_snapshot())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/data/products":
            try:
                return _json(self, 200, admin.data_products())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/admin/orchestration-status":
            try:
                return _json(self, 200, admin.get_orchestration_status())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/admin/mcp-registration":
            try:
                return _json(self, 200, admin.register_mcp_servers_for_agents())
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if path in ("/", "/ui"):
            try:
                html = DASHBOARD_PATH.read_text(encoding="utf-8")
                return _html(self, 200, html)
            except Exception as e:
                return _json(self, 500, {"error": f"failed to load dashboard: {e}"})

        return _json(self, 404, {"error": "not found", "path": path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            return _json(self, 400, {"error": f"invalid json: {e}"})
        if not isinstance(payload, dict):
            return _json(self, 400, {"error": "json body must be an object"})

        try:
            if path == "/tasks":
                run = admin.run_task(payload)
                return _json(self, 200, run)


            if path == "/admin/directives":
                try:
                    target_list = payload.get("targets")
                    return _json(self, 200, admin.issue_profit_directives(targets=target_list))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/specs/generate":
                try:
                    names = payload.get("names")
                    if names is not None and not isinstance(names, list):
                        return _json(self, 400, {"error": "'names' must be a list of subplatform names"})
                    report = admin.ensure_specs(names=names, auto_generate=True)
                    return _json(self, 200, report)
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/spend-authorization":
                try:
                    agent = payload.get("agent") or payload.get("target")
                    if not agent:
                        return _json(self, 400, {"error": "'agent' is required"})
                    return _json(self, 200, admin.authorize_spend(
                        agent=agent,
                        purpose=payload.get("purpose") or payload.get("reason") or "operator spend authorization",
                        amount_usd=float(payload.get("amount_usd") or 0),
                        approved_by=payload.get("approved_by") or "operator",
                        reason=payload.get("reason"),
                        target=payload.get("target"),
                        scope=payload.get("scope"),
                        max_uses=payload.get("max_uses"),
                    ))
                except ValueError as e:
                    return _json(self, 400, {"error": str(e)})
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/spend-authorization/revoke":
                rec = admin.revoke_spend_authorization(payload.get("spend_auth_id") or "")
                if not rec:
                    return _json(self, 404, {"error": "spend authorization not found"})
                return _json(self, 200, rec)

            if path == "/workflows/select":
                return _json(self, 200, admin.select_workflow_for_goal(payload.get("goal") or ""))

            if path == "/workflows/decide":
                hints = payload.get("compartments") or payload.get("hints") or []
                if isinstance(hints, str):
                    hints = [h.strip() for h in hints.split(",") if h.strip()]
                return _json(self, 200, admin.workflow_decision(
                    goal=payload.get("goal") or payload.get("task") or "",
                    workflow=payload.get("workflow") or payload.get("id"),
                    compartments=hints,
                    threshold=float(payload.get("threshold") or 2.0),
                    max_compartments=int(payload.get("max_compartments", 5) or 5),
                ))

            if path == "/workflows/execute":
                hints = payload.get("compartments") or payload.get("hints") or []
                if isinstance(hints, str):
                    hints = [h.strip() for h in hints.split(",") if h.strip()]
                return _json(self, 200, admin.execute_workflow(
                    goal=payload.get("goal") or payload.get("task") or "",
                    workflow=payload.get("workflow") or payload.get("id"),
                    mode=payload.get("mode") or "plan",
                    achieved=bool(payload.get("achieved", False)),
                    replicate=bool(payload.get("replicate", True)),
                    max_replicas=int(payload.get("max_replicas", 5) or 5),
                    compartments=hints,
                    threshold=float(payload.get("threshold") or 2.0),
                ))

            if path == "/admin/funding":
                try:
                    kind = payload.get("kind")
                    amount = payload.get("amount_usd")
                    if not kind or amount is None:
                        return _json(self, 400, {"error": "'kind' and 'amount_usd' are required"})
                    return _json(self, 200, admin.record_funding(
                        kind=kind,
                        amount_usd=float(amount),
                        currency=payload.get("currency", "USD"),
                        source=payload.get("source"),
                        purpose=payload.get("purpose"),
                        run_id=payload.get("run_id"),
                        evidence=payload.get("evidence"),
                    ))
                except ValueError as e:
                    return _json(self, 400, {"error": str(e)})
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/replicate":
                try:
                    source = payload.get("source")
                    purpose = payload.get("purpose")
                    compartment = payload.get("compartment")
                    reports_to = payload.get("reports_to")
                    if not source:
                        return _json(self, 400, {"error": "'source' is required"})
                    if not purpose:
                        return _json(self, 400, {"error": "'purpose' is required"})
                    return _json(self, 200, admin.replicate_agent(
                        source_name=source,
                        purpose=purpose,
                        compartment=compartment,
                        reports_to=reports_to,
                    ))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/worker/create":
                try:
                    return _json(self, 200, admin.create_worker_for_agent(
                        agent_id=payload.get("agent_id"),
                        job_id=payload.get("job_id"),
                        purpose=payload.get("purpose"),
                        mcp_tools=payload.get("mcp_tools"),
                        skills=payload.get("skills"),
                    ))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/worker/complete":
                try:
                    return _json(self, 200, admin.complete_worker_for_agent(
                        worker_id=payload.get("worker_id"),
                        result=payload.get("result"),
                    ))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/skill/contribute":
                try:
                    return _json(self, 200, admin.contribute_skill(
                        agent_id=payload.get("agent_id"),
                        skill_id=payload.get("skill_id"),
                        name=payload.get("name"),
                        description=payload.get("description"),
                        tags=payload.get("tags"),
                        mcp_tools=payload.get("mcp_tools"),
                    ))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/skill/use":
                try:
                    return _json(self, 200, admin.use_skill(
                        skill_id=payload.get("skill_id"),
                        agent_id=payload.get("agent_id"),
                    ))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/admin/plan/execute":
                try:
                    return _json(self, 200, admin.execute_plan_via_chain(
                        plan_id=payload.get("goal", "plan").replace(" ", "-").lower(),
                        goal=payload.get("goal"),
                        target_agents=payload.get("target_agents"),
                    ))
                except Exception as e:
                    return _json(self, 500, {"error": str(e)})

            if path == "/alerts/watchdog":
                return _json(self, 200, observability.run_watchdog())

            if path == "/geo/places":
                return _json(self, 200, geo.upsert_place(payload))
            if path == "/geo/automata":
                return _json(self, 200, geo.upsert_automaton_location(payload))
            if path == "/geo/work-items":
                return _json(self, 200, geo.upsert_work_item(payload))
            if path == "/geo/routes":
                return _json(self, 200, geo.plan_route(payload))
            if path == "/geo/manifests":
                kind = payload.get("kind")
                data = payload.get("data")
                if not kind:
                    return _json(self, 400, {"error": "'kind' is required"})
                if not isinstance(data, dict):
                    data = {k: v for k, v in payload.items() if k != "kind"}
                return _json(self, 200, geo.record_manifest(str(kind), data))
        except geo.GeoError as e:
            return _json(self, 400, {"error": str(e)})
        except ValueError as e:
            return _json(self, 400, {"error": str(e)})
        except Exception as e:
            return _json(self, 500, {"error": str(e)})

        return _json(self, 404, {"error": "not found", "path": path})


def serve(host: str = HOST, port: int = PORT):
    ensure_dirs()
    recovered = admin.recover_interrupted_runs()
    if recovered.get("recovered_runs") or recovered.get("reset_agents"):
        print(
            "Recovered state from previous run: "
            f"runs={recovered.get('recovered_runs')} agents={recovered.get('reset_agents')}"
        )

    # --- Spec healing: every registered agent has a valid constitution-bound spec ---
    try:
        spec_report = admin.ensure_specs()
        v = spec_report.get("validation", {})
        print(f"[specs] constitution-bound validation: valid={v.get('valid')} missing={v.get('missing_specs', [])} generated={len(spec_report.get('generated') or [])} skipped={len(spec_report.get('skipped') or [])}")
        migrate_report = admin.migrate_replica_specs()
        if migrate_report.get("migrated"):
            print(f"[specs] backfilled carried specs on replicas: {migrate_report['migrated']}")
    except Exception as e:
        print(f"[specs] WARNING: spec healing failed: {e}")

    # --- Autonomous bootup: admin takes control at startup ---
    print("[bootup] Admin agent taking control of platform...")
    try:
        boot_report = admin.bootup_initiative()
        print("[bootup]", boot_report.get("summary", "bootup complete"))
        auto = boot_report.get("automation") or {}
        print(
            f"[bootup] automation: {auto.get('mode', 'unknown')} — "
            f"{auto.get('claim', 'control plane only')}"
        )
        for sp in boot_report.get("subplatforms", []):
            status = sp.get("status", "unknown")
            errors = sp.get("errors", [])
            err_str = f" ({'; '.join(errors[:2])})" if errors else ""
            print(f"  [bootup] {sp['name']}: {status}{err_str}")
        watchdog = boot_report.get("watchdog", {})
        if watchdog.get("alerts_total", 0) > 0:
            print(f"[bootup] {watchdog['alerts_total']} active alert(s) surfaced by watchdog")
    except Exception as e:
        print(f"[bootup] ERROR during bootup survey: {e}")

    # --- Background watchdog: keeps admin in control, surfaces hiccups ---
    watchdog_thread = threading.Thread(target=_watchdog_loop, args=(60,), daemon=True)
    watchdog_thread.start()
    print("[watchdog] background watchdog started (60s interval)")

    server = None
    try:
        server = _Server((host, port), Handler)
    except OSError as e:
        in_use = getattr(e, "winerror", None) in (10048, 10013) or getattr(e, "errno", None) in (48, 98, 13)
        print(f"ERROR: cannot listen on http://{host}:{port} - {e}")
        if in_use:
            print("Another process is already using that port. Stop it, or set ORCHESTRATOR_PORT to a free port.")
        raise SystemExit(2)
    print(f"AutoTrading Ideal Platform Orchestrator (admin automaton, standalone)")
    print(f"Platform root: {PLATFORM_ROOT}")
    print(f"Listening on http://{host}:{port}")
    print(f"Dashboard: http://{host}:{port}/  (API: /api /health /registry /agents /components /agent-states /reports /runs /tasks)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
