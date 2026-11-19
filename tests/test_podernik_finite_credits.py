"""One-off correction is journaled, preserves reservations and identity, and survives replay."""

import importlib
import os
import uuid

import pytest
from sqlalchemy import select, text

from app.config import Settings
from app.db.models.entities import CreditAccount, CreditTransaction, TelegramUser
from app.db.repositories import Repository
from app.services.credit_service import CreditService, has_unlimited_credits
from app.services.payment_email import set_email
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")
TARGET = 392172803


async def apply(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("migrations.versions.014_podernik_finite_credits")

    def run(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    connection = await db.connection()
    await connection.run_sync(run)


@pytest.mark.parametrize("balance", [5, 100, 250])
async def test_finite_100_preserves_other_accounts_and_email(db_factory, balance):
    await add_user(db_factory, user_id=TARGET, balance=balance, unlimited=True)
    await add_user(db_factory, user_id=1221087257, balance=115)
    async with db_factory.begin() as db:
        await set_email(db, TARGET, "podernik@gmail.com", 1221087257)
        await db.execute(
            text("UPDATE credit_accounts SET reserved=2 WHERE telegram_user_id=:id"), {"id": TARGET}
        )
        await apply(db)
    async with db_factory() as db:
        user = await Repository(db).get_user_by_telegram_id(TARGET)
        account = await db.scalar(
            select(CreditAccount).where(CreditAccount.telegram_user_id == TARGET)
        )
        other = await db.scalar(
            select(CreditAccount).where(CreditAccount.telegram_user_id == 1221087257)
        )
        assert user.is_unlimited is False
        assert user.admin_credits_limited is True
        assert user.email_normalized == "podernik@gmail.com"
        assert user.access_status == "active"
        assert (account.balance, account.reserved) == (100, 2)
        assert other.balance == 115
        tx = await db.scalar(select(CreditTransaction))
        assert tx.amount == 100 - balance
        assert tx.balance_after == 100


async def test_admin_is_charged_and_reapply_keeps_spending(db_factory):
    await add_user(db_factory, user_id=TARGET, balance=10, unlimited=True)
    settings = Settings(_env_file=None, admin_user_ids=[TARGET])
    service = CreditService(settings)
    async with db_factory.begin() as db:
        await apply(db)
        user = await Repository(db).get_user_by_telegram_id(TARGET)
        assert not has_unlimited_credits(settings, TARGET, user)
        request = uuid.uuid4()
        amount = await service.reserve(Repository(db), TARGET, request, "quick")
        assert amount == settings.quick_mode_credits
        await service.commit(Repository(db), TARGET, request, amount)
    async with db_factory.begin() as db:
        await apply(db)
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 100 - settings.quick_mode_credits
        assert (
            len(
                (
                    await db.scalars(
                        select(CreditTransaction).where(
                            CreditTransaction.transaction_type == "adjustment"
                        )
                    )
                ).all()
            )
            == 1
        )


async def test_large_reservation_rolls_back(db_factory):
    await add_user(db_factory, user_id=TARGET, balance=200, unlimited=True)
    async with db_factory.begin() as db:
        await db.execute(text("UPDATE credit_accounts SET reserved=150"))
    with pytest.raises(RuntimeError, match="reservations"):
        async with db_factory.begin() as db:
            await apply(db)
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 200
        assert (await db.scalar(select(TelegramUser))).is_unlimited is True
        assert (await db.scalars(select(CreditTransaction))).all() == []


async def test_missing_user_is_not_created(db_factory):
    async with db_factory.begin() as db:
        await apply(db)
        assert (await db.scalars(select(TelegramUser))).all() == []
