"""Configurable HTTP payment gateway for African payment providers.

Real mobile-money/bank integrations vary by provider and aggregator
(Flutterwave, Paystack, M-Pesa Daraja, MTN MoMo API, Airtel, etc.). This
gateway provides a thin, configurable HTTP client: set base_url and per-action
endpoints so the same service can target different providers/environments.
"""

import json
import os
import urllib.request
from typing import Any, Dict, Optional


class PaymentGatewayError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class PaymentGateway:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        endpoints: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ):
        self.base_url = (base_url or os.environ.get("PAYMENT_API_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("PAYMENT_API_KEY")
        self.headers = headers or {}
        # Map logical actions to provider endpoint paths, e.g.
        # {"collect": "/collection", "disburse": "/disbursement", "status": "/status/{id}"}
        self.endpoints = endpoints or {}
        self.timeout = timeout

    def _resolve(self, action: str) -> str:
        path = self.endpoints.get(action, f"/{action}")
        return f"{self.base_url}{path}"

    def request(self, action: str, payload: Dict[str, Any], method: str = "POST") -> Dict[str, Any]:
        url = self._resolve(action)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req_headers = {"Content-Type": "application/json", **self.headers}
        if self.api_key and "Authorization" not in req_headers:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status", None) or getattr(e, "code", None)
            raise PaymentGatewayError(str(e), status=status) from e
