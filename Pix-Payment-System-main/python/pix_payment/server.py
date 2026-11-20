"""Live HTTP API for accounts, wallets, payments and settlement.

Standard library only, so it runs with the platform's Python toolchain without
extra services. Every mutation is recorded in the durable ledger. Mutations
require a configured API key (PIX_PAYMENT_API_KEY); with no key the server stays
up for health/read-only inspection but fails closed on writes.
"""

from __future__ import annotations

import json
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .account import AccountService
from .ledger import Ledger, now_iso, to_minor
from .models import MobileMoneyProvider, Party, PaymentMethod, PaymentRequest
from .payment import PaymentService
from .settlement import SettlementService, verify_webhook
from .wallet import WalletService


def _api_key() -> Optional[str]:
    return os.environ.get("PIX_PAYMENT_API_KEY")


class PaymentAPI:
    def __init__(self) -> None:
        self.ledger = Ledger()
        self.accounts = AccountService(self.ledger)
        self.wallets = WalletService(self.ledger)
        self.payments = PaymentService(self.ledger)
        self.settlements = SettlementService(self.ledger)

    # --- request conversion ----------------------------------------------
    @staticmethod
    def _party(value: Optional[Dict[str, Any]]) -> Optional[Party]:
        if not isinstance(value, dict):
            return None
        return Party(**{k: value.get(k) for k in ("name", "phone", "email", "account", "country", "document")})

    @classmethod
    def _payment_request(cls, body: Dict[str, Any]) -> PaymentRequest:
        method = body.get("method", PaymentMethod.MOBILE_MONEY.value)
        provider = body.get("provider")
        return PaymentRequest(
            amount=body.get("amount"),
            currency=str(body.get("currency", "")).upper(),
            method=PaymentMethod(method) if method else PaymentMethod.MOBILE_MONEY,
            provider=MobileMoneyProvider(provider) if provider else None,
            payer=cls._party(body.get("payer")),
            payee=cls._party(body.get("payee")),
            reference=body.get("reference"),
            description=body.get("description"),
            callback_url=body.get("callback_url"),
            country=body.get("country"),
            metadata=body.get("metadata") or {},
        )


class Handler(BaseHTTPRequestHandler):
    api: PaymentAPI

    protocol_version = "HTTP/1.1"
    server_version = "PixPaymentLive/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # structured responses below; silence default stderr access log

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _authorize(self) -> bool:
        key = _api_key()
        if not key:
            self._send(503, {"ok": False, "error": "mutations disabled: PIX_PAYMENT_API_KEY is not configured"})
            return False
        supplied = self.headers.get("Authorization", "")
        if supplied != f"Bearer {key}":
            self._send(401, {"ok": False, "error": "unauthorized"})
            return False
        return True

    # --- routing ----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path.rstrip("/") or "/", parse_qs(parsed.query)
        api = self.api
        try:
            if path == "/health":
                return self._send(200, {
                    "ok": True,
                    "service": "pix-payment",
                    "db": str(api.ledger.path),
                    "rail_configured": bool(api.payments.service.gateway.base_url),
                    "mutations_enabled": bool(_api_key()),
                })
            if path == "/accounts":
                return self._send(200, {"ok": True, "accounts": api.accounts.list()})
            if path.startswith("/accounts/"):
                account = api.accounts.get(unquote(path.split("/")[-1]))
                return self._send(200, {"ok": True, "account": account}) if account else self._send(404, {"ok": False, "error": "account not found"})
            if path == "/wallets":
                account_id = (query.get("account_id") or [None])[0]
                return self._send(200, {"ok": True, "wallets": api.wallets.list(account_id)})
            if path.startswith("/wallets/"):
                parts = path.split("/")
                wallet_id = unquote(parts[2]) if len(parts) > 2 else ""
                if len(parts) > 3 and parts[3] == "balance":
                    return self._send(200, {"ok": True, "balance": api.wallets.balance(wallet_id)})
                if len(parts) > 3 and parts[3] == "entries":
                    return self._send(200, {"ok": True, "entries": api.wallets.entries(wallet_id)})
                wallet = api.wallets.get(wallet_id)
                return self._send(200, {"ok": True, "wallet": wallet}) if wallet else self._send(404, {"ok": False, "error": "wallet not found"})
            if path == "/payments":
                return self._send(200, {"ok": True, "payments": api.payments.list()})
            if path.startswith("/payments/reference/"):
                record = api.payments.get_by_reference(unquote(path.split("/")[-1]))
                return self._send(200, {"ok": True, "payment": record}) if record else self._send(404, {"ok": False, "error": "payment not found"})
            if path.startswith("/payments/"):
                record = api.payments.get(unquote(path.split("/")[-1]))
                return self._send(200, {"ok": True, "payment": record}) if record else self._send(404, {"ok": False, "error": "payment not found"})
            return self._send(404, {"ok": False, "error": "not found"})
        except (ValueError, KeyError) as exc:
            return self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        api = self.api
        try:
            if path == "/webhooks" or path.startswith("/webhooks/"):
                provider = unquote(path.split("/")[2]) if path.startswith("/webhooks/") else "default"
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                signature = self.headers.get("X-Payment-Signature")
                if not verify_webhook(raw, signature):
                    return self._send(401, {"ok": False, "error": "invalid webhook signature"})
                event_id = uuid.uuid4().hex
                with api.ledger.transaction() as conn:
                    conn.execute(
                        "INSERT INTO webhook_events (id, provider, signature_valid, body, received_at) VALUES (?, ?, 1, ?, ?)",
                        (event_id, provider, raw.decode("utf-8", errors="replace"), now_iso()),
                    )
                return self._send(200, {"ok": True, "webhook_event_id": event_id})

            if not self._authorize():
                return
            body = self._body()

            if path == "/accounts":
                return self._send(201, {"ok": True, "account": api.accounts.create(
                    name=body.get("name"), phone=body.get("phone"), email=body.get("email"),
                    document=body.get("document"), country=body.get("country"), account_id=body.get("id"),
                )})
            if path == "/wallets":
                wallet = api.wallets.create(str(body.get("account_id") or ""), str(body.get("currency") or ""))
                return self._send(201, {"ok": True, "wallet": wallet})
            if path in ("/payments/collect", "/payments/disburse", "/payments/transfer"):
                action = path.split("/")[-1]
                request = api._payment_request(body)
                authorized_by = body.get("authorized_by") or "api-client"
                result = getattr(api.payments, action)(request, authorized_by=authorized_by)
                return self._send(201, {"ok": True, "payment": result})
            if path.startswith("/payments/") and path.endswith("/refresh-status"):
                reference = unquote(path.split("/")[2])
                return self._send(200, {"ok": True, "payment": api.payments.refresh_status(reference)})
            if path == "/settlements":
                result = api.settlements.settle(
                    str(body.get("payment_id") or ""),
                    str(body.get("authorized_by") or ""),
                    wallet_id=body.get("wallet_id"),
                    evidence=body.get("evidence"),
                )
                return self._send(201, {"ok": True, "settlement": result})
            return self._send(404, {"ok": False, "error": "not found"})
        except (ValueError, KeyError, TypeError) as exc:
            return self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})


def serve(host: Optional[str] = None, port: Optional[int] = None) -> None:
    Handler.api = PaymentAPI()
    host = host or os.environ.get("PAYMENT_API_HOST", "127.0.0.1")
    port = port or int(os.environ.get("PAYMENT_API_PORT", "8790"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"pix-payment live API on http://{host}:{port} (db={Handler.api.ledger.path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
