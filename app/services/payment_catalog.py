"""Owner-confirmed external codes; prices require separate explicit configuration."""

CREDITS_BY_EXTERNAL_ID = {
    "PbhJaRz1P31ctv9EIgXm": 10,
    "EnvzUoDtB8uyPYfXBt5p": 50,
    "XCEUGyv0icslp5eph439": 100,
}

TILDA_AUDITED_CONTRACT = {
    "audit_reference": "live-audit:d061c9f3-4492-4664-ad89-05cc5b2b56e5",
    "email": "Email",
    "payment_id": "payment.systranid",
    "order_id": "payment.orderid",
    "lead_id": None,
    "products": "payment.products",
    "sku": "externalid",
    "quantity": "quantity",
    "amount": "payment.amount",
    "line_price": "price",
    "line_amount": "amount",
    "currency": "payment.currency",
    "currency_fallback": "RUB",
    "provider_order_id": "metadata.tilda_orderid",
}

# Explicitly approved by the owner in this session. Never a production price default.
TILDA_TEST_PACKAGES = {
    code: {
        "credits": credits,
        "amount_kopecks": price,
        "currency": "RUB",
        "audit_reference": "owner-approved-test-prices:2026-11-17",
    }
    for (code, credits), price in zip(CREDITS_BY_EXTERNAL_ID.items(), (100, 200, 300), strict=True)
}
