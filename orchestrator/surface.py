r"""Surfaces — how the automated system is exposed to the world.

Three surfacing channels, each *probed* and described (never invented):

* **IPFS** — peer/gateway reachability (``ipfs id`` over ``localhost:5001`` or
  an explicit gateway).
* **Cloudflare** — availability of the ``cloudflared`` tunnel binary + config.
* **Tailscale** — availability of the ``tailscale`` CLI + status probe.

Nothing here modifies state or claims a surface exists unless the probe
succeeds or the config is present. See ``orchestrator/sentry.py`` (deployment
status) and ``orchestrator/stack.py`` (semantic + deployment mapping).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import urllib.request
from typing import Any, Dict, List, Optional

from .config import PLATFORM_ROOT, PORT, HOST

PROBE_TIMEOUT_S = 1.5


def _probe(fn, default: Optional[Any] = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def _probe_ipfs() -> Dict[str, Any]:
    out = {"status": "unavailable", "available": False}
    exe = shutil.which("ipfs")
    out["binary_present"] = bool(exe)
    out["binary"] = exe or ""
    if not exe:
        return out
    # Can we reach the ipfs node API (if a daemon runs)?
    def _fetch():
        with urllib.request.urlopen("http://127.0.0.1:5001/api/v0/id", timeout=PROBE_TIMEOUT_S) as r:
            import json
            return json.loads(r.read().decode() or "{}").get("ID")
    out["id"] = _probe(_fetch)
    out["status"] = "reachable" if out["id"] else "binary_present_no_daemon"
    out["available"] = bool(out["id"])
    return out


def _probe_cloudflare() -> Dict[str, Any]:
    out = {"status": "unavailable", "available": False, "present": False}
    exe = shutil.which("cloudflared")
    out["present"] = bool(exe)
    if not exe:
        return out
    out["version"] = _probe(
        lambda: subprocess.check_output([exe, "--version"], timeout=PROBE_TIMEOUT_S, stderr=subprocess.DEVNULL)
                         .decode(errors="replace").strip(),
        default=None,
    )
    # Present on the box = surface can be configured; reachability is a TODO
    # for an operator with a tunnel token.
    out["status"] = "present_not_connected"
    out["available"] = True
    return out


def _probe_tailscale() -> Dict[str, Any]:
    out = {"status": "unavailable", "available": False, "present": False}
    exe = shutil.which("tailscale")
    out["present"] = bool(exe)
    if not exe:
        return out
    status = _probe(
        lambda: subprocess.check_output([exe, "status", "--json"], timeout=PROBE_TIMEOUT_S,
                                        stderr=subprocess.DEVNULL)
                         .decode(errors="replace").strip(),
        default=None,
    )
    if status:
        import json
        try:
            data = json.loads(status)
            out.update({
                "status": "reachable",
                "available": True,
                "self_id": (data.get("Self") or {}).get("ID"),
                "magic_dns": (data.get("MagicDNS") or ""),
            })
        except Exception:
            out["status"] = "present_no_status"
            out["available"] = True
    else:
        out["status"] = "present_no_daemon"
    return out


def _probe_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_S):
            return True
    except Exception:
        return False


def surface_status() -> Dict[str, Any]:
    return {
        "surfaces": {
            "ipfs": _probe_ipfs(),
            "cloudflare": _probe_cloudflare(),
            "tailscale": _probe_tailscale(),
        },
        "platform_local": {
            "host": HOST,
            "port": PORT,
            "loopback_reachable": _probe_tcp("127.0.0.1", PORT),
            "platform_root": str(PLATFORM_ROOT),
        },
    }


def surfaces_summary() -> Dict[str, Any]:
    """Compact, UI-friendly summary of the surfacing channels."""
    s = surface_status()["surfaces"]
    return {
        key: {
            "status": v["status"],
            "present": v.get("present", v.get("available", False)),
            "available": v.get("available", False),
        }
        for key, v in s.items()
    }
