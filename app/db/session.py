"""Async database session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.db.ssl import build_asyncpg_ssl_connect_args

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
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
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
    except Exception:
        return False


def reset_engine() -> None:
    global _engine, _session_factory
    _engine = None
    _session_factory = None
