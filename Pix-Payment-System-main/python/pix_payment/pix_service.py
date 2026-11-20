"""PIX payment system — Python variant of PixService.java.

No sample debtors or fixed output paths: the payer and QR destination are
supplied by the caller, and missing data fails closed.
"""

import base64
from pathlib import Path
from typing import Any, Dict, Optional

from .credentials import Credentials
from .efi_client import EfiPay


class PixService:
    """Mirrors the Java PixService: pixCreateEvp, pixCreateCharge, pixGenerateQRCode."""

    def __init__(self, client_id=None, client_secret=None, credentials=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.credentials = credentials or Credentials()

    def _options(self):
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "certificate": self.credentials.certificate,
            "sandbox": self.credentials.sandbox,
        }

    def pix_create_evp(self):
        efi = EfiPay(self._options())
        return efi.call("pixCreateEvp", {}, {})

    def pix_create_charge(
        self,
        chave: str,
        valor: Any,
        devedor: Dict[str, Any],
        info_adicionais: Optional[list] = None,
        expiracao: int = 3600,
    ):
        """Create an immediate charge. `devedor` must be a real payer identity."""
        if not chave or not devedor:
            raise ValueError("pix_create_charge requires chave and devedor")
        body = {
            "calendario": {"expiracao": expiracao},
            "devedor": devedor,
            "valor": {"original": valor},
            "chave": chave,
        }
        if info_adicionais:
            body["infoAdicionais"] = info_adicionais
        efi = EfiPay(self._options())
        response = efi.call("pixCreateImmediateCharge", {}, body)
        loc_id = response.get("loc", {}).get("id")
        return {"charge": response, "loc_id": str(loc_id) if loc_id is not None else None}

    def pix_generate_qr_code(self, id: str, output_path: Optional[str] = None) -> str:
        """Fetch and persist a QR image; the destination must be provided."""
        if not id:
            raise ValueError("pix_generate_qr_code requires a location id")
        if not output_path:
            raise ValueError("pix_generate_qr_code requires output_path (no fixed artifact)")
        efi = EfiPay(self._options())
        response = efi.call("pixGenerateQRCode", {"id": id}, {})
        imagem = (response.get("imagemQrcode") or "").split(",")[-1]
        if not imagem:
            raise ValueError("provider returned no QR image")
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(imagem))
        return str(path)
