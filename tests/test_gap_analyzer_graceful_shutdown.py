from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
import uuid

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.gap_analyzer.enums import GapJobKind, GapJobStatus
from backend.gap_analyzer.jobs import (
    _claim_next_gap_job,
    _refresh_gap_job_lease_until_stopped,
    request_graceful_shutdown,
    start_gap_analyzer_job_runner,
)
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import Tenant, GapAnalyzerJob, User


@pytest.fixture(autouse=True)
def _use_test_sessionlocal(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.core import db as core_db

    testing_session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )
    monkeypatch.setattr(core_db, "SessionLocal", testing_session_local)


def _create_tenant(db_session: Session) -> uuid.UUID:
    user = User(
        email=f"gap-shutdown-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(
                name=f"Gap Shutdown {uuid.uuid4().hex[:8]}",
        api_key=f"gap-{uuid.uuid4().hex[:28]}",
    )
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant.id


def test_shutdown_stops_runner_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.gap_analyzer.jobs as gap_jobs

    idle_entered = threading.Event()

    def _idle_run_pending(*, max_jobs=None):
        idle_entered.set()
        gap_jobs._shutdown_event.wait(1.0)
        return 0

    monkeypatch.setattr(gap_jobs, "run_pending_gap_analyzer_jobs_best_effort", _idle_run_pending)

    start_gap_analyzer_job_runner()

    assert idle_entered.wait(1.0)
    request_graceful_shutdown(timeout_seconds=1.0)

    thread = gap_jobs._job_runner_state.current_thread()
    assert thread is None or not thread.is_alive()


def test_shutdown_releases_in_progress_job(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    tenant_id = _create_tenant(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    repository.enqueue_gap_job(
        tenant_id=tenant_id,
        job_kind=GapJobKind.mode_a,
        trigger="manual",
    )
    db_session.commit()

    running = threading.Event()
    release_execute = threading.Event()

    def _blocking_run_mode_a(self, run_tenant_id):
        assert run_tenant_id == tenant_id
        running.set()
        release_execute.wait(5.0)

    monkeypatch.setattr(
        "backend.gap_analyzer.jobs.GapAnalyzerOrchestrator.run_mode_a",
        _blocking_run_mode_a,
    )

    start_gap_analyzer_job_runner()
    assert running.wait(1.0)

    request_graceful_shutdown(timeout_seconds=0.2)

    db_session.expire_all()
    job = db_session.query(GapAnalyzerJob).filter(GapAnalyzerJob.tenant_id == tenant_id).one()
    assert job.status == GapJobStatus.retry
    assert job.lease_expires_at is None
    assert job.available_at is not None
    assert job.available_at <= datetime.now(timezone.utc).replace(tzinfo=None)
    assert job.attempt_count == 1

    release_execute.set()
    import backend.gap_analyzer.jobs as gap_jobs

    assert wait_for(lambda: gap_jobs._job_runner_state.current_thread() is None)
    gap_jobs._shutdown_event.clear()

    reclaimed = _claim_next_gap_job()
    assert reclaimed is not None
    assert reclaimed.job_id == job.id
    assert reclaimed.attempt_count == 2


def test_shutdown_when_job_finishes_before_timeout(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    finished = threading.Event()
    tenant_id = _create_tenant(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    repository.enqueue_gap_job(
        tenant_id=tenant_id,
        job_kind=GapJobKind.mode_a,
        trigger="manual",
    )
    db_session.commit()

    released_calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    def _release_spy(job_id, tenant_id):
        released_calls.append((job_id, tenant_id))

    def _fast_run_mode_a(self, run_tenant_id):
        assert run_tenant_id == tenant_id
        time.sleep(0.1)
        finished.set()

    monkeypatch.setattr(
        "backend.gap_analyzer.jobs._release_job_for_shutdown",
        _release_spy,
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.jobs.GapAnalyzerOrchestrator.run_mode_a",
        _fast_run_mode_a,
    )

    start_gap_analyzer_job_runner()
    assert finished.wait(1.0)
    request_graceful_shutdown(timeout_seconds=2.0)

    db_session.expire_all()
    job = db_session.query(GapAnalyzerJob).filter(GapAnalyzerJob.tenant_id == tenant_id).one()
    assert job.status == GapJobStatus.completed
    assert job.lease_expires_at is None
    assert released_calls == []


def test_shutdown_is_idempotent() -> None:
    request_graceful_shutdown(timeout_seconds=0.1)
    request_graceful_shutdown(timeout_seconds=0.1)


def test_heartbeat_stops_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.gap_analyzer.jobs as gap_jobs

    stop_event = threading.Event()
    refresh_calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    class _FakeRepository:
        def refresh_gap_job_lease(self, *, job_id, tenant_id):
            refresh_calls.append((job_id, tenant_id))
            return True

    class _FakeSession:
        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

        def close(self) -> None:
            return None

    job_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    monkeypatch.setattr(gap_jobs, "_GAP_JOB_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(gap_jobs.core_db, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(gap_jobs, "SqlAlchemyGapAnalyzerRepository", lambda db: _FakeRepository())

    heartbeat = threading.Thread(
        target=_refresh_gap_job_lease_until_stopped,
        args=(job_id, tenant_id, stop_event),
    )
    heartbeat.start()

    assert wait_for(lambda: len(refresh_calls) >= 1)
    request_graceful_shutdown(timeout_seconds=0.1)
    first_count = len(refresh_calls)
    time.sleep(0.05)
    stop_event.set()
    heartbeat.join(timeout=1.0)

    assert len(refresh_calls) == first_count


def test_new_claim_blocked_after_shutdown(
    db_session: Session,
) -> None:
    tenant_id = _create_tenant(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    for _ in range(5):
        repository.enqueue_gap_job(
            tenant_id=tenant_id,
            job_kind=GapJobKind.mode_a,
            trigger="manual",
        )
    db_session.commit()

    request_graceful_shutdown(timeout_seconds=0.1)

    assert _claim_next_gap_job() is None


def wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False
