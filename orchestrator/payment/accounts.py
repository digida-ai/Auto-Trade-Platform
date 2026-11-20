"""Operator accounts & settlement config for the ported payment/settlement system.

Values discovered in the operator's automaton environment (`.automaton/`):

* `wallet.json`        — EVM/Tron/BTC/Solana/Stellar identities + derivation salt
* `automaton.json`     — treasury policy, payment assets, marketplaces, endpoints
* `.automaton/sms-otp.key` — the SMS-OTP gateway secret (redacted here; supplied
  via env or provisioning)

This module never fabricates payment secrets. Fields marked ``PROVESETUP`` are
empty until the operator fills them from the source configs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

ACCOUNTS_PATH = Path(__file__).resolve().parent / "accounts.json"


def _default_accounts() -> Dict[str, Any]:
    return {
        "version": 1,
        "status": "unprovisioned",
        # Operator-provided identities. The single trusted wallet is the
        # TrustWallet address; other chain addresses are not used because
        # ownership was not verified (fail-closed: settlements go to the
        # TrustWallet address only).
        "creatorAddress": "0x61f9FAC0A44e0D975673CB97fc4A615B16398846",
        "walletAddress": "0x61f9FAC0A44e0D975673CB97fc4A615B16398846",
        "wallets": {
            "evm": "0x61f9FAC0A44e0D975673CB97fc4A615B16398846",
            "tron": None,
            "bitcoin": None,
            "solana": None,
            "stellar": None,
        },
        "settlement": {
            "spend": {"autoSettleTo": "0x61f9FAC0A44e0D975673CB97fc4A615B16398846"},
            "rudipay": {
                "admins": ["0x61f9FAC0A44e0D975673CB97fc4A615B16398846"],
                "countryCode": "UG",
                "preferredRouterPhones": [
                    "+256780135102",
                    "+256780698353",
                    "+256700677543",
                    "+256731235987",
                    "+256707319020",
                ],
            },
            "socials": {
                # +256700677543 is the Airtel number and doubles as the WhatsApp/Telegram
                # channel phone — the socials surface.
                "whatsapp": "+256700677543",
                "telegram": "+256700677543",
            },
            "bank": {
                "name": "EXIM Uganda",
                "code": "0080010865",
                "swift": "EXTNUGAKXXX",
            },
            "onchain": {"creatorAddress": "0x61f9FAC0A44e0D975673CB97fc4A615B16398846"},
        },
        "payment": {
            "defaultAsset": "usdt",
            "assetPriority": ["usdt", "dai", "eurc", "usdc"],
        },
        "marketplaces": [
            {"id": "jumia", "label": "Jumia Uganda Seller Center", "status": "provided"},
            {"id": "jiji", "label": "Jiji Uganda", "status": "provided"},
            {"id": "alibaba", "label": "Alibaba.com", "status": "provided"},
            {"id": "kilimall", "label": "Kilimall Uganda", "status": "provided"},
            {"id": "facebook", "label": "Facebook Commerce", "status": "provided"},
            {"id": "whatsapp", "label": "WhatsApp +256700677543", "status": "provided", "phone": "+256700677543"},
            {"id": "telegram", "label": "Telegram +256700677543", "status": "provided", "phone": "+256700677543"},
        ],
        "endpoints": {
            "ollamaBaseUrl": "http://127.0.0.1:11434",
            "freellmapiBaseUrl": "http://127.0.0.1:31416/v1",
            "ownCloudApiUrl": "http://127.0.0.1:4080",
        },
        "provisioning": {
            "ownCloudApiKey": "",
            "ownCloud.automatonId": "",
            "settlement.otp.gatewaySecret": "",
            "settlement.otp.key": "",
            "settlement.paywall.redirectUrl": None,
        },
    }


def _save_accounts(data: Dict[str, Any]) -> None:
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_accounts(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or ACCOUNTS_PATH
    if not target.exists():
        data = _default_accounts()
        _save_accounts(data)
        return data
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return _default_accounts()


def validate_accounts(accounts: Dict[str, Any]) -> Dict[str, Any]:
    """Check money-destination facts exist where required (fail-closed)."""
    errors: List[str] = []
    warnings: List[str] = []
    if not (accounts.get("creatorAddress") or "").startswith("0x"):
        errors.append("settlement creatorAddress is required and must be an 0x address")
    settle_to = (accounts.get("settlement", {}).get("spend", {}) or {}).get("autoSettleTo", "")
    if settle_to and not settle_to.startswith("0x"):
        errors.append("settlement.spend.autoSettleTo must be an 0x address")
    rpp = accounts.get("settlement", {}).get("rudipay", {}).get("preferredRouterPhones") or []
    for p in rpp:
        if not normalized_phone(p or ""):
            warnings.append(f"preferredRouterPhones: {p!r} does not look like a phone number")
    prov = accounts.get("provisioning", {})
    for key in ("settlement.otp.gatewaySecret", "settlement.otp.key"):
        if not (prov.get(key) or "").strip():
            warnings.append(f"provisioning gap: {key} is empty (needs operator provisioning)")
    return {"errors": errors, "warnings": warnings, "accounts": accounts}


def otp_secrets(accounts: Dict[str, Any]) -> Dict[str, str]:
    prov = accounts.get("provisioning", {})
    return {
        "gatewaySecret": prov.get("settlement.otp.gatewaySecret", "") or "",
        "key": prov.get("settlement.otp.key", "") or "",
    }


def who_is_admin(accounts: Dict[str, Any], owner_id: Optional[str]) -> bool:
    """Is this user allowed to register rudi routers? (RudiPay RBAC)."""
    admins = accounts.get("settlement", {}).get("rudipay", {}).get("admins") or []
    return bool(admins) and owner_id in admins


def normalized_phone(phone: str) -> str:
    """MSISDN-first normalization: digits plus at most one leading '+'."""
    cleaned = "".join(ch for ch in (phone or "").strip() if ch.isdigit() or ch in "+()")
    out: List[str] = []
    seen_plus = False
    for ch in cleaned:
        if ch == "+":
            if not seen_plus:
                seen_plus = True
                out.append("+")
        elif ch.isdigit():
            out.append(ch)
    return "".join(out)


def otp_gateway_status() -> Dict[str, Any]:
    accounts = load_accounts()
    sec = otp_secrets(accounts)
    ok = bool(sec["gatewaySecret"]) and bool(sec["key"])
    return {
        "configured": ok,
        "gatewaySecret_present": bool(sec["gatewaySecret"]),
        "key_present": bool(sec["key"]),
        "note": "OTP secrets are redacted from accounts.json; they are supplied by the operator (automaton .automaton/sms-otp.key) or provisioning.",
    }


def provider_secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()
