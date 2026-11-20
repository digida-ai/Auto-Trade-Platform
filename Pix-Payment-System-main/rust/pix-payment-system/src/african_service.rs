//! Payment service adapted for African use cases.
//!
//! Mobile money collect (request-to-pay), disbursement, transfer and status,
//! using a configurable gateway. PIX flows remain available via PixService.

use crate::gateway::PaymentGateway;
use crate::models::{PaymentMethod, PaymentRequest};
use crate::random_string::generate_random_string;
use serde_json::Value;

pub struct AfricanPaymentService {
    pub gateway: PaymentGateway,
}

impl AfricanPaymentService {
    pub fn new(gateway: PaymentGateway) -> Self {
        AfricanPaymentService { gateway }
    }

    pub fn from_env() -> Self {
        AfricanPaymentService::new(PaymentGateway::from_env())
    }

    /// Request payment from a customer (mobile money collect / request-to-pay).
    pub fn collect(&self, mut request: PaymentRequest) -> Result<Value, String> {
        if request.method == PaymentMethod::MobileMoney {
            let has_phone = request
                .payer
                .as_ref()
                .and_then(|p| p.phone.as_ref())
                .map(|s| !s.is_empty())
                .unwrap_or(false);
            if !has_phone {
                return Err("Mobile money collect requires payer.phone (MSISDN)".to_string());
            }
        }
        if request.reference.is_none() {
            request.reference = Some(generate_random_string(12));
        }
        self.gateway.request("collect", &request.to_payload())
    }

    /// Send money to a recipient (payout/disbursement).
    pub fn disburse(&self, mut request: PaymentRequest) -> Result<Value, String> {
        let has_dest = request
            .payee
            .as_ref()
            .map(|p| {
                p.phone.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
                    || p.account.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
            })
            .unwrap_or(false);
        if !has_dest {
            return Err("Disbursement requires payee.phone or payee.account".to_string());
        }
        if request.reference.is_none() {
            request.reference = Some(generate_random_string(12));
        }
        self.gateway.request("disburse", &request.to_payload())
    }

    /// Bank or wallet transfer.
    pub fn transfer(&self, mut request: PaymentRequest) -> Result<Value, String> {
        if request.reference.is_none() {
            request.reference = Some(generate_random_string(12));
        }
        self.gateway.request("transfer", &request.to_payload())
    }

    /// Check transaction/payment status by reference.
    pub fn status(&self, reference: &str) -> Result<Value, String> {
        let payload = serde_json::json!({ "reference": reference });
        self.gateway.request("status", &payload)
    }
}
