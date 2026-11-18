"""Catalog validation is tied to the order snapshot, not whichever profile is active."""

import copy

from app.services.payment_catalog import TILDA_TEST_PACKAGES, validate_catalog_package


def test_versioned_catalog_rejects_changed_price_or_credits():
    code = "PbhJaRz1P31ctv9EIgXm"
    package = {**TILDA_TEST_PACKAGES[code], "catalog_version": "tilda_test_2026_11_17"}
    assert validate_catalog_package(code, package)
    for field, value in [
        ("credits", 1),
        ("amount_kopecks", 5000),
        ("currency", "USD"),
        ("catalog_version", "unknown"),
    ]:
        changed = copy.deepcopy(package)
        changed[field] = value
        assert not validate_catalog_package(code, changed)


def test_legacy_credit_snapshot_is_preserved():
    assert validate_catalog_package("PbhJaRz1P31ctv9EIgXm", {"credits": 10})
    assert not validate_catalog_package("PbhJaRz1P31ctv9EIgXm", {"credits": 1})


def test_live_profile_uses_confirmed_counts_and_prices_without_activation():
    from app.config import Settings

    settings = Settings(_env_file=None, payments_profile="tilda_live_2026_11_18")
    assert settings.payments_enabled is False
    assert {
        code: (p["credits"], p["amount_kopecks"]) for code, p in settings.payments_packages.items()
    } == {
        "PbhJaRz1P31ctv9EIgXm": (1, 5000),
        "EnvzUoDtB8uyPYfXBt5p": (5, 25000),
        "XCEUGyv0icslp5eph439": (10, 35000),
    }


def test_old_snapshot_keeps_original_credits_under_live_settings():
    from types import SimpleNamespace

    from app.config import Settings
    from app.services.payment_service import parse_order, verify_payment
    from tests.test_confirmed_payment_catalog import setup

    data, old_settings, provider = setup()
    old_settings = Settings(_env_file=None, payments_profile="tilda_test_2026_11_17")
    parsed = parse_order(data, old_settings.payments_contract)
    parsed["package_snapshot"] = copy.deepcopy(old_settings.payments_packages[parsed["sku"]])
    parsed["provider_order_path"] = "metadata.tilda_orderid"
    current = Settings(
        _env_file=None, payments_profile="tilda_live_2026_11_18", yookassa_shop_id="shop"
    )
    assert (
        verify_payment(SimpleNamespace(details=parsed, payment_id="payment-123"), provider, current)
        == 10
    )


def test_live_snapshot_keeps_credits_when_switching_back_to_test():
    from types import SimpleNamespace

    from app.config import Settings
    from app.services.payment_service import parse_order, verify_payment
    from tests.test_confirmed_payment_catalog import setup

    data, _, provider = setup(amount="50")
    provider["amount"]["value"] = "50"
    live = Settings(_env_file=None, payments_profile="tilda_live_2026_11_18")
    parsed = parse_order(data, live.payments_contract)
    parsed["package_snapshot"] = copy.deepcopy(live.payments_packages[parsed["sku"]])
    parsed["provider_order_path"] = "metadata.tilda_orderid"
    old = Settings(
        _env_file=None, payments_profile="tilda_test_2026_11_17", yookassa_shop_id="shop"
    )
    assert (
        verify_payment(SimpleNamespace(details=parsed, payment_id="payment-123"), provider, old)
        == 1
    )
