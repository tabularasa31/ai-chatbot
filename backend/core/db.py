from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Generator
from typing import TypeVar

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

T = TypeVar("T")

engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=Session,
    future=True,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _to_async_url(url: str) -> str:
    """Map a sync SQLAlchemy URL to its async-driver counterpart.

    Lets services share a single ``DATABASE_URL`` env while still picking up
    asyncpg/aiosqlite under the async engine. URLs that already specify an
    async dialect pass through unchanged.
    """
    if url.startswith("postgresql+asyncpg://") or url.startswith("sqlite+aiosqlite://"):
        return url
    for sync_prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if url.startswith(sync_prefix):
            return "postgresql+asyncpg://" + url[len(sync_prefix) :]
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    return url


_async_url = _to_async_url(settings.database_url)
_async_engine_kwargs: dict = {"future": True}
if not _async_url.startswith("sqlite"):
    _async_engine_kwargs.update(pool_size=10, max_overflow=20)

async_engine = create_async_engine(_async_url, **_async_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def run_sync(
    db: AsyncSession,
    fn: Callable[[Session], T],
) -> T:
    """Greenlet-safe call into sync DB code from an async context.

    Single named entry point for the ``AsyncSession.run_sync`` pattern that
    bridges async chat handlers to sync DB helpers. Two reasons we cross the
    boundary: aiosqlite's greenlet context must be active for sync session
    ops, and tests monkeypatch sync paths (e.g. ``retrieve_context``) — both
    only fire when the call goes through ``run_sync``.
    """
    return await db.run_sync(fn)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    Use this for new async services. Existing sync code keeps using
    ``get_db``; both contours run side-by-side against the same database.
    """
    async with AsyncSessionLocal() as db:
        yield db


def _build_async_readonly_engine_kwargs(url: str) -> dict:
    """Engine kwargs for the read-only async engine.

    asyncpg ``server_settings={"default_transaction_read_only": "on"}`` makes
    every transaction start in read-only mode; writes raise SQLAlchemy
    ``DBAPIError`` wrapping ``asyncpg.ReadOnlySQLTransactionError``. Pure
    connection-level mechanism — no statement parsing. SQLite has no such
    flag, so the test contour falls back to a regular session.
    """
    kwargs: dict = {"future": True}
    if url.startswith("postgresql+asyncpg://"):
        kwargs["connect_args"] = {
            "server_settings": {"default_transaction_read_only": "on"},
        }
    if not url.startswith("sqlite"):
        kwargs.update(pool_size=10, max_overflow=20)
    return kwargs


async_readonly_engine = create_async_engine(
    _async_url, **_build_async_readonly_engine_kwargs(_async_url)
)

AsyncReadOnlySessionLocal = async_sessionmaker(
    bind=async_readonly_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_async_readonly_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a read-only async database session.

    Postgres enforces ``default_transaction_read_only=on`` at the connection
    level, so any write statement raises before reaching the table. Intended
    for analytics/reporting endpoints that must never mutate state.

    On SQLite (tests) the engine has no read-only mechanism; the dependency
    still works for query-shape parity but does not block writes.
    """
    async with AsyncReadOnlySessionLocal() as db:
        yield db
