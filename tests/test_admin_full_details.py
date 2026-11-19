"""Private admin reports reveal requested email and selected order fields, never raw secrets."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.routers import admin
from app.services.admin_payment_report import admin_email_report, admin_order_report


def user():
    return SimpleNamespace(
        telegram_user_id=7,
        email="Person@Example.com",
        email_normalized="person@example.com",
        registration_email="person@example.com",
        email_registered_by=42,
        email_registered_at=None,
    )


def order():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="credited",
        error_code=None,
        payment_id="payment-123",
        order_id="order-123",
        lead_id=None,
        email_normalized="person@example.com",
        recipient_id=7,
        provider_checked_at=None,
        credited_at=None,
        credit_transaction_id=uuid.uuid4(),
        details={
            "sku": "code",
            "amount": 18000,
            "currency": "RUB",
            "package_snapshot": {"credits": 3, "catalog_version": "version"},
            "secret": "never-show-secret",
            "provider_raw": {"card_number": "never-show-card"},
        },
    )


def test_full_email_fields():
    text = admin_email_report(user())
    assert "person@example.com" in text
    assert "Person@Example.com" in text
    assert "42" in text
    assert "***" not in text


def test_order_report_is_allowlisted():
    text = admin_order_report(order())
    for value in ["person@example.com", "payment-123", "order-123", "18000", "credited", "version"]:
        assert value in text
    assert "never-show" not in text


@pytest.mark.parametrize(
    ("handler", "uid", "chat"),
    [
        ("admin_email", 7, "private"),
        ("admin_email", 42, "group"),
        ("admin_user", 7, "private"),
        ("admin_user", 42, "group"),
        ("admin_payment", 7, "private"),
        ("admin_payment", 42, "group"),
    ],
)
async def test_private_admin_required(monkeypatch, handler, uid, chat):
    def forbidden():
        pytest.fail("Unauthorized storage access")

    monkeypatch.setattr(admin, "session_scope", forbidden)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=uid),
        chat=SimpleNamespace(type=chat),
        text="/admin_email 7",
        answer=AsyncMock(),
    )
    await getattr(admin, handler)(message, SimpleNamespace(admin_user_ids=[42]))
    message.answer.assert_not_awaited()


async def test_admin_email_command_returns_full_address(monkeypatch):
    repo = AsyncMock()
    repo.get_user_by_telegram_id.return_value = user()

    @asynccontextmanager
    async def scope():
        yield AsyncMock()

    monkeypatch.setattr(admin, "session_scope", scope)
    monkeypatch.setattr(admin, "Repository", lambda db: repo)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(type="private"),
        text="/admin_email 7",
        answer=AsyncMock(),
    )
    await admin.admin_email(message, SimpleNamespace(admin_user_ids=[42]))
    assert "person@example.com" in message.answer.call_args.args[0]
    assert message.answer.call_args.kwargs["parse_mode"] is None


async def test_payment_status_command_shows_selected_full_details(monkeypatch):
    item = order()
    db = AsyncMock()
    db.get.return_value = item

    @asynccontextmanager
    async def scope():
        yield db

    monkeypatch.setattr(admin, "session_scope", scope)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(type="private"),
        text="/admin_payment_status " + str(item.id),
        answer=AsyncMock(),
    )
    await admin.admin_payment(message, SimpleNamespace(admin_user_ids=[42]))
    text = message.answer.call_args.args[0]
    assert "person@example.com" in text
    assert "payment-123" in text
    assert "never-show" not in text
    assert item.status == "credited"
    db.execute.assert_not_awaited()
    assert message.answer.call_args.kwargs["parse_mode"] is None


async def test_email_without_id_has_precise_usage(monkeypatch):
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(type="private"),
        text="/admin_set_email person@example.com",
        answer=AsyncMock(),
    )
    await admin.admin_email(message, SimpleNamespace(admin_user_ids=[42]))
    assert "сначала Telegram ID" in message.answer.call_args.args[0]


def test_registration_time_is_shown_in_moscow_time():
    from datetime import UTC, datetime

    item = user()
    item.email_registered_at = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
    assert "19.11.2026 12:00 МСК" in admin_email_report(item)
