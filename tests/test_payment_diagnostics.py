"""Read-only admin diagnostics must not disclose stored checkout values."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.routers import admin
from app.services.payment_diagnostics import payment_schema_report


def order(details):
    return SimpleNamespace(id=uuid.uuid4(), details=details)


def test_saved_shape_preserves_field_names_and_types():
    report = "\n".join(
        payment_schema_report(
            order(
                {
                    "shape": {
                        "Email": "str",
                        "payment": {"systranid": "str", "products": [{"quantity": "int"}]},
                        "key_012345abcdef": "str",
                    }
                }
            )
        )
    )
    for value in ["Email", "systranid", "quantity", "int", "key_012345abcdef"]:
        assert value in report


def test_malformed_shape_cannot_leak_values_or_keys():
    report = "\n".join(
        payment_schema_report(
            order(
                {
                    "shape": {
                        "Email": "private@example.com",
                        "payment": {"systranid": "secret-token"},
                        "key@example.com": "password-value",
                    }
                }
            )
        )
    )
    for value in ["private@example.com", "secret-token", "key@example.com", "password-value"]:
        assert value not in report


def test_parsed_record_does_not_expose_values():
    report = "\n".join(
        payment_schema_report(
            order(
                {
                    "email": "private@example.com",
                    "payment_id": "secret-payment",
                    "recipient_id": 123456789,
                }
            )
        )
    )
    for value in ["private@example.com", "secret-payment", "123456789"]:
        assert value not in report
    assert "нормализованной" in report


def test_long_report_fits_telegram_limits():
    reports = payment_schema_report(order({"shape": {f"field_{n}": "str" for n in range(4000)}}))
    assert 1 < len(reports) <= 9
    assert all(len(report) <= 3000 for report in reports)
    assert "обрезана" in reports[-1]


def message(text, user=42, chat="private"):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user),
        chat=SimpleNamespace(type=chat),
        answer=AsyncMock(),
    )


@pytest.mark.parametrize(("user", "chat"), [(7, "private"), (42, "group")])
async def test_unauthorized_never_queries_storage(monkeypatch, user, chat):
    def forbidden():
        pytest.fail("Unauthorized access to database")

    monkeypatch.setattr(admin, "session_scope", forbidden)
    msg = message("/admin_payment_schema " + str(uuid.uuid4()), user=user, chat=chat)
    await admin.admin_payment_schema(msg, SimpleNamespace(admin_user_ids=[42]))
    msg.answer.assert_not_awaited()


@pytest.mark.parametrize(
    "text",
    [
        "/admin_payment_schema",
        "/admin_payment_schema bad",
        "/admin_payment_schema " + str(uuid.uuid4()) + " extra",
    ],
)
async def test_invalid_arguments(monkeypatch, text):
    msg = message(text)
    await admin.admin_payment_schema(msg, SimpleNamespace(admin_user_ids=[42]))
    assert "Использование" in msg.answer.call_args.args[0]


@pytest.mark.parametrize("found", [True, False])
async def test_command_reads_one_order_and_does_not_write(monkeypatch, found):
    item = order({"shape": {"Email": "str", "tranid": "str"}})
    db = AsyncMock()
    db.get.return_value = item if found else None

    @asynccontextmanager
    async def scope():
        yield db

    monkeypatch.setattr(admin, "session_scope", scope)
    msg = message("/admin_payment_schema@mybot " + str(item.id))
    await admin.admin_payment_schema(msg, SimpleNamespace(admin_user_ids=[42]))
    db.get.assert_awaited_once_with(admin_schema_model(), item.id)
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    text = msg.answer.call_args.args[0]
    assert ("Email" in text) if found else ("не найден" in text)
    assert msg.answer.call_args.kwargs["parse_mode"] is None


def admin_schema_model():
    from app.db.models.entities import PurchaseOrder

    return PurchaseOrder
