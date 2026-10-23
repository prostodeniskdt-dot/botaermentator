"""Async database session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db.ssl import build_asyncpg_ssl_connect_args
from app.logging import get_logger

_SLOW_DB_OPERATION_MS = 100


def _log_timing(event_name: str, started: float) -> None:
    duration_ms = (perf_counter() - started) * 1000
    logger = get_logger(__name__)
    # Frequent queue polls must not evict request/agent events from bounded logs.
    log = logger.info if duration_ms >= _SLOW_DB_OPERATION_MS else logger.debug
    log(event_name, duration_ms=round(duration_ms, 2))


class TimedAsyncSession(AsyncSession):
    async def execute(self, *args, **kwargs):
        started = perf_counter()
        await self.connection()
        _log_timing("db_checkout_timing", started)
        return await super().execute(*args, **kwargs)


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _ssl_connect_args(settings: Settings) -> dict:
    from sqlalchemy.engine.url import make_url

    hostname = make_url(settings.database_url).host
    return build_asyncpg_ssl_connect_args(
        ssl_required=settings.database_ssl_required,
        hostname=hostname,
        root_cert_path=settings.database_ssl_root_cert or None,
    )


def get_engine(settings: Settings | None = None):
    global _engine, _session_factory
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_async_engine(
            settings.database_url,
            connect_args=_ssl_connect_args(settings),
            pool_pre_ping=True,
            hide_parameters=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
        )

        @event.listens_for(_engine.sync_engine, "before_cursor_execute")
        def before_cursor(conn, cursor, statement, parameters, context, executemany):
            context._metric_started = perf_counter()

        @event.listens_for(_engine.sync_engine, "after_cursor_execute")
        def after_cursor(conn, cursor, statement, parameters, context, executemany):
            _log_timing("db_query_timing", context._metric_started)

        @event.listens_for(_engine.sync_engine, "begin")
        def transaction_start(conn):
            conn.info["transaction_started"] = perf_counter()

        def transaction_end(conn):
            started = conn.info.pop("transaction_started", None)
            if started is not None:
                _log_timing("db_transaction_timing", started)

        event.listen(_engine.sync_engine, "commit", transaction_end)
        event.listen(_engine.sync_engine, "rollback", transaction_end)
        _session_factory = async_sessionmaker(
            _engine, class_=TimedAsyncSession, expire_on_commit=False
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    from sqlalchemy import text

    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        get_logger(__name__).error("database_connection_error", error_type=type(exc).__name__)
        return False


def reset_engine() -> None:
    global _engine, _session_factory
    _engine = None
    _session_factory = None
