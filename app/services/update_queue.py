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
        stale_after_seconds: int,
        heartbeat_seconds: int,
        delivery_handler,
    ) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self.poll_interval_seconds = poll_interval_seconds
        self.max_attempts = max_attempts
        self.stale_after_seconds = stale_after_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.delivery_handler = delivery_handler
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        logger.info("update_queue_worker_started")
        while not self._stopping.is_set():
            delivery = await self._claim_delivery_safely()
            if delivery is not None:
                await self._process_delivery(delivery)
                continue
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
            if job.lease_id is None:
                logger.error("queued_update_missing_lease", job_id=str(job.id))
                continue
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(job.id, job.lease_id),
                name=f"update-heartbeat-{job.telegram_update_id}",
            )
            try:
                update = Update.model_validate(job.payload, context={"bot": self.bot})
                await self.dispatcher.feed_update(self.bot, update)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "queued_update_failed",
                    update_id=job.telegram_update_id,
                    attempts=job.attempts,
                )
                await self._fail(job.id, job.lease_id, job.attempts, exc)
            else:
                await self._complete(job.id, job.lease_id)
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
        logger.info("update_queue_worker_stopped")

    def stop(self) -> None:
        self._stopping.set()

    async def _claim(self):
        async with session_scope() as db:
            return await Repository(db).claim_update_job(
                stale_after_seconds=self.stale_after_seconds
            )

    async def _complete(self, job_id, lease_id) -> None:
        async with session_scope() as db:
            await Repository(db).complete_update_job(job_id, lease_id)

    async def _fail(self, job_id, lease_id, attempts: int, exc: Exception) -> None:
        retry = attempts < self.max_attempts
        delay = min(2**attempts, 60)
        async with session_scope() as db:
            await Repository(db).fail_update_job(
                job_id,
                lease_id,
                error=f"{type(exc).__name__}: {exc}",
                retry=retry,
                retry_delay_seconds=delay,
            )

    async def _heartbeat_loop(self, job_id, lease_id) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            async with session_scope() as db:
                renewed = await Repository(db).heartbeat_update_job(job_id, lease_id)
            if not renewed:
                return

    async def _claim_delivery_safely(self):
        try:
            async with session_scope() as db:
                return await Repository(db).claim_delivery_job(
                    stale_after_seconds=self.stale_after_seconds
                )
        except Exception:  # noqa: BLE001
            logger.exception("delivery_queue_claim_failed")
            return None

    async def _process_delivery(self, job) -> None:
        if job.lease_id is None:
            logger.error("delivery_missing_lease", job_id=str(job.id))
            return
        heartbeat = asyncio.create_task(
            self._delivery_heartbeat_loop(job.id, job.lease_id),
            name=f"delivery-heartbeat-{job.id}",
        )
        try:
            sent_message = await self.delivery_handler(job)
        except Exception as exc:  # noqa: BLE001
            logger.exception("telegram_delivery_failed", job_id=str(job.id))
            retry = job.attempts < self.max_attempts
            async with session_scope() as db:
                await Repository(db).fail_delivery_job(
                    job.id,
                    job.lease_id,
                    error=f"{type(exc).__name__}: {exc}",
                    retry=retry,
                    retry_delay_seconds=min(2**job.attempts, 60),
                )
        else:
            async with session_scope() as db:
                await Repository(db).complete_delivery_job(
                    job.id, job.lease_id, sent_message.message_id
                )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _delivery_heartbeat_loop(self, job_id, lease_id) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            async with session_scope() as db:
                renewed = await Repository(db).heartbeat_delivery_job(job_id, lease_id)
            if not renewed:
                return
