"""Minimal EfiPay client — Python variant of the Java EfiPay class."""

import json
import os
import urllib.request


class ELException(Exception):
    def __init__(self, error=None, error_description=None):
        super().__init__(error or error_description)
        self.error = error
        self.error_description = error_description


class EfiPay:
    def __init__(self, options):
        self.options = options

    def call(self, endpoint, params=None, body=None):
        base = (
            self.options.get("base_url")
            or os.environ.get("PIX_API_BASE_URL")
            or ""
        ).rstrip("/")
        url = f"{base}/{endpoint}"
        payload = dict(body or {})
        payload.update(params or {})
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
