"""Tests for backend.jobs.knowledge_extraction.

Covers:
- After embed, a background_jobs row is created for extraction.
- embedder._run_tenant_knowledge_extraction_best_effort logs WARNING and
  does not raise when the queue is unavailable (no main loop).
- embedder._run_tenant_knowledge_extraction_best_effort is a no-op when
  api_key is missing.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Async SQLite fixture (mirrors test_queue.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def queue_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
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


def _install_fake_pool(monkeypatch) -> AsyncMock:
    fake_pool = AsyncMock()
    job = AsyncMock()
    job.job_id = "fake-job-id"
    fake_pool.enqueue_job = AsyncMock(return_value=job)

    async def _fake_get_pool():
        return fake_pool

    monkeypatch.setattr(queue_module, "get_pool", _fake_get_pool)
    return fake_pool


# ---------------------------------------------------------------------------
# Test: enqueue_knowledge_extraction creates a background_jobs row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_creates_background_jobs_row(queue_db, monkeypatch):
    """Calling enqueue_knowledge_extraction must create a queued row in background_jobs."""
    _install_fake_pool(monkeypatch)

    # Import after monkeypatching so register_job side-effects are already applied.
    from backend.jobs.knowledge_extraction import enqueue_knowledge_extraction

    document_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    job_id = await enqueue_knowledge_extraction(
        document_id=document_id,
        tenant_id=tenant_id,
    )
    assert job_id is not None

    rows = (await queue_db.execute(select(BackgroundJob))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "extract_tenant_knowledge"
    assert row.status == BackgroundJobStatus.queued.value
    assert row.tenant_id == tenant_id
    assert str(document_id) in (row.payload or {}).get("document_id", "")


# ---------------------------------------------------------------------------
# Test: embedder gracefully degrades when no main loop
# ---------------------------------------------------------------------------


def test_embedder_no_loop_logs_warning_not_raises(caplog):
    """_run_tenant_knowledge_extraction_best_effort must not raise when no main loop."""
    import logging

    from backend.documents.embedder import _run_tenant_knowledge_extraction_best_effort

    # Ensure get_main_loop returns None (no running loop in sync test context).
    with patch("backend.core.queue.get_main_loop", return_value=None):
        with caplog.at_level(logging.WARNING):
            _run_tenant_knowledge_extraction_best_effort(
                document_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                api_key="sk-test",
            )

    # Should log WARNING about skipped enqueue, not raise.
    assert any(
        "knowledge_enqueue" in r.message or "knowledge" in r.message.lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


# ---------------------------------------------------------------------------
# Test: no-op when api_key is missing
# ---------------------------------------------------------------------------


def test_embedder_noop_when_no_api_key():
    """_run_tenant_knowledge_extraction_best_effort must be a no-op when api_key is falsy."""
    from backend.documents.embedder import _run_tenant_knowledge_extraction_best_effort

    with patch(
        "backend.jobs.knowledge_extraction.enqueue_knowledge_extraction_sync"
    ) as mock_enqueue:
        _run_tenant_knowledge_extraction_best_effort(
            document_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            api_key=None,
        )
        mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Test: embeddings/service.py enqueues extraction after successful embed
# ---------------------------------------------------------------------------


def test_run_embeddings_background_enqueues_extraction(monkeypatch):
    """run_embeddings_background must call enqueue_knowledge_extraction_sync, not inline extract."""
    doc_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    # Minimal fake doc
    fake_doc = MagicMock()
    fake_doc.tenant_id = tenant_id

    # Minimal fake db session
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_doc

    enqueue_calls: list[dict[str, Any]] = []

    def _fake_enqueue_sync(*, document_id, tenant_id):
        enqueue_calls.append({"document_id": document_id, "tenant_id": tenant_id})
        return "fake-job-id"

    with (
        patch("backend.core.db.SessionLocal", return_value=fake_db),
        patch(
            "backend.embeddings.service.create_embeddings_for_document"
        ),
        patch(
            "backend.embeddings.service.run_mode_a_for_tenant_when_queue_empty_best_effort"
        ),
        patch(
            "backend.jobs.knowledge_extraction.enqueue_knowledge_extraction_sync",
            side_effect=_fake_enqueue_sync,
        ),
    ):
        from backend.embeddings.service import run_embeddings_background

        run_embeddings_background(doc_id, "sk-test")

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["document_id"] == doc_id
    assert enqueue_calls[0]["tenant_id"] == tenant_id
