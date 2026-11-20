"""PIX payment system — Python variant of PixService.java."""

import base64

from .credentials import Credentials
from .efi_client import EfiPay

INFO_ADICIONAIS = [
    {"nome": "Campo 1", "valor": "Informação Adicional1 do PSP-Recebedor"},
    {"nome": "Campo 2", "valor": "Informação Adicional2 do PSP-Recebedor"},
]


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

    def pix_create_charge(self, chave, valor):
        body = {
            "calendario": {"expiracao": 3600},
            "devedor": {"cpf": "12345678909", "nome": "Francisco da Silva"},
            "valor": {"original": valor},
            "chave": chave,
            "infoAdicionais": INFO_ADICIONAIS,
        }
        efi = EfiPay(self._options())
        response = efi.call("pixCreateImmediateCharge", {}, body)
        loc_id = response.get("loc", {}).get("id")
        if loc_id is not None:
            self.pix_generate_qr_code(str(loc_id))
        return response

    def pix_generate_qr_code(self, id):
        efi = EfiPay(self._options())
        response = efi.call("pixGenerateQRCode", {"id": id}, {})
        imagem = response.get("imagemQrcode", "").split(",")[-1]
        with open("qrCodeImage.png", "wb") as f:
            f.write(base64.b64decode(imagem))
        return "qrCodeImage.png"
