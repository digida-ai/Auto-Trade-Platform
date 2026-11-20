"""Free local alternative to the paid TypeSafe Jev endpoint.

Enabled with JEV_OFFLINE=1 (written by `npm run init`). It derives the same
answer shape the router expects from the task state, using conservative
heuristics. Risky/external actions still resolve to `ask_human`, so this
fallback never lowers the platform's fail-closed safety posture.
"""
from __future__ import annotations

import os
import time

_EXTERNAL_WORDS = (
    "http", "post", "send", "email", "publish", "deploy", "transfer",
    "payment", "trade", "order", "withdraw", "upload", "push",
)
_RISK_WORDS = (
    "delete", "remove", "transfer", "payment", "trade", "fund",
    "key", "secret", "credential", "wallet", "live",
)
_PREVIEW_WORDS = ("dry", "preview", "simulate", "backtest", "paper")


def _choice(name: str, confidence: float):
    return {
        "choice": name,
        "confidence": confidence,
        "probabilities": {name: confidence},
    }


def _yes(value: float):
    return {"noul": value, "probability": value}


def evaluate(state, questions: dict | None = None, model: str = "local-heuristic-v1",
             timeout: float = 30.0) -> dict:
    """Return a deterministic, offline answer set for ``jev.questions.decide``."""
    started = time.perf_counter()
    state = state if isinstance(state, dict) else {"task": str(state)}

    text = " ".join(
        str(state.get(key, ""))
        for key in ("task", "proposed_action", "notes", "last_result")
    ).lower()
    attempts = int(state.get("attempts", 0) or 0)

    external = 0.9 if any(word in text for word in _EXTERNAL_WORDS) else 0.1
    risk = 3.0 if any(word in text for word in _RISK_WORDS) else 1.0
    reversible = 0.2 if external >= 0.5 or risk >= 2.5 else 0.8
    done = 1.0 if any(word in text for word in ("complete", "completed", "already done")) else 0.0
    looping = 0.9 if attempts >= 3 or "failed" in text else 0.0
    necessary = 0.8

    if external >= 0.5 or risk >= 2.5 or reversible < 0.3 or attempts >= 3:
        lane, confidence = "ask_human", 0.95
    elif any(word in text for word in _PREVIEW_WORDS):
        lane, confidence = "dry_run", 0.85
    else:
        lane, confidence = "proceed", 0.8

    answers = {
        "lane": _choice(lane, confidence),
        "risk": {"score": risk},
        "reversible": _yes(reversible),
        "external": _yes(external),
        "necessary": _yes(necessary),
        "done": _yes(done),
        "looping": _yes(looping),
        "specialist": _choice("none", 0.0),
    }
    return {
        "answers": answers,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "model": os.environ.get("JEV_MODEL", model),
        "ms": round((time.perf_counter() - started) * 1000),
        "cost_usd": 0.0,
        "offline": True,
    }
