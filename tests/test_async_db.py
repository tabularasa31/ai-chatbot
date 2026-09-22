"""Async DB contour: engine/session wiring, URL normalization, and the
PendingRollbackError guards on the async write path.

A failed flush/commit on an AsyncSession (e.g. an asyncpg DataError from a
naive/aware datetime mismatch, or any constraint violation) leaves the
transaction rolled back at the driver level but still marked active in
SQLAlchemy. The *next* statement on the same session then raises
PendingRollbackError — masking the real cause and surfacing as a generic 500.
The guard tests below pin the two guards that keep a poisoned session from
outliving the operation that broke it:

* :func:`backend.core.db.async_commit_or_rollback` — used on the async chat
  write path.
* :func:`backend.core.db.get_async_db` — the request-scoped dependency.

The trigger is a NOT NULL violation on ``Tenant.name`` (no default), which
raises on flush on both SQLite and Postgres, so the reproduction runs in the
fast SQLite contour. PG enforcement of read-only writes is covered separately
under ``tests/pgvector_tests/test_async_readonly_engine.py``.
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core import db as core_db
from backend.core.config import Settings, settings
from backend.core.db import (
    _build_async_readonly_engine_kwargs,
    _to_async_url,
    async_commit_or_rollback,
    async_readonly_engine,
    get_async_readonly_db,
)
from backend.models import Base, Chat, Tenant

# ---------------------------------------------------------------------------
# Wiring canary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_session_wiring_is_healthy(async_db_session: AsyncSession) -> None:
    """Canary that aiosqlite, the async engine, and Base.metadata are wired up."""
    result = await async_db_session.execute(text("SELECT 1"))
    assert result.scalar_one() == 1

    tables = await async_db_session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    )
    assert "users" in {row[0] for row in tables.all()}


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Legacy postgres:// is the primary case to fix.
        ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        # Already-valid URLs pass through unchanged.
        ("postgresql://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_normalize_database_url_validator(url: str, expected: str) -> None:
    assert Settings._normalize_database_url(url) == expected


@pytest.mark.parametrize(
    ("sync_url", "expected"),
    [
        # Postgres variants — including legacy ``postgres://`` from Railway/Heroku.
        ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgresql+psycopg2://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgres://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        # SQLite.
        ("sqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
        ("sqlite:////abs/path.db", "sqlite+aiosqlite:////abs/path.db"),
        # Already-async URLs pass through unchanged.
        ("postgresql+asyncpg://u:p@h/db", "postgresql+asyncpg://u:p@h/db"),
        ("sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite:///:memory:"),
    ],
)
def test_to_async_url(sync_url: str, expected: str) -> None:
    assert _to_async_url(sync_url) == expected


# ---------------------------------------------------------------------------
# Read-only engine wiring
#
# The kwargs builder and module-level engine URL binding are tested without
# opening connections — opening a session against the SQLite ``:memory:``
# readonly engine inside the suite event loop deadlocked on CI.
# ---------------------------------------------------------------------------


def test_readonly_engine_kwargs_postgres_sets_readonly_server_setting() -> None:
    kwargs = _build_async_readonly_engine_kwargs("postgresql+asyncpg://u:p@h:5432/db")
    assert kwargs["connect_args"] == {
        "server_settings": {"default_transaction_read_only": "on"},
    }
    assert kwargs["pool_size"] == settings.db_pool_size
    assert kwargs["max_overflow"] == settings.db_max_overflow


def test_readonly_engine_kwargs_sqlite_skips_pool_and_server_settings() -> None:
    kwargs = _build_async_readonly_engine_kwargs("sqlite+aiosqlite:///:memory:")
    assert kwargs == {"future": True}


def test_readonly_engine_module_wiring() -> None:
    """Bound engine uses the test SQLite URL; dependency is an async generator."""
    assert str(async_readonly_engine.url).startswith("sqlite+aiosqlite://")
    assert inspect.isasyncgenfunction(get_async_readonly_db)


# ---------------------------------------------------------------------------
# PendingRollbackError guards
# ---------------------------------------------------------------------------


def _valid_tenant() -> Tenant:
    return Tenant(name="Acme", settings={})


def _flush_failing_tenant() -> Tenant:
    # ``name`` is NOT NULL with no default → INSERT fails at flush time.
    return Tenant(name=None, settings={})


@pytest.fixture
def patched_async_factory(async_engine_fx, monkeypatch: pytest.MonkeyPatch):
    factory = async_sessionmaker(
        bind=async_engine_fx,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(core_db, "AsyncSessionLocal", factory)
    return factory


@pytest.mark.asyncio
async def test_async_commit_or_rollback_recovers_broken_session(
    async_db_session: AsyncSession,
) -> None:
    """A failed commit is re-raised but leaves the session reusable."""
    async_db_session.add(_flush_failing_tenant())

    with pytest.raises(IntegrityError):
        await async_commit_or_rollback(async_db_session)

    # The guard rolled the broken transaction back, so a subsequent write on
    # the SAME session succeeds instead of raising PendingRollbackError.
    good = _valid_tenant()
    async_db_session.add(good)
    await async_commit_or_rollback(async_db_session)

    fetched = await async_db_session.execute(
        select(Tenant).where(Tenant.id == good.id)
    )
    assert fetched.scalar_one().name == "Acme"


@pytest.mark.asyncio
async def test_raw_commit_leaves_session_poisoned(
    async_db_session: AsyncSession,
) -> None:
    """Documents the bug the guard prevents: a raw commit leaves the session
    in a needs-rollback state, so the next statement raises
    ``PendingRollbackError`` rather than the original cause."""
    async_db_session.add(_flush_failing_tenant())

    with pytest.raises(IntegrityError):
        await async_db_session.commit()

    async_db_session.add(_valid_tenant())
    with pytest.raises(PendingRollbackError):
        await async_db_session.commit()


def test_finalize_surfaces_turn_row_flush_error_unmasked() -> None:
    """A flush failure on the turn's own rows propagates as the original
    exception, not a masked ``PendingRollbackError``.

    ``_finalize_persisted_messages`` flushes the messages/chat update before
    the best-effort session-turn savepoint. Regression guard: ``begin_nested()``
    implicitly flushes pending state, so if the flush were left to happen inside
    the tracking ``try`` the real error would be swallowed by its ``except`` and
    re-surface as ``PendingRollbackError`` at commit.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.chat.persistence import _finalize_persisted_messages

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, future=True)()

    # ``tenant_id`` is NOT NULL with no default → the chat row fails at flush,
    # standing in for the asyncpg DataError seen in production.
    bad_chat = Chat(tenant_id=None, session_id=uuid.uuid4(), user_context={})

    with pytest.raises(IntegrityError):
        _finalize_persisted_messages(
            db=db,
            chat=bad_chat,
            tenant_id=uuid.uuid4(),
            extra_tokens=0,
        )

    # Session recovered: a follow-up write on the same session succeeds.
    good = Tenant(name="Acme", settings={})
    db.add(good)
    db.commit()
    assert db.get(Tenant, good.id) is not None
    db.close()


@pytest.mark.asyncio
async def test_get_async_db_rolls_back_on_consumer_error(
    patched_async_factory,
) -> None:
    """``get_async_db`` rolls back when the request handler raises, so a
    poisoned transaction never returns to the pool."""
    gen = core_db.get_async_db()
    db = await gen.__anext__()

    # Simulate a handler that broke the transaction with a failed flush and
    # then let an exception propagate out of the dependency.
    db.add(_flush_failing_tenant())
    with pytest.raises(IntegrityError):
        await db.flush()

    boom = RuntimeError("handler failed")
    with pytest.raises(RuntimeError):
        await gen.athrow(boom)

    # After the dependency unwinds, the invalid row was not persisted.
    async with patched_async_factory() as verify:
        rows = await verify.execute(select(Tenant.id).where(Tenant.name.is_(None)))
        assert rows.first() is None


@pytest.mark.asyncio
async def test_get_async_db_normal_path_yields_session(
    patched_async_factory,
) -> None:
    """The happy path still yields a working session and commits survive."""
    tenant_id: uuid.UUID | None = None
    async for db in core_db.get_async_db():
        tenant = _valid_tenant()
        db.add(tenant)
        await db.commit()
        tenant_id = tenant.id
        break

    async with patched_async_factory() as verify:
        fetched = await verify.execute(select(Tenant).where(Tenant.id == tenant_id))
        assert fetched.scalar_one().name == "Acme"
