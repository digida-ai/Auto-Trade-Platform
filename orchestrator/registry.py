import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import REGISTRY_PATH, PLATFORM_ROOT


def load_registry() -> Dict[str, Any]:
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def subplatforms() -> List[Dict[str, Any]]:
    return load_registry().get("subplatforms", [])


def get_subplatform(name: str) -> Optional[Dict[str, Any]]:
    name_l = name.lower()
    for sp in subplatforms():
        if sp.get("name", "").lower() == name_l or sp.get("component", "").lower() == name_l:
            return sp
    return None


def component_path(sp: Dict[str, Any]) -> Path:
    return PLATFORM_ROOT / sp.get("component", "")


def route_task(task: str, target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Route a task to one or more subplatform eagents.

    - If target is provided, route to that subplatform (by name or component).
    - Otherwise, keyword-match against name/description/component.
    - If no match, return all (admin may fan-out) is not default; return empty to signal admin decision.
    """
    if target:
        sp = get_subplatform(target)
        return [sp] if sp else []

    t = (task or "").lower()
    matches = []
    for sp in subplatforms():
        hay = " ".join([
            sp.get("name", ""),
            sp.get("component", ""),
            sp.get("description", ""),
        ]).lower()
        # match on name tokens and key terms
        tokens = [sp.get("name", "").lower(), sp.get("component", "").lower().replace("-main", "")]
        tokens += [w for w in sp.get("name", "").lower().replace("-", " ").split() if len(w) > 2]
        if any(tok and tok in t for tok in tokens if tok):
            matches.append(sp)
            continue
        # keyword hints
        hints = {
            "pix": "Pix-Payment-System", "payment": "Pix-Payment-System", "mpesa": "Pix-Payment-System",
            "momo": "Pix-Payment-System", "airtel": "Pix-Payment-System",
            "trade": "spider-ai--trading-bot", "trading": "spider-ai--trading-bot", "hedge": "AutoHedge",
            "scrape": "Scrapling", "scraping": "Scrapling",
            "chat": "LibreChat", "llm": "freellmapi", "video": "hyperframes",
            "inbox": "agentic-inbox", "email": "agentic-inbox",
            "desktop": "agent-desktop", "terminal": "FinceptTerminal",
            "ads": "claude-ads", "vibe": "vibetrading", "vtuber": "Open-LLM-VTuber",
            "higgsfield": "Open-Higgsfield-AI", "router": "grok-jev-router", "jev": "grok-jev-router",
        }
        for kw, name in hints.items():
            if kw in t and sp.get("name") == name and sp not in matches:
                matches.append(sp)
                break
    # de-dupe preserving order
    seen = set()
    out = []
    for sp in matches:
        n = sp.get("name")
        if n not in seen:
            seen.add(n)
            out.append(sp)
    return out
