from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.openai_errors import OpenAIFailureKind
from backend.gap_analyzer._repo.job_retry import (
    _GAP_JITTER_FRACTION,
    retry_delay_for_kind,
)
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import Tenant, GapAnalyzerJob, User


def _create_client(db_session: Session) -> uuid.UUID:
    user = User(
        email=f"{uuid.uuid4()}@example.com",
        password_hash="hash",
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()
    tenant = Tenant(
                name="Retry Tenant",
        api_key=uuid.uuid4().hex[:32],
    )
    db_session.add(tenant)
    db_session.commit()
    return tenant.id


def _create_job(
    db_session: Session,
    *,
    tenant_id: uuid.UUID,
    attempt_count: int,
    max_attempts: int,
) -> GapAnalyzerJob:
    job = GapAnalyzerJob(
        tenant_id=tenant_id,
        job_kind="mode_a",
        status="in_progress",
        trigger="manual",
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        available_at=datetime.now(UTC),
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _status_value(job: GapAnalyzerJob) -> str:
    value = job.status
    return value.value if hasattr(value, "value") else str(value)


def test_transient_failure_retries_with_backoff(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = _create_client(db_session)
    job = _create_job(db_session, tenant_id=tenant_id, attempt_count=1, max_attempts=3)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    monkeypatch.setattr("backend.gap_analyzer._repo.job_retry.random.uniform", lambda a, b: 0.0)

    before = datetime.now(UTC)
    repository.fail_gap_job(
        job_id=job.id,
        tenant_id=tenant_id,
        error_message="temporary",
        failure_kind=OpenAIFailureKind.TRANSIENT,
    )
    db_session.commit()
    db_session.refresh(job)
    after = datetime.now(UTC)

    assert job.status == "retry"
    min_expected = before.timestamp() + 30.0
    max_expected = after.timestamp() + 30.0
    assert min_expected <= job.available_at.replace(tzinfo=UTC).timestamp() <= max_expected


def test_permanent_failure_goes_straight_to_failed(db_session: Session) -> None:
    tenant_id = _create_client(db_session)
    job = _create_job(db_session, tenant_id=tenant_id, attempt_count=1, max_attempts=5)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)

    repository.fail_gap_job(
        job_id=job.id,
        tenant_id=tenant_id,
        error_message="auth",
        failure_kind=OpenAIFailureKind.PERMANENT,
    )
    db_session.commit()
    db_session.refresh(job)

    assert job.status == "failed"
    assert job.finished_at is not None


def test_rate_limit_honors_retry_after(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = _create_client(db_session)
    job = _create_job(db_session, tenant_id=tenant_id, attempt_count=1, max_attempts=3)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    monkeypatch.setattr("backend.gap_analyzer._repo.job_retry.random.uniform", lambda a, b: 6.0)

    before = datetime.now(UTC)
    repository.fail_gap_job(
        job_id=job.id,
        tenant_id=tenant_id,
        error_message="rate limited",
        failure_kind=OpenAIFailureKind.RATE_LIMIT,
        retry_after_seconds=60.0,
    )
    db_session.commit()
    db_session.refresh(job)

    delay = job.available_at.replace(tzinfo=UTC).timestamp() - before.timestamp()
    assert 60.0 <= delay <= 72.5


def test_rate_limit_falls_back_to_backoff_when_no_hint(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = _create_client(db_session)
    job = _create_job(db_session, tenant_id=tenant_id, attempt_count=2, max_attempts=3)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    monkeypatch.setattr("backend.gap_analyzer._repo.job_retry.random.uniform", lambda a, b: 0.0)

    before = datetime.now(UTC)
    repository.fail_gap_job(
        job_id=job.id,
        tenant_id=tenant_id,
        error_message="rate limited",
        failure_kind=OpenAIFailureKind.RATE_LIMIT,
        retry_after_seconds=None,
    )
    db_session.commit()
    db_session.refresh(job)

    delay = job.available_at.replace(tzinfo=UTC).timestamp() - before.timestamp()
    assert 60.0 <= delay <= 61.0


def test_transient_attempts_extend_to_five(db_session: Session) -> None:
    tenant_id = _create_client(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    statuses: list[str] = []

    for attempt in range(1, 6):
        job = _create_job(db_session, tenant_id=tenant_id, attempt_count=attempt, max_attempts=3)
        repository.fail_gap_job(
            job_id=job.id,
            tenant_id=tenant_id,
            error_message=f"attempt {attempt}",
            failure_kind=OpenAIFailureKind.TRANSIENT,
        )
        db_session.commit()
        db_session.refresh(job)
        statuses.append(_status_value(job))

    assert statuses[:4] == ["retry", "retry", "retry", "retry"]
    assert statuses[4] == "failed"


def test_unknown_error_capped_at_three_attempts(db_session: Session) -> None:
    tenant_id = _create_client(db_session)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    statuses: list[str] = []

    for attempt in range(1, 4):
        job = _create_job(db_session, tenant_id=tenant_id, attempt_count=attempt, max_attempts=5)
        repository.fail_gap_job(
            job_id=job.id,
            tenant_id=tenant_id,
            error_message=f"attempt {attempt}",
            failure_kind=OpenAIFailureKind.UNKNOWN,
        )
        db_session.commit()
        db_session.refresh(job)
        statuses.append(_status_value(job))

    assert statuses == ["retry", "retry", "failed"]


def test_retry_delays_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.gap_analyzer._repo.job_retry.random.uniform", lambda a, b: 0.0)

    delays = [
        retry_delay_for_kind(
            attempt_count=attempt,
            failure_kind=OpenAIFailureKind.TRANSIENT,
            retry_after_seconds=None,
        )
        for attempt in range(1, 5)
    ]

    assert delays == sorted(delays)


def test_final_failure_logs_warn_with_context(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant_id = _create_client(db_session)
    job = _create_job(db_session, tenant_id=tenant_id, attempt_count=5, max_attempts=5)
    repository = SqlAlchemyGapAnalyzerRepository(db_session)
    caplog.set_level(logging.WARNING, logger="backend.gap_analyzer._repo.job_queue")

    repository.fail_gap_job(
        job_id=job.id,
        tenant_id=tenant_id,
        error_message="boom",
        failure_kind=OpenAIFailureKind.TRANSIENT,
    )

    assert any(
        record.msg == "gap_analyzer_job_final_failure"
        and record.failure_kind == "transient"
        for record in caplog.records
    )


def test_migration_gap_jobs_retry_v1_applies(db_session: Session) -> None:
    server_default = db_session.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='gap_analyzer_jobs'")
    ).scalar_one()

    assert server_default is not None
    assert "max_attempts" in server_default


def test_gap_retry_jitter_fraction_is_25_percent() -> None:
    assert _GAP_JITTER_FRACTION == 0.25
