from __future__ import annotations

from datetime import datetime, timezone
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.gap_analyzer.enums import GapJobKind, GapJobStatus
from backend.gap_analyzer.jobs import (
    _GapJobRunnerState,
    _claim_next_gap_job,
    _refresh_gap_job_lease_until_stopped,
    request_graceful_shutdown,
    start_gap_analyzer_job_runner,
)
from backend.gap_analyzer._repo.job_queue_helpers import _GAP_JOB_LAST_ERROR_MAX_CHARS
from backend.gap_analyzer.repository import (
    SqlAlchemyGapAnalyzerRepository,
    invalidate_bm25_cache_for_tenant,
)
from backend.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    GapAnalyzerJob,
    GapCluster,
    GapClusterStatus,
    Tenant,
    User,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client_and_token(
    tenant: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(tenant, db_session, email=email)
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.json()
    tenant_id = uuid.UUID(response.json()["id"])
    set_client_openai_key(tenant, token)
    return token, tenant_id


def _create_tenant_direct(db_session: Session) -> uuid.UUID:
    """Create a tenant directly in DB (for background-thread tests that can't use HTTP)."""
    user = User(
        email=f"gap-jobs-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="test-hash",
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(
        name=f"Gap Jobs {uuid.uuid4().hex[:8]}",
        api_key=f"gap-{uuid.uuid4().hex[:28]}",
    )
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant.id


@pytest.fixture()
def use_test_sessionlocal(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch SessionLocal so background threads use the test DB."""
    from backend.core import db as core_db

    testing_session_local = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=Session,
        future=True,
    )
    monkeypatch.setattr(core_db, "SessionLocal", testing_session_local)


def _wait_for(predicate, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Job lifecycle & repository
# ---------------------------------------------------------------------------

def test_job_lease_refresh_extends_expiration(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-jobs-lease@example.com", name="Gap Jobs Lease Tenant"
    )
    job = GapAnalyzerJob(
        tenant_id=tenant_id,
        job_kind="mode_b",
        status="in_progress",
        trigger="manual",
        lease_expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    previous_expiry = job.lease_expires_at

    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    refreshed = repository.refresh_gap_job_lease(job_id=job.id, tenant_id=tenant_id)
    db_session.commit()
    db_session.refresh(job)

    assert refreshed is True
    assert previous_expiry is not None
    assert job.lease_expires_at is not None
    assert job.lease_expires_at > previous_expiry


def test_fail_job_truncates_error_to_tail(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-jobs-error@example.com", name="Gap Jobs Error Tenant"
    )
    job = GapAnalyzerJob(
        tenant_id=tenant_id,
        job_kind="mode_a",
        status="in_progress",
        trigger="manual",
        attempt_count=1,
        max_attempts=1,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    error_message = "head\n" + ("middle\n" * 1000) + "ValueError: final failure"
    repository.fail_gap_job(job_id=job.id, tenant_id=tenant_id, error_message=error_message)
    db_session.commit()
    db_session.refresh(job)

    assert job.status == "failed"
    assert job.last_error is not None
    assert len(job.last_error) <= _GAP_JOB_LAST_ERROR_MAX_CHARS
    assert "ValueError: final failure" in job.last_error


def test_complete_job_ignores_other_tenant(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_a = _create_client_and_token(
        tenant, db_session, email="gap-jobs-complete-a@example.com", name="Gap Jobs Complete A"
    )
    _, tenant_b = _create_client_and_token(
        tenant, db_session, email="gap-jobs-complete-b@example.com", name="Gap Jobs Complete B"
    )
    job_a = GapAnalyzerJob(tenant_id=tenant_a, job_kind="mode_a", status="in_progress", trigger="manual")
    job_b = GapAnalyzerJob(tenant_id=tenant_b, job_kind="mode_b", status="in_progress", trigger="manual")
    db_session.add_all([job_a, job_b])
    db_session.commit()
    db_session.refresh(job_a)
    db_session.refresh(job_b)

    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    completed = repository.complete_gap_job(job_id=job_a.id, tenant_id=tenant_b)
    db_session.commit()
    db_session.refresh(job_a)
    db_session.refresh(job_b)

    assert completed is False
    assert job_a.status == "in_progress"
    assert job_a.finished_at is None
    assert job_b.status == "in_progress"


def test_fail_job_ignores_other_tenant(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_a = _create_client_and_token(
        tenant, db_session, email="gap-jobs-fail-a@example.com", name="Gap Jobs Fail A"
    )
    _, tenant_b = _create_client_and_token(
        tenant, db_session, email="gap-jobs-fail-b@example.com", name="Gap Jobs Fail B"
    )
    job_a = GapAnalyzerJob(
        tenant_id=tenant_a, job_kind="mode_a", status="in_progress", trigger="manual",
        attempt_count=1, max_attempts=1,
    )
    job_b = GapAnalyzerJob(tenant_id=tenant_b, job_kind="mode_b", status="in_progress", trigger="manual")
    db_session.add_all([job_a, job_b])
    db_session.commit()
    db_session.refresh(job_a)
    db_session.refresh(job_b)

    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    failed = repository.fail_gap_job(
        job_id=job_a.id, tenant_id=tenant_b, error_message="should not apply"
    )
    db_session.commit()
    db_session.refresh(job_a)
    db_session.refresh(job_b)

    assert failed is False
    assert job_a.status == "in_progress"
    assert job_a.last_error is None
    assert job_b.status == "in_progress"


# ---------------------------------------------------------------------------
# BM25 search
# ---------------------------------------------------------------------------

def test_bm25_returns_exact_title_and_body_matches(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-bm25-stream@example.com", name="Gap BM25 Stream Tenant"
    )
    title_document = Document(
        tenant_id=tenant_id, filename="Invoice Exports",
        file_type=DocumentType.markdown, status=DocumentStatus.ready,
    )
    body_document = Document(
        tenant_id=tenant_id, filename="guide.md",
        file_type=DocumentType.markdown, status=DocumentStatus.ready,
    )
    filler_document = Document(
        tenant_id=tenant_id, filename="faq.md",
        file_type=DocumentType.markdown, status=DocumentStatus.ready,
    )
    db_session.add_all([title_document, body_document, filler_document])
    db_session.flush()
    db_session.add_all(
        [
            Embedding(document_id=title_document.id, chunk_text="overview only", vector=[0.1] * 1536),
            Embedding(document_id=body_document.id, chunk_text="invoice exports and billing workflows", vector=[0.1] * 1536),
            Embedding(document_id=filler_document.id, chunk_text="account profile settings and notifications", vector=[0.1] * 1536),
        ]
    )
    db_session.commit()

    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    title_match = repository.bm25_match_for_tenant(
        tenant_id=tenant_id, query_text="invoice exports", excluded_file_types=()
    )
    body_match = repository.bm25_match_for_tenant(
        tenant_id=tenant_id, query_text="billing workflows", excluded_file_types=()
    )

    assert title_match.hit is True
    assert title_match.match_kind == "exact_title"
    assert title_match.score == 1.0
    assert body_match.hit is True
    assert body_match.match_kind == "body"
    assert 0.0 < body_match.score < 1.0


def test_bm25_handles_single_term_queries(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-bm25-single@example.com", name="Gap BM25 Single Tenant"
    )
    document = Document(
        tenant_id=tenant_id, filename="guide.md",
        file_type=DocumentType.markdown, status=DocumentStatus.ready,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(Embedding(document_id=document.id, chunk_text="billing exports workflow", vector=[0.1] * 1536))
    db_session.commit()

    match = SqlAlchemyGapAnalyzerRepository(db_session).bm25_match_for_tenant(
        tenant_id=tenant_id, query_text="billing", excluded_file_types=()
    )

    assert match.hit is True
    assert match.match_kind == "body"
    assert 0.0 < match.score < 1.0


def test_bm25_skips_db_for_empty_token_query(
    db_session: Session,
    monkeypatch,
) -> None:
    repository = SqlAlchemyGapAnalyzerRepository(db_session)

    def _fail_query(*args, **kwargs):
        raise AssertionError("DB query should not run for an empty tokenized query")

    monkeypatch.setattr(repository.db, "query", _fail_query)

    match = repository.bm25_match_for_tenant(
        tenant_id=uuid.uuid4(), query_text="!!!", excluded_file_types=()
    )

    assert match.hit is False
    assert match.score == 0.0
    assert match.match_kind == "none"


def test_bm25_cache_reuses_corpus_until_invalidated(
    db_session: Session,
    monkeypatch,
) -> None:
    user = User(
        email="gap-bm25-cache@example.com",
        password_hash="x",
        is_verified=True,
        verification_token=None,
        verification_expires_at=None,
    )
    db_session.add(user)
    db_session.flush()
    client_record = Tenant(name="Gap BM25 Cache", api_key="k" * 32)
    db_session.add(client_record)
    db_session.flush()
    document = Document(
        tenant_id=client_record.id, filename="guide.md",
        file_type=DocumentType.markdown, status=DocumentStatus.ready,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(Embedding(document_id=document.id, chunk_text="billing exports workflow", vector=[0.1] * 1536))
    db_session.commit()

    invalidate_bm25_cache_for_tenant(client_record.id)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    query_calls = 0
    original_query = repository.db.query

    def _counted_query(*args, **kwargs):
        nonlocal query_calls
        query_calls += 1
        return original_query(*args, **kwargs)

    monkeypatch.setattr(repository.db, "query", _counted_query)

    first_match = repository.bm25_match_for_tenant(
        tenant_id=client_record.id, query_text="billing", excluded_file_types=()
    )
    second_match = repository.bm25_match_for_tenant(
        tenant_id=client_record.id, query_text="workflow", excluded_file_types=()
    )

    assert first_match.hit is True
    assert second_match.hit is True
    assert query_calls == 1

    invalidate_bm25_cache_for_tenant(client_record.id)
    third_match = repository.bm25_match_for_tenant(
        tenant_id=client_record.id, query_text="exports", excluded_file_types=()
    )

    assert third_match.hit is True
    assert query_calls == 2


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

def test_job_runner_restarts_when_enqueue_races_with_shutdown(monkeypatch) -> None:
    import backend.gap_analyzer.jobs as gap_jobs_module

    monkeypatch.setattr(gap_jobs_module, "_job_runner_state", _GapJobRunnerState())

    calls: list[str] = []
    done = threading.Event()

    def _fake_run_pending(*, max_jobs=None):
        calls.append("run")
        if len(calls) == 1:
            start_gap_analyzer_job_runner()
            return 0
        done.set()
        return 0

    monkeypatch.setattr(
        gap_jobs_module,
        "run_pending_gap_analyzer_jobs_best_effort",
        _fake_run_pending,
    )

    start_gap_analyzer_job_runner()

    assert done.wait(1.0), "runner should perform a second drain pass after restart is requested"
    for _ in range(50):
        if not gap_jobs_module._job_runner_state.active:
            break
        time.sleep(0.01)
    assert calls == ["run", "run"]


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

def test_shutdown_stops_idle_runner(monkeypatch: pytest.MonkeyPatch) -> None:
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
    use_test_sessionlocal,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    tenant_id = _create_tenant_direct(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    repository.enqueue_gap_job(tenant_id=tenant_id, job_kind=GapJobKind.mode_a, trigger="manual")
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

    assert _wait_for(lambda: gap_jobs._job_runner_state.current_thread() is None)
    gap_jobs._shutdown_event.clear()

    reclaimed = _claim_next_gap_job()
    assert reclaimed is not None
    assert reclaimed.job_id == job.id
    assert reclaimed.attempt_count == 2


def test_shutdown_when_job_finishes_before_timeout(
    use_test_sessionlocal,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    finished = threading.Event()
    tenant_id = _create_tenant_direct(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    repository.enqueue_gap_job(tenant_id=tenant_id, job_kind=GapJobKind.mode_a, trigger="manual")
    db_session.commit()

    released_calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    def _release_spy(job_id, tenant_id):
        released_calls.append((job_id, tenant_id))

    def _fast_run_mode_a(self, run_tenant_id):
        assert run_tenant_id == tenant_id
        time.sleep(0.1)
        finished.set()

    monkeypatch.setattr("backend.gap_analyzer.jobs._release_job_for_shutdown", _release_spy)
    monkeypatch.setattr("backend.gap_analyzer.jobs.GapAnalyzerOrchestrator.run_mode_a", _fast_run_mode_a)

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

    assert _wait_for(lambda: len(refresh_calls) >= 1)
    request_graceful_shutdown(timeout_seconds=0.1)
    first_count = len(refresh_calls)
    time.sleep(0.05)
    stop_event.set()
    heartbeat.join(timeout=1.0)

    assert len(refresh_calls) == first_count


def test_new_claim_blocked_after_shutdown(
    use_test_sessionlocal,
    db_session: Session,
) -> None:
    tenant_id = _create_tenant_direct(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    for _ in range(5):
        repository.enqueue_gap_job(tenant_id=tenant_id, job_kind=GapJobKind.mode_a, trigger="manual")
    db_session.commit()

    request_graceful_shutdown(timeout_seconds=0.1)

    assert _claim_next_gap_job() is None
