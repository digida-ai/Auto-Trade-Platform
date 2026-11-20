//! Credentials for the PIX payment system — Rust variant (Credentials.java).

use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Credentials {
    #[serde(default)]
    pub certificate: Option<String>,
    #[serde(default)]
    pub sandbox: Option<bool>,
    #[serde(default)]
    pub debug: Option<bool>,
}

impl Credentials {
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self, String> {
        let raw =
            fs::read_to_string(path).map_err(|e| format!("failed to read credentials: {e}"))?;
        serde_json::from_str(&raw).map_err(|e| format!("failed to parse credentials: {e}"))
    }

    pub fn certificate(&self) -> Option<&str> {
        self.certificate.as_deref()
    }

    pub fn sandbox(&self) -> bool {
        self.sandbox.unwrap_or(false)
    }

    pub fn debug(&self) -> bool {
        self.debug.unwrap_or(false)
    }
}
