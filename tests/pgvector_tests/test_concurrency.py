"""Concurrency tests that require real PostgreSQL (SELECT ... FOR UPDATE SKIP LOCKED, ON CONFLICT)."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.contact_sessions import service as user_session_service
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import ContactSession, GapAnalyzerJob
from tests.test_models import _create_client, _create_user


@pytest.mark.pgvector
def test_claim_next_gap_job_claims_unique_jobs_without_double_attempts(
    pg_engine,
    pg_db_session: Session,
) -> None:
    user = _create_user(pg_db_session, email="gap-job-claim-concurrency@example.com")
    tenant = _create_client(pg_db_session, user, name="Gap Job Claim Concurrency")
    jobs = [
        GapAnalyzerJob(
            tenant_id=tenant.id,
            job_kind=GapJobKind.mode_a.value,
            status="queued",
            trigger=f"test-{index}",
            available_at=datetime.now(UTC),
        )
        for index in range(5)
    ]
    pg_db_session.add_all(jobs)
    pg_db_session.commit()

    barrier = threading.Barrier(5)
    session_factory = sessionmaker(
        autocommit=False, autoflush=False, bind=pg_engine, class_=Session, future=True
    )

    def _worker():
        with session_factory() as db:
            barrier.wait(timeout=5)
            repository = SqlAlchemyGapAnalyzerRepository(db)
            row = repository.claim_next_gap_job()
            if row is None:
                db.rollback()
                return None
            db.commit()
            return (row.job_id, row.attempt_count)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker) for _ in range(5)]
        claimed_rows = [future.result(timeout=10) for future in futures]

    assert all(job_id is not None for job_id, _attempt_count in claimed_rows)
    claimed_ids = [job_id for job_id, _attempt_count in claimed_rows]
    assert len(set(claimed_ids)) == 5
    assert all(attempt_count == 1 for _job_id, attempt_count in claimed_rows)

    pg_db_session.expire_all()
    refreshed_jobs = (
        pg_db_session.query(GapAnalyzerJob)
        .filter(GapAnalyzerJob.tenant_id == tenant.id)
        .order_by(GapAnalyzerJob.created_at.asc(), GapAnalyzerJob.id.asc())
        .all()
    )
    assert len(refreshed_jobs) == 5
    assert all(job.status == "in_progress" for job in refreshed_jobs)
    assert all(int(job.attempt_count or 0) == 1 for job in refreshed_jobs)


@pytest.mark.pgvector
def test_start_user_session_two_writer_race_returns_winner_row_on_postgres(
    pg_engine,
    pg_db_session: Session,
    monkeypatch,
) -> None:
    user = _create_user(pg_db_session, email="user-session-race-pg@example.com")
    tenant = _create_client(pg_db_session, user, name="User Session Race PG")
    user_context = {"user_id": "u1", "email": "user-session-race-pg@example.com"}
    barrier = threading.Barrier(2)
    original_create = user_session_service._create_user_session_row

    def _barrier_create(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(user_session_service, "_create_user_session_row", _barrier_create)
    session_factory = sessionmaker(
        autocommit=False, autoflush=False, bind=pg_engine, class_=Session, future=True
    )

    def _worker() -> uuid.UUID | None:
        with session_factory() as db:
            row = user_session_service.start_user_session(
                db, tenant_id=tenant.id, user_context=user_context
            )
            db.commit()
            return row.id if row is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_worker)
        second = executor.submit(_worker)
        ids = [first.result(timeout=10), second.result(timeout=10)]

    assert ids[0] is not None
    assert ids[1] is not None
    assert ids[0] == ids[1]

    pg_db_session.expire_all()
    active_rows = (
        pg_db_session.query(ContactSession)
        .filter(
            ContactSession.tenant_id == tenant.id,
            ContactSession.contact_id == "u1",
            ContactSession.session_ended_at.is_(None),
        )
        .all()
    )
    assert len(active_rows) == 1
    assert active_rows[0].id == ids[0]
