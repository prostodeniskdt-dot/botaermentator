"""Versioned catalog for the moved Tilda page; provider responses are synthetic."""

import copy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.payment_audit import audit_provider, capture_audit_input
from app.services.payment_service import parse_order, verify_payment
from tests.test_confirmed_payment_catalog import setup

CASES = [
    ("PbhJaRz1P31ctv9EIgXm", 3, "180"),
    ("EnvzUoDtB8uyPYfXBt5p", 10, "500"),
    ("XCEUGyv0icslp5eph439", 50, "2000"),
]


def settings(profile="tilda_live_2026_11_19"):
    return Settings(
        _env_file=None,
        payments_profile=profile,
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
    )


@pytest.mark.parametrize(("code", "credits", "price"), CASES)
def test_current_catalog(code, credits, price):
    data, _, provider = setup(code, price)
    provider["amount"]["value"] = price
    config = settings()
    assert config.payments_enabled is False
    parsed = parse_order(data, config.payments_contract)
    assert config.payments_packages[code]["amount_kopecks"] == int(price) * 100
    assert (
        verify_payment(SimpleNamespace(details=parsed, payment_id="payment-123"), provider, config)
        == credits
    )


@pytest.mark.parametrize(
    ("profile", "credits", "price"),
    [("tilda_test_2026_11_17", 10, "1"), ("tilda_live_2026_11_18", 1, "50")],
)
def test_old_snapshots_under_current_profile(profile, credits, price):
    data, _, provider = setup(amount=price)
    provider["amount"]["value"] = price
    old = settings(profile)
    parsed = parse_order(data, old.payments_contract)
    parsed["package_snapshot"] = copy.deepcopy(old.payments_packages[parsed["sku"]])
    parsed["provider_order_path"] = "metadata.tilda_orderid"
    assert (
        verify_payment(
            SimpleNamespace(details=parsed, payment_id="payment-123"), provider, settings()
        )
        == credits
    )


def test_current_snapshot_after_rollback():
    data, _, provider = setup(amount="180")
    provider["amount"]["value"] = "180"
    config = settings()
    parsed = parse_order(data, config.payments_contract)
    parsed["package_snapshot"] = copy.deepcopy(config.payments_packages[parsed["sku"]])
    parsed["provider_order_path"] = "metadata.tilda_orderid"
    assert (
        verify_payment(
            SimpleNamespace(details=parsed, payment_id="payment-123"),
            provider,
            settings("tilda_live_2026_11_18"),
        )
        == 3
    )


@pytest.mark.parametrize("price", ["1", "50", "2600"])
def test_incorrect_prices_are_blocked(price):
    data, _, provider = setup(amount=price)
    provider["amount"]["value"] = price
    config = settings()
    with pytest.raises(ValueError):
        verify_payment(
            SimpleNamespace(
                details=parse_order(data, config.payments_contract), payment_id="payment-123"
            ),
            provider,
            config,
        )


async def test_audit_validates_three_credits_without_activation():
    from unittest.mock import AsyncMock

    import httpx

    data, _, provider = setup(amount="180")
    provider["amount"]["value"] = "180"
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json=provider)
    result = await audit_provider(capture_audit_input(data), settings(), client)
    assert result["configured_package_valid"] is True
    assert result["verified_credits"] == 3
    assert result["activation_allowed"] is False
