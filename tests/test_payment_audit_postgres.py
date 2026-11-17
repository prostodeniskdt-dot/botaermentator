"""Audit capture/replay and Telegram audit cannot change balances or credit orders."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.bot.routers import admin
from app.config import Settings
from app.db.models.entities import CreditAccount, CreditTransaction, PurchaseOrder
from app.services.payment_service import receive_order
from tests.test_payment_audit import payload, provider
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")


async def test_capture_is_auditable_with_payments_disabled(db_factory):
    await add_user(db_factory, user_id=42, balance=100)
    settings = Settings(
        _env_file=None,
        admin_user_ids=[42],
        payments_enabled=False,
        yookassa_shop_id="shop",
        yookassa_secret_key="private-secret",
    )
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), settings, "event")
        order_id = order.id
        assert order.status == "manual_review"
        assert order.payment_id is None
        assert order.details["audit_input"]["payment_id"] == "payment-123"
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(type="private"),
        answer=AsyncMock(),
        text="/admin_payment_audit " + str(order_id),
    )
    with respx.mock() as mock:
        mock.get("https://api.yookassa.ru/v3/payments/payment-123").mock(
            return_value=httpx.Response(200, json=provider())
        )
        await admin.admin_payment_audit(msg, settings)
        assert mock.calls.call_count == 1
    async with db_factory() as db:
        order = await db.get(PurchaseOrder, order_id)
        assert order.status == "manual_review"
        assert order.payment_id is None
        assert order.credited_at is None
        assert order.details["audit_result"]["succeeded_and_paid"] is True
        assert order.details["audit_result"]["activation_allowed"] is False
        assert (await db.scalar(select(CreditAccount))).balance == 100
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 0
    text = "\n".join(call.args[0] for call in msg.answer.call_args_list)
    assert "private@example.com" not in text
    assert "private-secret" not in text


async def test_identical_redelivery_fills_old_structure_only_record(db_factory):
    settings = Settings(_env_file=None, payments_enabled=False)
    async with db_factory.begin() as db:
        order = PurchaseOrder(
            event_key="old-body-hash",
            details={"shape": {"Email": "str"}},
            status="manual_review",
            error_code="schema_or_fields_unverified",
        )
        db.add(order)
        await db.flush()
        order_id = order.id
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), settings, "old-body-hash")
        assert order.id == order_id
        assert order.status == "manual_review"
        assert order.details["audit_input"]["payment_id"] == "payment-123"
    async with db_factory() as db:
        assert await db.scalar(select(func.count()).select_from(PurchaseOrder)) == 1
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 0
