"""A user lease survives short transactions and fences stale workers."""

import asyncio
import uuid
from contextlib import asynccontextmanager, suppress

from app.db.repositories import Repository
from app.db.session import session_scope


@asynccontextmanager
async def processing_lease(repo: Repository, user_id: int, request_id, *, seconds: int):
    owner = uuid.uuid4()
    acquired = await repo.acquire_processing_lease(user_id, owner, request_id, seconds)
    await repo.session.commit()
    if not acquired:
        yield False
        return
    repo.processing_lease = (user_id, owner)
    task = asyncio.current_task()

    async def heartbeat():
        while True:
            await asyncio.sleep(seconds / 3)
            try:
                async with session_scope() as db:
                    renewed = await Repository(db).renew_processing_lease(user_id, owner, seconds)
            except Exception:
                renewed = False
            if not renewed:
                task.cancel("Processing lease lost")
                return

    beat = asyncio.create_task(heartbeat(), name=f"user-lease-{user_id}")
    try:
        yield True
        await repo.before_external()
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
        repo.processing_lease = None
        await repo.session.rollback()
        async with session_scope() as db:
            await Repository(db).release_processing_lease(user_id, owner)
