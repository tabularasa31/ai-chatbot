"""Regression tests for backend.core.queue.

Focus: the row-then-enqueue ordering that prevents a fast worker from
running the wrapper before the ``background_jobs`` row exists.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.core import db as core_db
from backend.core import queue as queue_module
from backend.models.base import Base
from backend.models.jobs import BackgroundJob, BackgroundJobStatus


@pytest_asyncio.fixture
async def queue_db():
    """Per-test async SQLite engine + AsyncSessionLocal patch.

    Repoints ``core_db.AsyncSessionLocal`` so ``queue._record_queued`` and
    friends open sessions on the in-memory test engine. Yields a session bound
    to the same engine for assertions.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    original = core_db.AsyncSessionLocal
    core_db.AsyncSessionLocal = factory
    queue_module._pool = None
    try:
        async with factory() as session:
            yield session
    finally:
        core_db.AsyncSessionLocal = original
        queue_module._pool = None
        await engine.dispose()


def _install_fake_pool(monkeypatch, enqueue_side_effect=None) -> AsyncMock:
    fake_pool = AsyncMock()
    if enqueue_side_effect is not None:
        fake_pool.enqueue_job = AsyncMock(side_effect=enqueue_side_effect)
    else:
        job = AsyncMock()
        job.job_id = "will-be-set"
        fake_pool.enqueue_job = AsyncMock(return_value=job)

    async def _fake_get_pool():
        return fake_pool

    monkeypatch.setattr(queue_module, "get_pool", _fake_get_pool)
    return fake_pool


@pytest.mark.asyncio
async def test_status_row_committed_before_enqueue(queue_db, monkeypatch):
    """The wrapper UPDATEs would lose the race if enqueue published to Redis
    before the row was committed. Assert ordering by querying the DB from
    inside the fake ``enqueue_job``.
    """
    seen: dict[str, Any] = {}

    async def _capture_then_return(
        name: str, *args: Any, _job_id: str | None = None, **kwargs: Any
    ):
        async with core_db.AsyncSessionLocal() as probe:
            row = (
                await probe.execute(
                    select(BackgroundJob).where(BackgroundJob.arq_job_id == _job_id)
                )
            ).scalar_one_or_none()
        seen["row_exists"] = row is not None
        seen["row_status"] = row.status if row else None
        job = AsyncMock()
        job.job_id = _job_id
        return job

    _install_fake_pool(monkeypatch, enqueue_side_effect=_capture_then_return)

    job_id = await queue_module.enqueue("smoke_ping", kind="smoke_ping")

    assert job_id is not None
    assert seen["row_exists"] is True
    assert seen["row_status"] == BackgroundJobStatus.queued.value


@pytest.mark.asyncio
async def test_orphan_row_deleted_on_enqueue_failure(queue_db, monkeypatch):
    """When pool.enqueue_job raises, the pre-inserted row must be cleaned up."""

    async def _boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("redis exploded")

    _install_fake_pool(monkeypatch, enqueue_side_effect=_boom)

    job_id = await queue_module.enqueue("smoke_ping", kind="smoke_ping")
    assert job_id is None

    rows = (await queue_db.execute(select(BackgroundJob))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_orphan_row_deleted_when_arq_reports_duplicate(
    queue_db, monkeypatch
):
    """ARQ returns ``None`` from enqueue_job when the job_id is already
    queued by another producer. We must not leave a stale row behind."""

    async def _none(*_args: Any, **_kwargs: Any):
        return None

    _install_fake_pool(monkeypatch, enqueue_side_effect=_none)

    job_id = await queue_module.enqueue("smoke_ping", kind="smoke_ping")
    assert job_id is None

    rows = (await queue_db.execute(select(BackgroundJob))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_caller_supplied_dedup_collision_returns_none(
    queue_db, monkeypatch
):
    """Two enqueues with the same ``job_id`` — the second one collides on the
    unique index and must report duplicate without disturbing the first."""

    async def _echo(name: str, *args: Any, _job_id: str | None = None, **kwargs: Any):
        job = AsyncMock()
        job.job_id = _job_id
        return job

    _install_fake_pool(monkeypatch, enqueue_side_effect=_echo)

    first = await queue_module.enqueue(
        "smoke_ping", kind="smoke_ping", job_id="dedup-key"
    )
    second = await queue_module.enqueue(
        "smoke_ping", kind="smoke_ping", job_id="dedup-key"
    )

    assert first == "dedup-key"
    assert second is None

    rows = (await queue_db.execute(select(BackgroundJob))).scalars().all()
    assert len(rows) == 1
    assert rows[0].arq_job_id == "dedup-key"
