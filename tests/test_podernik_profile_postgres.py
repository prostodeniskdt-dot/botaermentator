"""Control payment received while disabled can be retried once after activation."""

import asyncio
import os
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models.entities import CreditAccount, CreditTransaction, PaymentNotice, PurchaseOrder
from app.services.payment_email import set_email
from app.services.payment_service import process_order, receive_order, retry_order
from tests.test_confirmed_payment_catalog import setup
from tests.test_podernik_profile import CASES, settings
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")


@pytest.mark.parametrize(("code", "credits", "price"), CASES)
async def test_control_order_requires_one_payment_only(db_factory, code, credits, price):
    await add_user(db_factory, balance=100)
    async with db_factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)
    config = settings()
    data, _, provider = setup(code, price)
    provider["amount"]["value"] = price
    async with db_factory.begin() as db:
        order = await receive_order(db, data, config, "event")
        order_id = order.id
        assert order.status == "manual_review"
        assert order.error_code == "payments_disabled"
        assert order.recipient_id == 7
    config.payments_enabled = True
    async with db_factory.begin() as db:
        await retry_order(db, order_id, config)

    async def duplicate():
        async with db_factory.begin() as db:
            order = await receive_order(db, data, config, "event")
            if order.status == "pending_verification":
                client = AsyncMock()
                client.get.return_value = httpx.Response(200, json=provider)
                await process_order(db, order, config, client)

    await asyncio.gather(*(duplicate() for _ in range(5)))
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 100 + credits
        assert (await db.get(PurchaseOrder, order_id)).status == "credited"
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 1
        assert await db.scalar(select(func.count()).select_from(PaymentNotice)) == 1
