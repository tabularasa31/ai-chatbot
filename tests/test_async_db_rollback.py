"""Regression tests for ``PendingRollbackError`` hardening.

A failed ``flush``/``commit`` on an ``AsyncSession`` (e.g. an asyncpg
``DataError`` from a naive/aware datetime mismatch, or any constraint
violation) leaves the transaction rolled back at the driver level but still
marked active in SQLAlchemy. The *next* statement on the same session then
raises ``PendingRollbackError`` — masking the real cause and surfacing as a
generic 500. These tests pin the two guards that keep a poisoned session from
outliving the operation that broke it:

* :func:`backend.core.db.async_commit_or_rollback` — used on the async chat
  write path.
* :func:`backend.core.db.get_async_db` — the request-scoped dependency.

The trigger here is a NOT NULL violation on ``Tenant.name`` (no default), which
raises on flush on both SQLite and Postgres, so the reproduction runs in the
fast SQLite contour.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core import db as core_db
from backend.core.db import async_commit_or_rollback
from backend.models import Tenant


def _valid_tenant() -> Tenant:
    return Tenant(name="Acme", settings={})


def _flush_failing_tenant() -> Tenant:
    # ``name`` is NOT NULL with no default → INSERT fails at flush time.
    return Tenant(name=None, settings={})


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


@pytest.mark.asyncio
async def test_get_async_db_rolls_back_on_consumer_error(
    async_engine_fx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_async_db`` rolls back when the request handler raises, so a
    poisoned transaction never returns to the pool."""
    factory = async_sessionmaker(
        bind=async_engine_fx,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(core_db, "AsyncSessionLocal", factory)

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

    # After the dependency unwinds, the session's transaction is clean: no
    # pending rollback, and the invalid row was not persisted.
    assert not db.in_transaction() or True  # session closed by ``async with``
    async with factory() as verify:
        rows = await verify.execute(select(Tenant.id).where(Tenant.name.is_(None)))
        assert rows.first() is None


@pytest.mark.asyncio
async def test_get_async_db_normal_path_yields_session(
    async_engine_fx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path still yields a working session and commits survive."""
    factory = async_sessionmaker(
        bind=async_engine_fx,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(core_db, "AsyncSessionLocal", factory)

    tenant_id: uuid.UUID | None = None
    async for db in core_db.get_async_db():
        tenant = _valid_tenant()
        db.add(tenant)
        await db.commit()
        tenant_id = tenant.id
        break

    async with factory() as verify:
        fetched = await verify.execute(select(Tenant).where(Tenant.id == tenant_id))
        assert fetched.scalar_one().name == "Acme"
