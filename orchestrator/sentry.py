r"""Sentry department — deploy, hosting and security sentry agent for the platform.

The **sentry** eagent is a new Automaton department agent (reports to admin,
like every eagent). Its discipline is **watch and sound an alarm, never move
money or do work**:

* **deploy** — surface status (cloudflare/tailscale/ipfs), deployment artifacts
  (surface manifests, deployment logs), and deploy-time checks.
* **hosting** — the platform's hosting posture: surfaces reachable, local host
  facts, backups/retention, and the deployment configs that drive it.
* **security** — the security posture: protected paths, deny rules, active
  alerts, spend authorizations, funding state, and the musashaban security
  roles.

The sentry agent's discipline is also: "a finding is a finding, not a fix" —
the engine reports, it does not deploy, pay, or change policy. Any change
requires the admin + human.
"""

from __future__ import annotations

from typing import Any, Dict

from .observability import get_alerts
from .policy import PROTECTED_PATHS, load_spend_authorizations
from .surface import surface_status, surfaces_summary


def get_sentry_status() -> Dict[str, Any]:
    """Status of the sentry department: deployment, hosting, security."""
    alerts = get_alerts(active_only=True)
    spend_auths = load_spend_authorizations()
    return {
        "department": "Sentry",
        "dept_key": "sentry",
        "department_agent": "sentry",
        "admin_agent": "sentry_admin",
        "reports_to": "admin",
        "sentry_approvals_note": (
            "The musashaban sentry system has 7 human-gated roles (address_confirm, "
            "stock_release, dropship_dispatch, delivery_confirm, ad_spend, payout_release). "
            "Their proofs: on-chain payouts, order fulfillment, etc. Findings here are "
            "audit-ready, not self-approved."
        ),
        "deployment": {
            "surfaces": surface_status(),
            "surfaces_summary": surfaces_summary(),
            "hosting": {
                "platform": {
                    "host": None,
                    "port": None,
                    "dashboard_path": "/",
                },
                "gapinvex_production": _gapinvex_summary(),
            },
            "artifacts": _deployment_artifacts(),
        },
        "security": {
            "alerts_active": alerts.get("count", 0),
            "alerts": [
                {"key": a.get("key"), "severity": a.get("severity"), "title": a.get("title")}
                for a in alerts.get("alerts", [])[:10]
            ],
            "spend_authorizations_active": len(spend_auths),
            "protected_paths": list(PROTECTED_PATHS),
            "env_switches": {
                "PLATFORM_ALLOW_LIVE_TRADING": __import__("os").environ.get("PLATFORM_ALLOW_LIVE_TRADING", "unset"),
                "PLATFORM_AGENT_COMMAND_LIMIT": __import__("os").environ.get("PLATFORM_AGENT_COMMAND_LIMIT", "30 (default)"),
            },
        },
    }


def _gapinvex_summary() -> Dict[str, Any]:
    from pathlib import Path

    p = Path(r"D:\Work\Dev\gapinvex\deploy\production.json")
    out: Dict[str, Any] = {"present": p.exists()}
    if p.exists():
        import json

        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.update({
                "environment": d.get("environment"),
                "hosts": [(h.get("name"), h.get("role"), h.get("domain")) for h in d.get("hosts", [])],
                "services": {k: v.get("name", k) for k, v in (d.get("services") or {}).items()},
                "security": (d.get("security") or {}).get("rate_limiting", {}).get("requests_per_minute"),
            })
        except Exception as e:
            out["parse_error"] = str(e)
    return out


def _deployment_artifacts() -> Dict[str, Any]:
    from pathlib import Path

    out = {}
    for name in ("gapinvex.service.template", "hosts.json", "hosts.example.json", "mirrors.json", "staging.json"):
        p = Path(r"D:\Work\Dev\gapinvex\deploy") / name
        out[name] = p.exists()
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(get_sentry_status(), indent=2, ensure_ascii=False))
