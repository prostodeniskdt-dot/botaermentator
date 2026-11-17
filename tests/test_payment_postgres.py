# ruff: noqa: F811
"""Real PostgreSQL transactions, concurrent duplicates and email ownership."""

import asyncio
import os
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models.entities import CreditAccount, CreditTransaction, PaymentNotice, PurchaseOrder
from app.services.payment_email import set_email
from app.services.payment_service import process_order, receive_order, retry_order
from tests.test_paid_credits import payload, payment_settings, provider  # noqa: F401
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")


async def prepare(factory):
    await add_user(factory, balance=5)
    async with factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)


def provider_client(n=50, payment="pay-1"):
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json=provider(n, payment))
    return client


@pytest.mark.parametrize("n", [10, 50, 100])
async def test_exactly_one_grant_concurrent(db_factory, payment_settings, n):
    await prepare(db_factory)

    async def handle():
        async with db_factory.begin() as db:
            order = await receive_order(db, payload(n), payment_settings, "event")
            if order.status == "pending_verification":
                await process_order(db, order, payment_settings, provider_client(n))

    await asyncio.gather(*(handle() for _ in range(10)))
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 5 + n
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 1
        assert await db.scalar(select(func.count()).select_from(PaymentNotice)) == 1
        order = await db.scalar(select(PurchaseOrder))
        assert order.status == "credited"
        assert order.credit_transaction_id is not None


async def test_permanent_email_owner(db_factory):
    await prepare(db_factory)
    await add_user(db_factory, user_id=8)
    with pytest.raises(ValueError, match="reserved"):
        async with db_factory.begin() as db:
            await set_email(db, 8, "BUYER@example.com", 42)
    async with db_factory.begin() as db:
        await set_email(db, 7, None, 42)
    with pytest.raises(ValueError, match="reserved"):
        async with db_factory.begin() as db:
            await set_email(db, 8, "buyer@example.com", 42)


async def test_two_orders(db_factory, payment_settings):
    await prepare(db_factory)
    for payment in ["one", "two"]:
        async with db_factory.begin() as db:
            order = await receive_order(db, payload(payment=payment), payment_settings, payment)
            await process_order(db, order, payment_settings, provider_client(payment=payment))
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 105


async def test_provider_timeout_then_recovery(db_factory, payment_settings):
    await prepare(db_factory)
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectTimeout("synthetic")
        await process_order(db, order, payment_settings, client)
        assert order.status == "pending_verification"
        assert order.attempts == 1
    async with db_factory.begin() as db:
        order = await db.scalar(select(PurchaseOrder).with_for_update())
        await process_order(db, order, payment_settings, provider_client())
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 55


async def test_disabled_unknown_and_changed_email(db_factory, payment_settings):
    await prepare(db_factory)
    payment_settings.payments_enabled = False
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
        assert order.status == "manual_review"
        assert order.recipient_id == 7
    payment_settings.payments_enabled = True
    async with db_factory.begin() as db:
        await set_email(db, 7, "new@example.com", 42)
        order = await db.scalar(select(PurchaseOrder).with_for_update())
        await retry_order(db, order.id, payment_settings)
        await process_order(db, order, payment_settings, provider_client())
        assert order.status == "manual_review"
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 5


async def test_unknown_email_never_rebound(db_factory, payment_settings):
    await add_user(db_factory)
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
        assert order.status == "manual_review"
        order_id = order.id
    async with db_factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)
        with pytest.raises(ValueError, match="retry_blocked"):
            await retry_order(db, order_id, payment_settings)


async def test_resolve_unknown_email_is_explicit(db_factory, payment_settings):
    from app.services.payment_service import resolve_recipient

    await add_user(db_factory, balance=5)
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
        order_id = order.id
    async with db_factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)
        order = await resolve_recipient(db, order_id, 7, 42, payment_settings)
        await process_order(db, order, payment_settings, provider_client())
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 55
        assert (await db.get(PurchaseOrder, order_id)).details["resolved_by"] == 42


@pytest.mark.parametrize("status", ["pending", "rejected", "blocked"])
async def test_access_not_changed(db_factory, payment_settings, status):
    from app.db.models.entities import TelegramUser

    await prepare(db_factory)
    async with db_factory.begin() as db:
        user = await db.scalar(select(TelegramUser))
        user.access_status = "active" if status == "blocked" else status
        user.is_blocked = status == "blocked"
        order = await receive_order(db, payload(), payment_settings, "event")
        await process_order(db, order, payment_settings, provider_client())
        assert order.status == "manual_review"
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 5


async def test_notice_failure_does_not_reverse_credit(db_factory, payment_settings, monkeypatch):
    from datetime import UTC, datetime

    from aiogram.exceptions import TelegramForbiddenError

    import app.services.payment_service as module

    await prepare(db_factory)
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
        await process_order(db, order, payment_settings, provider_client())
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("temporary")
    await module.payment_tick(payment_settings, bot)
    async with db_factory.begin() as db:
        notice = await db.scalar(select(PaymentNotice))
        assert notice.status == "pending"
        assert notice.attempts == 1
        notice.next_attempt_at = datetime.now(UTC)
    bot.send_message.side_effect = None
    await module.payment_tick(payment_settings, bot)
    await module.payment_tick(payment_settings, bot)
    assert bot.send_message.await_count == 2
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 55
        assert (await db.scalar(select(PaymentNotice))).status == "sent"
    # Bot-forbidden is terminal, separately from the credit ledger.
    async with db_factory.begin() as db:
        notice = await db.scalar(select(PaymentNotice))
        notice.status = "pending"
        notice.next_attempt_at = datetime.now(UTC)
    bot.send_message.side_effect = TelegramForbiddenError(method=AsyncMock(), message="blocked")
    await module.payment_tick(payment_settings, bot)
    async with db_factory() as db:
        assert (await db.scalar(select(PaymentNotice))).status == "blocked"
        assert (await db.scalar(select(CreditAccount))).balance == 55


@pytest.mark.parametrize("refund", ["1.00", "50.00"])
async def test_refund_never_debits_or_recredits(db_factory, payment_settings, refund):
    import respx

    from app.services.payment_service import reconcile_refund

    await prepare(db_factory)
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
        await process_order(db, order, payment_settings, provider_client())
        order_id = order.id
    refunded = provider()
    refunded["refunded_amount"] = {"value": refund, "currency": "RUB"}
    with respx.mock() as mock:
        mock.get("https://api.yookassa.ru/v3/payments/pay-1").mock(
            return_value=httpx.Response(200, json=refunded)
        )
        async with db_factory.begin() as db:
            order = await db.get(PurchaseOrder, order_id)
            await reconcile_refund(db, order, payment_settings)
            assert order.status == "refunded"
            await retry_order(db, order_id, payment_settings)
            await process_order(db, order, payment_settings, provider_client())
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 55
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 1


async def test_conflicting_replay_does_not_move_recipient(db_factory, payment_settings):
    await prepare(db_factory)
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "event")
    async with db_factory.begin() as db:
        order = await receive_order(
            db, payload(email="other@example.com"), payment_settings, "different-event"
        )
        assert order.error_code == "conflicting_replay"
        assert order.recipient_id == 7
        with pytest.raises(ValueError):
            await retry_order(db, order.id, payment_settings)
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 5


async def test_diagnostic_replay_after_audit(db_factory, payment_settings):
    await prepare(db_factory)
    contract = payment_settings.payments_contract
    payment_settings.payments_contract = {}
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "same-body-hash")
        assert order.payment_id is None
        assert "buyer@example.com" not in str(order.details)
    payment_settings.payments_contract = contract
    async with db_factory.begin() as db:
        order = await receive_order(db, payload(), payment_settings, "same-body-hash")
        await process_order(db, order, payment_settings, provider_client())
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 55
