//! Configurable HTTP payment gateway for African payment providers.
//!
//! Provider integrations differ (M-Pesa Daraja, MTN MoMo, Airtel, aggregators,
//! etc.). This gateway maps logical actions (collect/disburse/transfer/status)
//! to configurable endpoints on a base URL.

use serde_json::Value;
use std::collections::HashMap;
use std::env;

#[derive(Debug, Clone, Default)]
pub struct PaymentGateway {
    pub base_url: String,
    pub api_key: Option<String>,
    pub headers: HashMap<String, String>,
    pub endpoints: HashMap<String, String>,
}

impl PaymentGateway {
    pub fn new(base_url: impl Into<String>) -> Self {
        PaymentGateway {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            ..Default::default()
        }
    }

    pub fn from_env() -> Self {
        PaymentGateway::new(env::var("PAYMENT_API_BASE_URL").unwrap_or_default())
            .with_api_key(env::var("PAYMENT_API_KEY").ok())
    }

    pub fn with_api_key(mut self, key: Option<String>) -> Self {
        self.api_key = key;
        self
    }

    pub fn with_endpoint(mut self, action: &str, path: &str) -> Self {
        self.endpoints.insert(action.to_string(), path.to_string());
        self
    }

    fn resolve(&self, action: &str) -> String {
        let path = self
            .endpoints
            .get(action)
            .cloned()
            .unwrap_or_else(|| format!("/{action}"));
        format!("{}{}", self.base_url, path)
    }

    pub fn request(&self, action: &str, payload: &Value) -> Result<Value, String> {
        let url = self.resolve(action);
        let client = reqwest::blocking::Client::new();
        let mut req = client.post(&url).json(payload);
        if let Some(key) = &self.api_key {
            req = req.bearer_auth(key);
        }
        for (k, v) in &self.headers {
            req = req.header(k, v);
        }
        let resp = req.send().map_err(|e| e.to_string())?;
        resp.json::<Value>().map_err(|e| e.to_string())
    }
}
