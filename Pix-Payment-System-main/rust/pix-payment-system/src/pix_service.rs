//! PIX payment system — Rust variant of PixService.java.

use crate::credentials::Credentials;
use crate::efi_client::EfiPay;
use crate::random_string::generate_random_string;
use crate::DEFAULT_CREDENTIALS_PATH;
use serde_json::{json, Value};

fn info_adicionais() -> Value {
    json!([
        {"nome": "Campo 1", "valor": "Informação Adicional1 do PSP-Recebedor"},
        {"nome": "Campo 2", "valor": "Informação Adicional2 do PSP-Recebedor"},
    ])
}

pub struct PixService {
    pub client_id: Option<String>,
    pub client_secret: Option<String>,
    pub credentials: Credentials,
}

impl PixService {
    pub fn new(
        client_id: Option<String>,
        client_secret: Option<String>,
        credentials: Credentials,
    ) -> Self {
        PixService {
            client_id,
            client_secret,
            credentials,
        }
    }

    pub fn from_credentials_file(path: &str) -> Result<Self, String> {
        Ok(PixService::new(None, None, Credentials::from_file(path)?))
    }

    /// Loads credentials from the payment-system resources file, like the Java app.
    pub fn from_default_credentials() -> Result<Self, String> {
        Self::from_credentials_file(DEFAULT_CREDENTIALS_PATH)
    }

    fn options(&self) -> Value {
        json!({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "certificate": self.credentials.certificate(),
            "sandbox": self.credentials.sandbox(),
        })
    }

    pub fn pix_create_evp(&self) -> Result<Value, String> {
        EfiPay::new(self.options()).call("pixCreateEvp", json!({}), json!({}))
    }

    pub fn pix_create_charge(&self, chave: &str, valor: f64) -> Result<Value, String> {
        let body = json!({
            "calendario": {"expiracao": 3600},
            "devedor": {"cpf": "12345678909", "nome": "Francisco da Silva"},
            "valor": {"original": valor},
            "chave": chave,
            "infoAdicionais": info_adicionais(),
        });
        let response = EfiPay::new(self.options())
            .call("pixCreateImmediateCharge", json!({}), body)?;
        if let Some(id) = response.pointer("/loc/id").and_then(Value::as_i64) {
            let _ = self.pix_generate_qr_code(&id.to_string());
        }
        Ok(response)
    }

    fn pix_generate_qr_code(&self, id: &str) -> Result<String, String> {
        let response = EfiPay::new(self.options())
            .call("pixGenerateQRCode", json!({"id": id}), json!({}))?;
        let image = response
            .get("imagemQrcode")
            .and_then(Value::as_str)
            .ok_or_else(|| "missing imagemQrcode in response".to_string())?;
        let b64 = image.split(',').last().unwrap_or(image);
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(b64)
            .map_err(|e| e.to_string())?;
        std::fs::write("qrCodeImage.png", bytes).map_err(|e| e.to_string())?;
        Ok("qrCodeImage.png".to_string())
    }
}
