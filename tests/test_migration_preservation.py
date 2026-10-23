"""Exercise real Alembic upgrade/downgrade against synthetic version-005 data."""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Requires isolated TEST_DATABASE_URL with CREATEDB"
)


async def test_upgrade_preserves_existing_dialogue_profile_credits_and_queue():
    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    database = "migration_" + uuid.uuid4().hex
    admin = create_async_engine(base_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{database}"'))
    test_url = base_url.set(database=database)
    engine = create_async_engine(test_url)

    async def migrate(*args):
        env = {
            **os.environ,
            "DATABASE_URL": test_url.render_as_string(hide_password=False),
            "APP_ENV": "development",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            *args,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        assert proc.returncode == 0, (stdout + stderr).decode()

    tables = [
        "telegram_users",
        "chat_sessions",
        "user_questions",
        "bot_responses",
        "credit_accounts",
        "credit_transactions",
        "telegram_update_jobs",
        "telegram_delivery_jobs",
    ]
    columns = {}

    async def snapshot():
        result = {}
        async with engine.connect() as conn:
            for table in tables:
                names = ",".join('"' + column + '"' for column in columns[table])
                rows = await conn.execute(text(f'SELECT {names} FROM "{table}" ORDER BY id'))
                result[table] = rows.all()
        return result

    try:
        await migrate("upgrade", "005_unlimited_access")
        ids = {name: uuid.uuid4() for name in tables}
        request = uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO telegram_users(id,telegram_user_id,access_status,profile_facts) "
                    "VALUES(:id,7,'active',CAST(:profile AS jsonb))"
                ),
                {
                    "id": ids["telegram_users"],
                    "profile": json.dumps({"confirmed": ["Партия 10 л"]}),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO chat_sessions(id,chat_id,telegram_user_id,is_private,summary) "
                    "VALUES(:id,7,7,true,'Сохранённая память')"
                ),
                {"id": ids["chat_sessions"]},
            )
            await conn.execute(
                text(
                    "INSERT INTO user_questions(id,session_id,raw_question,request_id,"
                    "status,credits_charged) "
                    "VALUES(:id,:session,'Существующий вопрос',:request,'answered',5)"
                ),
                {"id": ids["user_questions"], "session": ids["chat_sessions"], "request": request},
            )
            await conn.execute(
                text(
                    "INSERT INTO bot_responses(id,session_id,question_id,response_text,"
                    "telegram_message_id) "
                    "VALUES(:id,:session,:question,'Существующий ответ',42)"
                ),
                {
                    "id": ids["bot_responses"],
                    "session": ids["chat_sessions"],
                    "question": ids["user_questions"],
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO credit_accounts(id,telegram_user_id,balance,reserved) "
                    "VALUES(:id,7,45,0)"
                ),
                {"id": ids["credit_accounts"]},
            )
            await conn.execute(
                text(
                    "INSERT INTO credit_transactions(id,telegram_user_id,request_id,"
                    "transaction_type,amount) "
                    "VALUES(:id,7,:request,'charge',-5)"
                ),
                {"id": ids["credit_transactions"], "request": request},
            )
            await conn.execute(
                text(
                    "INSERT INTO telegram_update_jobs(id,telegram_update_id,payload) "
                    "VALUES(:id,1,CAST(:payload AS jsonb))"
                ),
                {
                    "id": ids["telegram_update_jobs"],
                    "payload": json.dumps({"update_id": 1, "message": {"text": "/balance"}}),
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO telegram_delivery_jobs(id,bot_response_id,question_id,"
                    "is_private,chat_id,text) "
                    "VALUES(:id,:response,:question,true,7,'Существующий ответ')"
                ),
                {
                    "id": ids["telegram_delivery_jobs"],
                    "response": ids["bot_responses"],
                    "question": ids["user_questions"],
                },
            )
            for table in tables:
                rows = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=:name ORDER BY ordinal_position"
                    ),
                    {"name": table},
                )
                columns[table] = list(rows.scalars())
        before = await snapshot()
        await migrate("upgrade", "head")
        assert await snapshot() == before
        async with engine.connect() as conn:
            assert (
                await conn.execute(text("SELECT lane FROM telegram_update_jobs"))
            ).scalar_one() == "service"
            assert (
                await conn.execute(text("SELECT context_snapshot FROM bot_responses"))
            ).scalar_one() == {}
        await migrate("downgrade", "005_unlimited_access")
        assert await snapshot() == before
        await migrate("upgrade", "head")
        assert await snapshot() == before
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin.dispose()
