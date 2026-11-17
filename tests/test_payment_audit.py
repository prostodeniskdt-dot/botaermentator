"""Synthetic provider responses; diagnostics must never grant or expose checkout PII."""

import copy
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.bot.routers import admin
from app.config import Settings
from app.services.payment_audit import audit_provider, audit_report, capture_audit_input
from app.services.payment_service import parse_order, shape
from app.services.tilda_payload import normalize_tilda_payload
from tests.test_paid_credits import payment_settings  # noqa: F401

# ruff: noqa: F811


def payload():
    return {
        "Email": "private@example.com",
        "Name": "Private Name",
        "tranid": "lead-123",
        "payment[systranid]": "payment-123",
        "payment[orderid]": "order-123",
        "payment[amount]": "2.00",
        "payment[currency]": "RUB",
        "payment[products][0][externalid]": "credits-50",
        "payment[products][0][quantity]": "1",
        "payment[products][0][price]": "2.00",
        "payment[products][0][amount]": "2.00",
        "payment[products][0][name]": "private-product-name",
        "authorization": "private-secret",
    }


def provider():
    return {
        "id": "payment-123",
        "status": "succeeded",
        "paid": True,
        "test": False,
        "recipient": {"account_id": "shop"},
        "amount": {"value": "2.00", "currency": "RUB"},
        "metadata": {"order_id": "order-123", "email": "private@example.com"},
        "description": "Order order-123. private@example.com",
    }


def settings():
    return Settings(_env_file=None, yookassa_shop_id="shop", yookassa_secret_key="private-secret")


def test_capture_is_restricted_and_does_not_mutate_payload():
    data = payload()
    original = copy.deepcopy(data)
    captured = capture_audit_input(data)
    assert data == original
    assert captured["payment_id"] == "payment-123"
    assert captured["order_id"] == "order-123"
    assert captured["amount_kopecks"] == 200
    assert captured["products"][0]["externalid"] == "credits-50"
    for private in [
        "private@example.com",
        "Private Name",
        "private-secret",
        "private-product-name",
    ]:
        assert private not in str(captured)
    flat = normalize_tilda_payload(data)
    nested_copy = copy.deepcopy(flat)
    assert capture_audit_input(flat) == captured
    assert flat == nested_copy


@pytest.mark.parametrize(
    "data",
    [
        {"payment": {}, "payment[systranid]": "x"},
        {"payment[products][1][quantity]": "1"},
        {"payment[products][1000000][quantity]": "1"},
        {"payment[products]": "x", "payment[products][0][quantity]": "1"},
    ],
)
def test_ambiguous_or_sparse_payload_is_blocked(data):
    assert "capture_error" in capture_audit_input(data)
    with pytest.raises(ValueError):
        normalize_tilda_payload(data)


def test_flat_products_are_accepted_by_audited_parser(payment_settings):
    payment_settings.payments_contract["sku"] = "externalid"
    parsed = parse_order(payload(), payment_settings.payments_contract)
    assert parsed["sku"] == "credits-50"
    assert parsed["amount"] == 200
    assert parsed["email"] == "private@example.com"
    payment_settings.payments_contract["payment_id"] = "payment[systranid]"
    assert parse_order(payload(), payment_settings.payments_contract) == parsed


def test_stringified_product_array():
    data = {"payment[products]": json.dumps([{"externalid": "credits-50", "quantity": "1"}])}
    assert capture_audit_input(data)["products"][0]["externalid"] == "credits-50"


def test_shape_keeps_only_known_flat_field_names():
    value = shape(payload())
    assert value["payment[products][0][externalid]"] == "str"
    assert "authorization" not in value
    assert "private@example.com" not in str(value)


async def test_read_only_provider_audit():
    candidate = capture_audit_input(payload())
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json=provider())
    report = await audit_provider(candidate, settings(), client)
    assert report["provider_id_and_shop_match"] is True
    assert report["succeeded_and_paid"] is True
    assert report["amount_matches_tilda"] is True
    assert report["exact_order_metadata_matches"] == ["metadata.order_id"]
    assert report["activation_allowed"] is False
    rendered = "\n".join(audit_report(uuid.uuid4(), candidate, report))
    for private in ["private@example.com", "private-secret", "payment-123", "order-123"]:
        assert private not in rendered
    client.post.assert_not_awaited()
    assert client.get.call_args.args[0] == "https://api.yookassa.ru/v3/payments/payment-123"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipient", {"account_id": "other-shop"}),
        ("id", "unrelated-payment"),
        ("status", "canceled"),
        ("paid", False),
        ("test", True),
        ("amount", {"value": "99.00", "currency": "USD"}),
        ("metadata", {"order_id": "other-order"}),
    ],
)
async def test_wrong_provider_evidence_never_activates(field, value):
    data = provider()
    data[field] = value
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json=data)
    report = await audit_provider(capture_audit_input(payload()), settings(), client)
    assert report.get("activation_allowed") is not True
    if field in {"recipient", "id"}:
        assert report["error"] == "provider_identity_mismatch"
    elif field == "metadata":
        assert report["exact_order_metadata_matches"] == []


async def test_missing_id_and_credentials_do_not_call_provider():
    client = AsyncMock()
    assert (await audit_provider({}, settings(), client))["error"] == "payment_id_not_captured"
    assert (await audit_provider({"payment_id": "payment-123"}, Settings(_env_file=None), client))[
        "error"
    ] == "yookassa_credentials_missing"
    client.get.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
async def test_provider_errors_are_safe(status):
    client = AsyncMock()
    client.get.return_value = httpx.Response(status, text="private-secret")
    report = await audit_provider(capture_audit_input(payload()), settings(), client)
    assert report == {"error": "yookassa_http_" + str(status)}


async def test_provider_timeout_is_safe():
    client = AsyncMock()
    client.get.side_effect = httpx.ConnectTimeout("private-secret")
    assert (await audit_provider(capture_audit_input(payload()), settings(), client))[
        "error"
    ] == "provider_temporarily_unavailable"


@pytest.mark.parametrize(("user_id", "chat"), [(7, "private"), (42, "group")])
async def test_nonadmin_never_reads_storage(monkeypatch, user_id, chat):
    def denied():
        pytest.fail("Must not access storage")

    monkeypatch.setattr(admin, "session_scope", denied)
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(type=chat),
        answer=AsyncMock(),
        text="/admin_payment_audit " + str(uuid.uuid4()),
    )
    await admin.admin_payment_audit(msg, SimpleNamespace(admin_user_ids=[42]))
    msg.answer.assert_not_awaited()


async def test_old_record_requests_redelivery_without_provider_io(monkeypatch):
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(details={"shape": {"Email": "str"}})

    @asynccontextmanager
    async def scope():
        yield db

    monkeypatch.setattr(admin, "session_scope", scope)
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(type="private"),
        answer=AsyncMock(),
        text="/admin_payment_audit " + str(uuid.uuid4()),
    )
    await admin.admin_payment_audit(msg, SimpleNamespace(admin_user_ids=[42]))
    assert "старой записи" in msg.answer.call_args.args[0]
    db.execute.assert_not_awaited()
