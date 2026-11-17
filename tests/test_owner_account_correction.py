"""Regression for the owner-confirmed ID, including already applied revision 011."""

import importlib
import os

import pytest
from sqlalchemy import select, text

from app.db.models.entities import CreditAccount, CreditTransaction, TelegramUser
from app.services.payment_email import set_email
from tests.test_admin_balance_migration import apply as apply_previous
from tests.test_postgres_workflows import add_user, db_factory  # noqa: F401

# ruff: noqa: F811
pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires PostgreSQL")
OWNER = 1221087257
WRONG = 392172803


async def apply(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("migrations.versions.012_correct_owner_account")

    def run(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    connection = await db.connection()
    await connection.run_sync(run)


@pytest.mark.parametrize("previous_applied", [False, True])
async def test_correct_owner_and_reverse_mistake(db_factory, previous_applied):
    await add_user(db_factory, user_id=OWNER, balance=17, unlimited=True)
    await add_user(db_factory, user_id=WRONG, balance=5, unlimited=True)
    async with db_factory.begin() as db:
        if previous_applied:
            await apply_previous(db)
        await apply(db)
    async with db_factory() as db:
        user = await db.scalar(select(TelegramUser).where(TelegramUser.telegram_user_id == OWNER))
        wrong = await db.scalar(select(TelegramUser).where(TelegramUser.telegram_user_id == WRONG))
        assert user.email_normalized == "prostodeniskdt@gmail.com"
        assert user.is_unlimited is False
        assert wrong.email_normalized is None
        assert wrong.is_unlimited is True
        accounts = {
            a.telegram_user_id: a.balance for a in (await db.scalars(select(CreditAccount)))
        }
        assert accounts == {OWNER: 100, WRONG: 5}


async def test_reapply_preserves_spending(db_factory):
    await add_user(db_factory, user_id=OWNER, balance=5, unlimited=True)
    async with db_factory.begin() as db:
        await apply(db)
    async with db_factory.begin() as db:
        await db.execute(text("UPDATE credit_accounts SET balance=96"))
        await apply(db)
    async with db_factory() as db:
        assert (await db.scalar(select(CreditAccount))).balance == 96
        assert len((await db.scalars(select(CreditTransaction))).all()) == 1


async def test_unrelated_email_owner_is_not_overwritten(db_factory):
    await add_user(db_factory, user_id=OWNER, balance=5, unlimited=True)
    await add_user(db_factory, user_id=8)
    async with db_factory.begin() as db:
        await set_email(db, 8, "prostodeniskdt@gmail.com", 8)
    with pytest.raises(RuntimeError, match="unrelated"):
        async with db_factory.begin() as db:
            await apply(db)
    async with db_factory() as db:
        user = await db.scalar(select(TelegramUser).where(TelegramUser.telegram_user_id == OWNER))
        assert user.is_unlimited is True
        assert user.email_normalized is None


async def test_owner_is_charged_even_while_admin(db_factory):
    import uuid

    from app.config import Settings
    from app.db.repositories import Repository
    from app.services.credit_service import CreditService

    await add_user(db_factory, user_id=OWNER, balance=5, unlimited=True)
    settings = Settings(_env_file=None, admin_user_ids=[OWNER])
    credits = CreditService(settings)
    async with db_factory.begin() as db:
        await apply(db)
    request = uuid.uuid4()
    async with db_factory.begin() as db:
        repo = Repository(db)
        amount = await credits.reserve(repo, OWNER, request, "quick")
        assert amount == settings.quick_mode_credits
        await credits.commit(repo, OWNER, request, amount)
    async with db_factory() as db:
        account = await db.scalar(select(CreditAccount))
        assert account.balance == 100 - settings.quick_mode_credits
        assert account.reserved == 0


async def test_balance_shows_number_for_limited_admin(db_factory):
    from unittest.mock import AsyncMock, MagicMock

    from app.bot.routers.private_messages import private_balance
    from app.config import Settings

    await add_user(db_factory, user_id=OWNER, balance=5, unlimited=True)
    async with db_factory.begin() as db:
        await apply(db)
    message = MagicMock()
    message.from_user.id = OWNER
    message.answer = AsyncMock()
    await private_balance(
        message, Settings(_env_file=None, admin_user_ids=[OWNER]), AsyncMock(), AsyncMock()
    )
    text = message.answer.call_args.args[0]
    assert "100" in text
    assert "безлимит" not in text.lower()


async def test_older_orders_keep_their_recipient(db_factory):

    from app.db.models.entities import PurchaseOrder

    await add_user(db_factory, user_id=OWNER, balance=5, unlimited=True)
    await add_user(db_factory, user_id=WRONG, balance=5, unlimited=True)
    async with db_factory.begin() as db:
        await apply_previous(db)
        order = PurchaseOrder(
            event_key="historical",
            email_normalized="prostodeniskdt@gmail.com",
            recipient_id=WRONG,
            status="pending_verification",
            details={},
        )
        db.add(order)
        await db.flush()
        order_id = order.id
        await apply(db)
    async with db_factory() as db:
        order = await db.get(PurchaseOrder, order_id)
        assert order.recipient_id == WRONG
        assert order.status == "manual_review"
        assert order.error_code == "owner_identity_corrected"
