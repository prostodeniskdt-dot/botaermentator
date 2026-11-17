"""Confirmed field paths/codes; all monetary prices below are synthetic test fixtures."""

import copy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.payment_catalog import CREDITS_BY_EXTERNAL_ID, TILDA_AUDITED_CONTRACT
from app.services.payment_service import parse_order, verify_payment


def setup(code="PbhJaRz1P31ctv9EIgXm", amount="1"):
    data = {
        "Email": "buyer@example.com",
        "payment[systranid]": "payment-123",
        "payment[orderid]": "order-123",
        "payment[amount]": amount,
        "payment[products][0][externalid]": code,
        "payment[products][0][quantity]": "1",
        "payment[products][0][price]": amount,
        "payment[products][0][amount]": amount,
    }
    settings = Settings(
        _env_file=None,
        yookassa_shop_id="shop",
        payments_contract=copy.deepcopy(TILDA_AUDITED_CONTRACT),
        payments_packages={
            code: {
                "credits": CREDITS_BY_EXTERNAL_ID.get(code, 10),
                "amount_kopecks": 100,
                "audit_reference": "SYNTHETIC_PRICE",
            }
        },
    )
    provider = {
        "id": "payment-123",
        "status": "succeeded",
        "paid": True,
        "test": False,
        "recipient": {"account_id": "shop"},
        "amount": {"value": "1.00", "currency": "RUB"},
        "metadata": {"tilda_orderid": "order-123"},
    }
    return data, settings, provider


@pytest.mark.parametrize("code", CREDITS_BY_EXTERNAL_ID)
def test_confirmed_codes_and_missing_lead_currency(code):
    data, settings, provider = setup(code)
    parsed = parse_order(data, settings.payments_contract)
    assert parsed["lead_id"] is None
    assert parsed["currency"] == "RUB"
    order = SimpleNamespace(details=parsed, payment_id=parsed["payment_id"])
    assert verify_payment(order, provider, settings) == CREDITS_BY_EXTERNAL_ID[code]


def test_missing_currency_requires_explicit_policy():
    data, settings, _ = setup()
    del settings.payments_contract["currency_fallback"]
    with pytest.raises(ValueError):
        parse_order(data, settings.payments_contract)


@pytest.mark.parametrize("field", ["currency", "line_price", "line_amount", "sku", "correlation"])
def test_inconsistent_fields_never_validate(field):
    data, settings, provider = setup()
    if field == "currency":
        data["payment[currency]"] = "USD"
    elif field == "line_price":
        data["payment[products][0][price]"] = "0.50"
    elif field == "line_amount":
        data["payment[products][0][amount]"] = "0.50"
    elif field == "sku":
        data["payment[products][0][externalid]"] = "unknown-code"
    else:
        provider["metadata"]["tilda_orderid"] = "other-order"
    order = SimpleNamespace(
        details=parse_order(data, settings.payments_contract), payment_id="payment-123"
    )
    with pytest.raises(ValueError):
        verify_payment(order, provider, settings)


def test_wrong_credit_mapping_is_rejected():
    data, settings, provider = setup()
    settings.payments_packages["PbhJaRz1P31ctv9EIgXm"]["credits"] = 100
    order = SimpleNamespace(
        details=parse_order(data, settings.payments_contract), payment_id="payment-123"
    )
    with pytest.raises(ValueError, match="invalid_package"):
        verify_payment(order, provider, settings)


def test_default_has_no_prices_and_no_activation():
    settings = Settings(_env_file=None)
    assert settings.payments_packages == {}
    assert settings.payments_contract == {}
    assert settings.payments_enabled is False


def test_named_profile_selects_approved_prices_without_enabling():
    settings = Settings(_env_file=None, payments_profile="tilda_test_2026_11_17")
    assert settings.payments_enabled is False
    assert [p["amount_kopecks"] for p in settings.payments_packages.values()] == [100, 200, 300]
    assert settings.payments_contract["provider_order_id"] == "metadata.tilda_orderid"


def test_profile_rejects_conflicting_custom_configuration():
    with pytest.raises(ValueError, match="conflicts"):
        Settings(
            _env_file=None,
            payments_profile="tilda_test_2026_11_17",
            payments_packages={"unknown": {"credits": 999}},
        )


async def test_audit_checks_actual_external_code_against_profile():
    from unittest.mock import AsyncMock

    import httpx

    from app.services.payment_audit import audit_provider, capture_audit_input

    data, _, provider = setup()
    settings = Settings(
        _env_file=None,
        payments_profile="tilda_test_2026_11_17",
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
    )
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json=provider)
    result = await audit_provider(capture_audit_input(data), settings, client)
    assert result["configured_package_valid"] is True
    assert result["verified_credits"] == 10
    assert result["activation_allowed"] is False
    data["payment[products][0][externalid]"] = "wrong-code"
    result = await audit_provider(capture_audit_input(data), settings, client)
    assert result["configured_package_valid"] is False
