"""Durable PostgreSQL-backed Telegram update worker."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from aiogram.types import Update

from app.db.repositories import Repository
from app.db.session import session_scope
from app.logging import get_logger

logger = get_logger(__name__)


class UpdateQueueWorker:
    def __init__(
        self,
        bot,
        dispatcher,
        *,
        poll_interval_seconds: float,
        max_attempts: int,
    ) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self.poll_interval_seconds = poll_interval_seconds
        self.max_attempts = max_attempts
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        logger.info("update_queue_worker_started")
        while not self._stopping.is_set():
            try:
                job = await self._claim()
            except Exception:  # noqa: BLE001
                logger.exception("update_queue_claim_failed")
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            if job is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.poll_interval_seconds
                    )
                continue
            try:
                update = Update.model_validate(job.payload, context={"bot": self.bot})
                await self.dispatcher.feed_update(self.bot, update)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "queued_update_failed",
                    update_id=job.telegram_update_id,
                    attempts=job.attempts,
                )
                await self._fail(job.id, job.attempts, exc)
            else:
                await self._complete(job.id)
        logger.info("update_queue_worker_stopped")

    def stop(self) -> None:
        self._stopping.set()

    async def _claim(self):
        async with session_scope() as db:
            return await Repository(db).claim_update_job()

    async def _complete(self, job_id) -> None:
        async with session_scope() as db:
            await Repository(db).complete_update_job(job_id)

    async def _fail(self, job_id, attempts: int, exc: Exception) -> None:
        retry = attempts < self.max_attempts
        delay = min(2**attempts, 60)
        async with session_scope() as db:
            await Repository(db).fail_update_job(
                job_id,
                error=f"{type(exc).__name__}: {exc}",
                retry=retry,
                retry_delay_seconds=delay,
            )
