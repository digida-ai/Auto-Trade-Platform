//! Payment system — Rust variant, adapted for African use cases.
//!
//! Includes original PIX flows (PixService, Efi client, Credentials) and a
//! generalized layer for African rails: mobile money (M-Pesa, MTN MoMo, Airtel
//! Money, Orange Money, Wave, etc.), bank transfers, multi-currency, and
//! phone/MSISDN-first payments via a configurable gateway.

pub mod african_service;
pub mod credentials;
pub mod efi_client;
pub mod gateway;
pub mod models;
pub mod pix_service;
pub mod random_string;

pub use crate::african_service::AfricanPaymentService;
pub use crate::credentials::Credentials;
pub use crate::gateway::PaymentGateway;
pub use crate::models::{
    african_currencies, MobileMoneyProvider, Party, PaymentMethod, PaymentRequest,
};
pub use crate::pix_service::PixService;

/// Default location of credentials.json — the payment-system resources file.
pub const DEFAULT_CREDENTIALS_PATH: &str =
    "../../payment-system/src/main/resources/credentials.json";
