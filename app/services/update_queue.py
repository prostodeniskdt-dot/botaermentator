"""Durable PostgreSQL-backed Telegram update worker."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from aiogram.types import Update

from app.db.repositories import Repository
from app.db.session import session_scope
from app.logging import get_logger
from app.services.metrics import stage

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
        concurrency: int = 2,
        maintenance_handler=None,
        enable_updates=True,
        delivery_failure_handler=None,
    ) -> None:
        self.delivery_failure_handler = delivery_failure_handler
        self.enable_updates = enable_updates
        self.concurrency = concurrency
        self.maintenance_handler = maintenance_handler
        self.bot = bot
        self.dispatcher = dispatcher
        self.poll_interval_seconds = poll_interval_seconds
        self.max_attempts = max_attempts
        self.stale_after_seconds = stale_after_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.delivery_handler = delivery_handler
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        logger.info("update_queue_worker_started", concurrency=self.concurrency)
        tasks = [asyncio.create_task(self._run_deliveries())]
        if self.enable_updates:
            tasks.extend(
                asyncio.create_task(self._run_updates("question")) for _ in range(self.concurrency)
            )
            tasks.append(asyncio.create_task(self._run_updates("service")))
        if self.maintenance_handler:
            tasks.append(asyncio.create_task(self._run_maintenance()))
        try:
            await asyncio.gather(*tasks)
        finally:
            self._stopping.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("update_queue_worker_stopped")

    async def _idle(self):
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval_seconds)

    async def _run_updates(self, lane):
        while not self._stopping.is_set():
            try:
                job = await self._claim(lane)
            except Exception:
                logger.exception("update_queue_claim_failed", lane=lane)
                await self._idle()
                continue
            if job is None:
                await self._idle()
                continue
            if job.lease_id is None:
                logger.error("queued_update_missing_lease", job_id=str(job.id))
                continue
            parent = asyncio.current_task()
            heartbeat = asyncio.create_task(self._heartbeat_loop(job.id, job.lease_id, parent))
            try:
                logger.info(
                    "update_queue_wait",
                    update_id=job.telegram_update_id,
                    lane=lane,
                    wait_ms=max(0, (datetime.now(UTC) - job.created_at).total_seconds() * 1000),
                )
                update = Update.model_validate(job.payload, context={"bot": self.bot})
                with stage("update_processing", update_id=job.telegram_update_id, lane=lane):
                    await self.dispatcher.feed_update(self.bot, update)
            except asyncio.CancelledError:
                if self._stopping.is_set():
                    raise
                logger.warning("update_processing_cancelled", job_id=str(job.id))
            except Exception as exc:
                logger.exception("queued_update_failed", update_id=job.telegram_update_id)
                await self._fail(job.id, job.lease_id, job.attempts, exc)
            else:
                await self._complete(job.id, job.lease_id)
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat

    async def _run_deliveries(self):
        while not self._stopping.is_set():
            job = await self._claim_delivery_safely()
            if job is None:
                await self._idle()
            else:
                try:
                    await self._process_delivery(job)
                except asyncio.CancelledError:
                    if self._stopping.is_set():
                        raise
                    logger.warning("delivery_processing_cancelled", job_id=str(job.id))

    async def _run_maintenance(self):
        while not self._stopping.is_set():
            try:
                await self.maintenance_handler()
            except Exception:
                logger.exception("queue_maintenance_failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=30)

    def stop(self) -> None:
        self._stopping.set()

    async def _claim(self, lane="question"):
        async with session_scope() as db:
            return await Repository(db).claim_update_job(
                stale_after_seconds=self.stale_after_seconds, lane=lane
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
                error=type(exc).__name__,
                retry=retry,
                retry_delay_seconds=delay,
            )

    async def _heartbeat_loop(self, job_id, lease_id, parent) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                async with session_scope() as db:
                    renewed = await Repository(db).heartbeat_update_job(job_id, lease_id)
            except Exception:
                renewed = False
            if not renewed:
                parent.cancel("Update lease lost")
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
        logger.info(
            "delivery_queue_wait",
            delivery_id=str(job.id),
            question_id=str(job.question_id),
            attempt=job.attempts,
            wait_ms=max(0, (datetime.now(UTC) - job.created_at).total_seconds() * 1000),
        )
        heartbeat = asyncio.create_task(
            self._delivery_heartbeat_loop(job.id, job.lease_id, asyncio.current_task()),
            name=f"delivery-heartbeat-{job.id}",
        )
        try:
            with stage(
                "answer_delivery", delivery_id=str(job.id), question_id=str(job.question_id)
            ):
                sent_message = await self.delivery_handler(job)
        except Exception as exc:  # noqa: BLE001
            logger.exception("telegram_delivery_failed", job_id=str(job.id))
            retry = job.attempts < self.max_attempts and not getattr(exc, "delivery_unknown", False)
            async with session_scope() as db:
                await Repository(db).fail_delivery_job(
                    job.id,
                    job.lease_id,
                    error=type(exc).__name__,
                    retry=retry,
                    retry_delay_seconds=max(
                        min(2**job.attempts, 60), getattr(exc, "retry_after", 0)
                    ),
                )
            if not retry and self.delivery_failure_handler:
                try:
                    await self.delivery_failure_handler(job)
                except Exception:
                    logger.warning("delivery_failure_status_unavailable", job_id=str(job.id))
        else:
            async with session_scope() as db:
                await Repository(db).complete_delivery_job(
                    job.id, job.lease_id, sent_message.message_id
                )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _delivery_heartbeat_loop(self, job_id, lease_id, parent) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                async with session_scope() as db:
                    renewed = await Repository(db).heartbeat_delivery_job(job_id, lease_id)
            except Exception:
                renewed = False
            if not renewed:
                parent.cancel("Delivery lease lost")
                return
