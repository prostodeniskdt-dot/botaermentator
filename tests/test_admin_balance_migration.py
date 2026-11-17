"""Apply the actual one-off migration inside an isolated PostgreSQL schema."""

import importlib
import os

import pytest
from sqlalchemy import select, text

from app.db.models.entities import CreditAccount, CreditTransaction, TelegramUser
from app.services.payment_email import set_email
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")


async def apply(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("migrations.versions.011_admin_finite_balance")

    def run(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    connection = await db.connection()
    await connection.run_sync(run)


@pytest.mark.parametrize("balance", [5, 100, 250])
async def test_exact_balance_and_email(db_factory, balance):
    await add_user(db_factory, user_id=392172803, balance=balance, unlimited=True)
    await add_user(db_factory, user_id=8, balance=35, unlimited=True)
    async with db_factory.begin() as db:
        await set_email(db, 392172803, "old@example.com", 392172803)
        await apply(db)
    async with db_factory() as db:
        user = await db.scalar(
            select(TelegramUser).where(TelegramUser.telegram_user_id == 392172803)
        )
        account = await db.scalar(
            select(CreditAccount).where(CreditAccount.telegram_user_id == 392172803)
        )
        other = await db.scalar(select(CreditAccount).where(CreditAccount.telegram_user_id == 8))
        assert user.is_unlimited is False
        assert user.email_normalized == "prostodeniskdt@gmail.com"
        assert account.balance == 100
        assert other.balance == 35
        transaction = await db.scalar(select(CreditTransaction))
        assert transaction.amount == 100 - balance
        assert transaction.balance_after == 100


async def test_reserved_email_aborts(db_factory):
    await add_user(db_factory, user_id=392172803, unlimited=True)
    await add_user(db_factory, user_id=8)
    async with db_factory.begin() as db:
        await set_email(db, 8, "prostodeniskdt@gmail.com", 42)
    with pytest.raises(RuntimeError, match="reserved"):
        async with db_factory.begin() as db:
            await apply(db)
    async with db_factory() as db:
        user = await db.scalar(
            select(TelegramUser).where(TelegramUser.telegram_user_id == 392172803)
        )
        assert user.is_unlimited is True


async def test_missing_user_does_not_create_account(db_factory):
    async with db_factory.begin() as db:
        await apply(db)
        assert await db.scalar(text("SELECT count(*) FROM telegram_users")) == 0


async def test_reapply_does_not_reset_spent_balance(db_factory):
    await add_user(db_factory, user_id=392172803, balance=5, unlimited=True)
    async with db_factory.begin() as db:
        await apply(db)
    async with db_factory.begin() as db:
        await db.execute(
            text("UPDATE credit_accounts SET balance=97 WHERE telegram_user_id=392172803")
        )
        await apply(db)
    async with db_factory() as db:
        account = await db.scalar(select(CreditAccount))
        assert account.balance == 97
        assert await db.scalar(text("SELECT count(*) FROM credit_transactions")) == 1


async def test_large_reservation_rolls_back_everything(db_factory):
    await add_user(db_factory, user_id=392172803, balance=250, unlimited=True)
    async with db_factory.begin() as db:
        await db.execute(text("UPDATE credit_accounts SET reserved=150"))
    with pytest.raises(RuntimeError, match="reservations"):
        async with db_factory.begin() as db:
            await apply(db)
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 250
        assert (await db.scalar(select(TelegramUser))).is_unlimited is True
        assert await db.scalar(text("SELECT count(*) FROM credit_transactions")) == 0
