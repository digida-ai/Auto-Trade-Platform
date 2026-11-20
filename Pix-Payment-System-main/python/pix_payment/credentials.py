"""Credentials for the PIX payment system — Python variant.

Loads certificate / sandbox / debug flags, as the Java
``br.com.jujubaprojects.paymentsystem.Pix.Credentials`` class does.
"""

import json
from pathlib import Path

# The Java app keeps credentials.json in payment-system/src/main/resources.
DEFAULT_CREDENTIALS_PATH = (
    Path(__file__).resolve().parents[2]
    / "payment-system"
    / "src"
    / "main"
    / "resources"
    / "credentials.json"
)


class Credentials:
    def __init__(self, path=DEFAULT_CREDENTIALS_PATH):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.certificate = data.get("certificate")
        self.sandbox = bool(data.get("sandbox"))
        self.debug = bool(data.get("debug"))

    def get_certificate(self):
        return self.certificate

    def is_sandbox(self):
        return self.sandbox

    def is_debug(self):
        return self.debug
