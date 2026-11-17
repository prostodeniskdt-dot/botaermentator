"""Owner-approved profile through real ledger transactions with mocked provider API."""

import asyncio
import os
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models.entities import CreditAccount, CreditTransaction, PaymentNotice
from app.services.payment_email import set_email
from app.services.payment_service import process_order, receive_order
from tests.test_confirmed_payment_catalog import setup
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")


@pytest.mark.parametrize(
    ("code", "credits", "amount"),
    [
        ("PbhJaRz1P31ctv9EIgXm", 10, "1"),
        ("EnvzUoDtB8uyPYfXBt5p", 50, "2"),
        ("XCEUGyv0icslp5eph439", 100, "3"),
    ],
)
async def test_profile_grants_once_without_tilda_currency_or_lead(
    db_factory, code, credits, amount
):
    await add_user(db_factory, balance=5)
    async with db_factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)
    data, _, provider = setup(code, amount)
    provider["amount"]["value"] = amount
    settings = Settings(
        _env_file=None,
        payments_enabled=True,
        payments_profile="tilda_test_2026_11_17",
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
    )

    async def deliver():
        async with db_factory.begin() as db:
            order = await receive_order(db, data, settings, "same-event")
            if order.status == "pending_verification":
                client = AsyncMock()
                client.get.return_value = httpx.Response(200, json=provider)
                await process_order(db, order, settings, client)

    await asyncio.gather(*(deliver() for _ in range(5)))
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 5 + credits
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 1
        assert await db.scalar(select(func.count()).select_from(PaymentNotice)) == 1
