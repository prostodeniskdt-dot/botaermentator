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


# Final package counts, prices and code mapping explicitly confirmed by the owner.
TILDA_LIVE_PACKAGES = {
    "PbhJaRz1P31ctv9EIgXm": {"credits": 1, "amount_kopecks": 5000},
    "EnvzUoDtB8uyPYfXBt5p": {"credits": 5, "amount_kopecks": 25000},
    "XCEUGyv0icslp5eph439": {"credits": 10, "amount_kopecks": 35000},
}
TILDA_LIVE_PACKAGES = {
    code: {
        **package,
        "currency": "RUB",
        "catalog_version": "tilda_live_2026_11_18",
        "audit_reference": "owner-confirmed-final-packages:2026-11-18",
    }
    for code, package in TILDA_LIVE_PACKAGES.items()
}

# Snapshot versions resolve against their original catalog, never the active price profile.
PAYMENT_CATALOGS = {
    "tilda_test_2026_11_17": TILDA_TEST_PACKAGES,
    "tilda_live_2026_11_18": TILDA_LIVE_PACKAGES,
}


def validate_catalog_package(code: str, package: dict) -> bool:
    version = package.get("catalog_version")
    if version is None:
        # Legacy snapshots created before catalogs were versioned retain their credit mapping.
        expected = CREDITS_BY_EXTERNAL_ID.get(code)
        return expected is None or package.get("credits") == expected
    expected = PAYMENT_CATALOGS.get(version, {}).get(code)
    return expected is not None and all(
        package.get(key) == expected.get(key) for key in ("credits", "amount_kopecks", "currency")
    )
