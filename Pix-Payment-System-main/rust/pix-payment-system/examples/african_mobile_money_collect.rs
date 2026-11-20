//! Example: mobile money collect (request-to-pay) for African use cases.
//!
//! Run (from rust/pix-payment-system):
//!   cargo run --example african_mobile_money_collect
//!
//! Configure PAYMENT_API_BASE_URL / PAYMENT_API_KEY and endpoint mapping to
//! match your provider (M-Pesa, MTN MoMo, Airtel, aggregator, etc.).

use pix_payment_system::{
    AfricanPaymentService, MobileMoneyProvider, Party, PaymentGateway, PaymentMethod, PaymentRequest,
};
use serde_json::Map;

fn mpesa_collect_example() -> PaymentRequest {
    let gateway = PaymentGateway::new(
        std::env::var("PAYMENT_API_BASE_URL")
            .unwrap_or_else(|_| "https://api.sandbox.provider.example".to_string()),
    )
    .with_api_key(Some(
        std::env::var("PAYMENT_API_KEY").unwrap_or_else(|_| "your-api-key".to_string()),
    ))
    .with_endpoint("collect", "/mpesa/collect")
    .with_endpoint("disburse", "/mpesa/disburse")
    .with_endpoint("transfer", "/transfers")
    .with_endpoint("status", "/transactions/status");

    let _service = AfricanPaymentService::new(gateway);

    let mut req = PaymentRequest::mobile_money_collect(
        1500.00,
        "KES",
        "254712345678",
        MobileMoneyProvider::Mpesa,
        Some("KE".to_string()),
        Some("Jane Wanjiku".to_string()),
    );
    req.reference = Some("ORDER-KE-1001".to_string());
    req.description = Some("Order payment".to_string());
    req.callback_url = Some("https://merchant.example/webhooks/payment".to_string());
    req.metadata.insert(
        "order_id".to_string(),
        serde_json::Value::String("1001".to_string()),
    );

    // Sends a request-to-pay prompt to the customer's phone:
    // let response = _service.collect(req.clone());
    // println!("{response:?}");
    req
}

fn mtn_momo_collect_example() -> PaymentRequest {
    let gateway = PaymentGateway::new(
        std::env::var("MOMO_API_BASE_URL")
            .unwrap_or_else(|_| "https://sandbox.momodeveloper.mtn.com".to_string()),
    )
    .with_api_key(std::env::var("MOMO_API_KEY").ok())
    .with_endpoint("collect", "/collection/v1_0/requesttopay")
    .with_endpoint("disburse", "/disbursement/v1_0/transfer")
    .with_endpoint("status", "/collection/v1_0/requesttopay/{reference}");

    let _service = AfricanPaymentService::new(gateway);

    let req = PaymentRequest {
        amount: 50_000.0,
        currency: "UGX".to_string(),
        method: PaymentMethod::MobileMoney,
        provider: Some(MobileMoneyProvider::MtnMomo),
        payer: Some(Party {
            name: Some("Kato Musa".to_string()),
            phone: Some("256771234567".to_string()),
            country: Some("UG".to_string()),
            email: None,
            account: None,
            document: None,
        }),
        payee: None,
        reference: Some("ORDER-UG-2001".to_string()),
        description: Some("Wallet top-up".to_string()),
        callback_url: Some("https://merchant.example/webhooks/momo".to_string()),
        country: Some("UG".to_string()),
        metadata: Map::new(),
    };

    // let response = _service.collect(req.clone());
    // let status = _service.status("ORDER-UG-2001");
    req
}

fn main() {
    let mpesa = mpesa_collect_example();
    let momo = mtn_momo_collect_example();
    println!("M-Pesa collect payload: {}", mpesa.to_payload());
    println!("MTN MoMo collect payload: {}", momo.to_payload());
}
