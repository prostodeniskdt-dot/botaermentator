"""Final owner-approved package quantities with real ledger/idempotency and mocked YooKassa."""

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
    ("code", "credits", "price"),
    [
        ("PbhJaRz1P31ctv9EIgXm", 1, "50"),
        ("EnvzUoDtB8uyPYfXBt5p", 5, "250"),
        ("XCEUGyv0icslp5eph439", 10, "350"),
    ],
)
async def test_final_package_is_credited_once(db_factory, code, credits, price):
    await add_user(db_factory, balance=100)
    async with db_factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)
    data, _, provider = setup(code, price)
    provider["amount"]["value"] = price
    settings = Settings(
        _env_file=None,
        payments_enabled=True,
        payments_profile="tilda_live_2026_11_18",
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
    )

    async def deliver():
        async with db_factory.begin() as db:
            order = await receive_order(db, data, settings, "same-body")
            if order.status == "pending_verification":
                client = AsyncMock()
                client.get.return_value = httpx.Response(200, json=provider)
                await process_order(db, order, settings, client)

    await asyncio.gather(*(deliver() for _ in range(5)))
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 100 + credits
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 1
        assert await db.scalar(select(func.count()).select_from(PaymentNotice)) == 1


@pytest.mark.parametrize("wrong", ["old_price", "old_credits", "wrong_order"])
async def test_wrong_final_data_does_not_grant(db_factory, wrong):
    await add_user(db_factory, balance=100)
    async with db_factory.begin() as db:
        await set_email(db, 7, "buyer@example.com", 42)
    data, _, provider = setup(amount="1" if wrong == "old_price" else "50")
    provider["amount"]["value"] = "1" if wrong == "old_price" else "50"
    if wrong == "wrong_order":
        provider["metadata"]["tilda_orderid"] = "other"
    settings = Settings(
        _env_file=None,
        payments_enabled=True,
        payments_profile="tilda_live_2026_11_18",
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
    )
    async with db_factory.begin() as db:
        order = await receive_order(db, data, settings, "event")
        if wrong == "old_credits":
            order.details = {
                **order.details,
                "package_snapshot": {**order.details["package_snapshot"], "credits": 10},
            }
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, json=provider)
        await process_order(db, order, settings, client)
        assert order.status == "manual_review"
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 100
        assert await db.scalar(select(func.count()).select_from(CreditTransaction)) == 0
