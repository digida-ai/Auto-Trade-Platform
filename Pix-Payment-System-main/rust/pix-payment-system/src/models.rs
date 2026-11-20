//! Payment models adapted for African use cases.
//!
//! Generalizes PIX-only flows to mobile money, bank transfers and other rails
//! with multi-currency and phone/MSISDN-first identifiers.

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PaymentMethod {
    Pix,
    MobileMoney,
    BankTransfer,
    Card,
    Ussd,
    Cash,
}

impl PaymentMethod {
    pub fn as_str(&self) -> &'static str {
        match self {
            PaymentMethod::Pix => "pix",
            PaymentMethod::MobileMoney => "mobile_money",
            PaymentMethod::BankTransfer => "bank_transfer",
            PaymentMethod::Card => "card",
            PaymentMethod::Ussd => "ussd",
            PaymentMethod::Cash => "cash",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MobileMoneyProvider {
    Mpesa,
    MtnMomo,
    AirtelMoney,
    OrangeMoney,
    Wave,
    TigoPesa,
    VodacomMpesa,
    MoovMoney,
}

impl MobileMoneyProvider {
    pub fn as_str(&self) -> &'static str {
        match self {
            MobileMoneyProvider::Mpesa => "mpesa",
            MobileMoneyProvider::MtnMomo => "mtn_momo",
            MobileMoneyProvider::AirtelMoney => "airtel_money",
            MobileMoneyProvider::OrangeMoney => "orange_money",
            MobileMoneyProvider::Wave => "wave",
            MobileMoneyProvider::TigoPesa => "tigo_pesa",
            MobileMoneyProvider::VodacomMpesa => "vodacom_mpesa",
            MobileMoneyProvider::MoovMoney => "moov_money",
        }
    }
}

/// Common African currency codes (ISO 4217).
pub fn african_currencies() -> HashMap<&'static str, &'static str> {
    HashMap::from([
        ("KES", "Kenya"),
        ("UGX", "Uganda"),
        ("TZS", "Tanzania"),
        ("RWF", "Rwanda"),
        ("NGN", "Nigeria"),
        ("GHS", "Ghana"),
        ("ZAR", "South Africa"),
        ("ZMW", "Zambia"),
        ("MWK", "Malawi"),
        ("ETB", "Ethiopia"),
        ("XOF", "West African CFA"),
        ("XAF", "Central African CFA"),
        ("MZN", "Mozambique"),
        ("BWP", "Botswana"),
        ("EGP", "Egypt"),
        ("MAD", "Morocco"),
        ("CDF", "DR Congo"),
    ])
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Party {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// MSISDN / phone number, primary identifier for mobile money.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phone: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub email: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub account: Option<String>,
    /// ISO 3166-1 alpha-2 country code (e.g. KE, UG, NG).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub country: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub document: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaymentRequest {
    pub amount: f64,
    pub currency: String,
    pub method: PaymentMethod,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<MobileMoneyProvider>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payer: Option<Party>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub payee: Option<Party>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reference: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub callback_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub country: Option<String>,
    #[serde(default, skip_serializing_if = "Map::is_empty")]
    pub metadata: Map<String, Value>,
}

impl PaymentRequest {
    pub fn mobile_money_collect(
        amount: f64,
        currency: impl Into<String>,
        phone: impl Into<String>,
        provider: MobileMoneyProvider,
        country: Option<String>,
        name: Option<String>,
    ) -> Self {
        let c = country.clone();
        PaymentRequest {
            amount,
            currency: currency.into(),
            method: PaymentMethod::MobileMoney,
            provider: Some(provider),
            payer: Some(Party {
                name,
                phone: Some(phone.into()),
                country: c.clone(),
                ..Default::default()
            }),
            payee: None,
            reference: None,
            description: None,
            callback_url: None,
            country: c,
            metadata: Map::new(),
        }
    }

    pub fn to_payload(&self) -> Value {
        serde_json::to_value(self).unwrap_or_else(|_| json!({}))
    }
}
