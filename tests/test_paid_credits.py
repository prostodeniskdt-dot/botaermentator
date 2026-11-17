"""Synthetic contract only: these names are NOT an observed Tilda schema."""

import copy
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.payment_service import parse_order, shape, verify_payment


@pytest.fixture
def payment_settings():
    return Settings(
        _env_file=None,
        payments_enabled=True,
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
        payments_contract={
            "audit_reference": "SYNTHETIC_TEST_ONLY",
            "payment_id": "payment.systranid",
            "order_id": "payment.orderid",
            "lead_id": "tranid",
            "email": "Email",
            "products": "payment.products",
            "quantity": "quantity",
            "sku": "sku",
            "amount": "payment.amount",
            "currency": "payment.currency",
            "provider_order_id": "metadata.order_id",
        },
        payments_packages={
            str(n): {
                "credits": n,
                "amount_kopecks": n * 100,
                "audit_reference": "SYNTHETIC_TEST_ONLY",
            }
            for n in [10, 50, 100]
        },
    )


def payload(n=50, payment="pay-1", email="Buyer@example.com"):
    return {
        "Email": email,
        "tranid": "lead-1",
        "payment": {
            "systranid": payment,
            "orderid": "order-" + payment,
            "amount": str(n),
            "currency": "RUB",
            "products": [{"sku": str(n), "quantity": 1}],
        },
    }


def provider(n=50, payment="pay-1"):
    return {
        "id": payment,
        "status": "succeeded",
        "paid": True,
        "test": False,
        "recipient": {"account_id": "shop"},
        "amount": {"value": str(n), "currency": "RUB"},
        "metadata": {"order_id": "order-" + payment},
    }


@pytest.mark.parametrize("n", [10, 50, 100])
def test_packages(payment_settings, n):
    parsed = parse_order(payload(n), payment_settings.payments_contract)
    order = SimpleNamespace(details=parsed, payment_id="pay-1")
    assert verify_payment(order, provider(n), payment_settings) == n


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", "canceled"),
        ("status", "pending"),
        ("status", "waiting_for_capture"),
        ("paid", False),
        ("test", True),
        ("test", None),
        ("id", "other"),
        ("recipient", {"account_id": "other"}),
        ("amount", {"value": "1", "currency": "RUB"}),
        ("amount", {"value": "50", "currency": "USD"}),
        ("metadata", {"order_id": "unrelated"}),
        ("refunded_amount", {"value": "1.00", "currency": "RUB"}),
    ],
)
def test_provider_rejection(payment_settings, key, value):
    p = provider()
    p[key] = value
    with pytest.raises(ValueError):
        verify_payment(
            SimpleNamespace(
                details=parse_order(payload(), payment_settings.payments_contract),
                payment_id="pay-1",
            ),
            p,
            payment_settings,
        )


def test_schema_requires_audit():
    with pytest.raises(ValueError):
        parse_order(payload(), {})


@pytest.mark.parametrize("change", ["no_id", "quantity", "cart"])
def test_bad_cart(payment_settings, change):
    data = copy.deepcopy(payload())
    if change == "no_id":
        del data["payment"]["systranid"]
    elif change == "quantity":
        data["payment"]["products"][0]["quantity"] = 2
    else:
        data["payment"]["products"] *= 2
    with pytest.raises((KeyError, ValueError)):
        parse_order(data, payment_settings.payments_contract)


def test_diagnostic_contains_no_values():
    data = {"Email": "secret@example.com", "secret@example.com": "secret-token"}
    assert "secret" not in str(shape(data))


@pytest.mark.parametrize("email_key", ["Email", "email"])
def test_audited_email_key(payment_settings, email_key):
    p = payload()
    p[email_key] = p.pop("Email")
    payment_settings.payments_contract["email"] = email_key
    assert parse_order(p, payment_settings.payments_contract)["email"] == "buyer@example.com"
