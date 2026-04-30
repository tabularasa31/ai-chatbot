from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

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


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    Use this for new async services. Existing sync code keeps using
    ``get_db``; both contours run side-by-side against the same database.
    """
    async with AsyncSessionLocal() as db:
        yield db
