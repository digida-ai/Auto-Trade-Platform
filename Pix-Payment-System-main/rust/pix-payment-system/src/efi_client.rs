//! Minimal EfiPay client — Rust variant of the Java EfiPay class.

use serde_json::Value;
use std::env;

pub struct EfiPay {
    pub options: Value,
}

impl EfiPay {
    pub fn new(options: Value) -> Self {
        EfiPay { options }
    }

    pub fn call(&self, endpoint: &str, params: Value, body: Value) -> Result<Value, String> {
        let base = match self.options.get("base_url").and_then(Value::as_str) {
            Some(b) => b.to_string(),
            None => env::var("PIX_API_BASE_URL").unwrap_or_default(),
        };
        let url = format!("{}/{}", base.trim_end_matches('/'), endpoint);

        let mut payload = body;
        if let (Some(obj), Some(p)) = (payload.as_object_mut(), params.as_object()) {
            for (k, v) in p {
                obj.insert(k.clone(), v.clone());
            }
        }

        let client = reqwest::blocking::Client::new();
        let response = client
            .post(&url)
            .json(&payload)
            .send()
            .map_err(|e| e.to_string())?;
        response.json().map_err(|e| e.to_string())
    }
}
