"""Gap analyzer job queue operations and helpers."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from backend.core.openai_errors import OpenAIFailureKind
from backend.gap_analyzer._repo.capabilities import (
    _aware_datetime,
    _enum_value,
    _repository_capabilities,
)
from backend.gap_analyzer._repo.job_retry import (
    effective_max_attempts,
    retry_delay_for_kind,
)
from backend.gap_analyzer._repo.records import GapJobEnqueueResult, GapJobRecord
from backend.gap_analyzer.enums import GapCommandStatus, GapJobKind, GapJobStatus
from backend.gap_analyzer.schemas import GapRunMode
from backend.models import GapAnalyzerJob

logger = logging.getLogger(__name__)
_GAP_JOB_LEASE_SECONDS = 1800
_GAP_JOB_CLAIM_MAX_ATTEMPTS = 3
_GAP_JOB_LAST_ERROR_MAX_CHARS = 4000
def _gap_job_status(value: GapJobStatus | str) -> GapJobStatus:
    if isinstance(value, GapJobStatus):
        return value
    return GapJobStatus(str(value))
def _gap_job_kind(value: GapJobKind | str) -> GapJobKind:
    if isinstance(value, GapJobKind):
        return value
    return GapJobKind(str(value))
def _remaining_lease_seconds(lease_expires_at: datetime | None) -> int | None:
    if lease_expires_at is None:
        return None
    aware_lease_expires_at = _aware_datetime(lease_expires_at)
    remaining = int((aware_lease_expires_at - datetime.now(UTC)).total_seconds())
    return max(1, remaining) if remaining > 0 else None
def _truncate_gap_job_error(error_message: str) -> str:
    if len(error_message) <= _GAP_JOB_LAST_ERROR_MAX_CHARS:
        return error_message
    truncated_prefix = "...[truncated]\n"
    tail_size = _GAP_JOB_LAST_ERROR_MAX_CHARS - len(truncated_prefix)
    if tail_size <= 0:
        return error_message[-_GAP_JOB_LAST_ERROR_MAX_CHARS:]
    # Keep the traceback tail because the final frames and exception text are usually the most actionable.
    return truncated_prefix + error_message[-tail_size:]
class _JobQueueOps:
    def __init__(self, db: Session) -> None:
        self._db = db

    def enqueue_gap_job(
        self,
        *,
        tenant_id: UUID,
        job_kind: GapJobKind,
        trigger: str,
    ) -> GapJobEnqueueResult:
        capabilities = _repository_capabilities(self._db)
        existing = (
            self._db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.tenant_id == tenant_id, GapAnalyzerJob.job_kind == job_kind)
            .filter(GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value, GapJobStatus.in_progress.value]))
            .order_by(GapAnalyzerJob.created_at.desc(), GapAnalyzerJob.id.desc())
            .first()
        )
        if existing is not None:
            status = GapCommandStatus.in_progress if _gap_job_status(existing.status) == GapJobStatus.in_progress else GapCommandStatus.accepted
            retry_after_seconds = _remaining_lease_seconds(existing.lease_expires_at) if _gap_job_status(existing.status) == GapJobStatus.in_progress else None
            return GapJobEnqueueResult(status=status, enqueued=False, retry_after_seconds=retry_after_seconds)

        self._db.add(
            GapAnalyzerJob(
                tenant_id=tenant_id,
                job_kind=job_kind,
                status=_enum_value(GapJobStatus.queued, capabilities=capabilities),
                trigger=trigger,
                available_at=datetime.now(UTC),
            )
        )
        self._db.flush()
        return GapJobEnqueueResult(status=GapCommandStatus.accepted, enqueued=True)

    def claim_next_gap_job(self) -> GapJobRecord | None:
        """Claim the next eligible gap job, using SKIP LOCKED when available."""
        capabilities = _repository_capabilities(self._db)
        if capabilities.supports_skip_locked:
            now = datetime.now(UTC)
            lease_expires_at = now + timedelta(seconds=_GAP_JOB_LEASE_SECONDS)
            candidate = (
                self._db.query(GapAnalyzerJob)
                .filter(
                    or_(
                        and_(
                            GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value]),
                            GapAnalyzerJob.available_at <= now,
                        ),
                        and_(
                            GapAnalyzerJob.status == GapJobStatus.in_progress,
                            GapAnalyzerJob.lease_expires_at.isnot(None),
                            GapAnalyzerJob.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(GapAnalyzerJob.available_at.asc(), GapAnalyzerJob.created_at.asc(), GapAnalyzerJob.id.asc())
                .with_for_update(skip_locked=True, of=GapAnalyzerJob)
                .limit(1)
                .first()
            )
            if candidate is None:
                return None

            candidate.status = _enum_value(GapJobStatus.in_progress, capabilities=capabilities)
            candidate.leased_at = now
            candidate.lease_expires_at = lease_expires_at
            candidate.started_at = candidate.started_at or now
            candidate.attempt_count = int(candidate.attempt_count or 0) + 1
            candidate.updated_at = now
            self._db.add(candidate)
            self._db.flush()
            return GapJobRecord(
                job_id=candidate.id,
                tenant_id=candidate.tenant_id,
                job_kind=_gap_job_kind(candidate.job_kind),
                status=_gap_job_status(candidate.status),
                trigger=candidate.trigger,
                attempt_count=int(candidate.attempt_count or 0),
                max_attempts=int(candidate.max_attempts or 0),
            )

        for _ in range(_GAP_JOB_CLAIM_MAX_ATTEMPTS):
            now = datetime.now(UTC)
            lease_expires_at = now + timedelta(seconds=_GAP_JOB_LEASE_SECONDS)
            candidate = (
                self._db.query(GapAnalyzerJob.id)
                .filter(
                    or_(
                        and_(
                            GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value]),
                            GapAnalyzerJob.available_at <= now,
                        ),
                        and_(
                            GapAnalyzerJob.status == GapJobStatus.in_progress,
                            GapAnalyzerJob.lease_expires_at.isnot(None),
                            GapAnalyzerJob.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(GapAnalyzerJob.available_at.asc(), GapAnalyzerJob.created_at.asc(), GapAnalyzerJob.id.asc())
                .first()
            )
            if candidate is None:
                return None

            job_id = candidate[0]
            updated_rows = (
                self._db.query(GapAnalyzerJob)
                .filter(GapAnalyzerJob.id == job_id)
                .filter(
                    or_(
                        and_(
                            GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value]),
                            GapAnalyzerJob.available_at <= now,
                        ),
                        and_(
                            GapAnalyzerJob.status == GapJobStatus.in_progress,
                            GapAnalyzerJob.lease_expires_at.isnot(None),
                            GapAnalyzerJob.lease_expires_at < now,
                        ),
                    )
                )
                .update(
                    {
                        GapAnalyzerJob.status: _enum_value(GapJobStatus.in_progress, capabilities=capabilities),
                        GapAnalyzerJob.leased_at: now,
                        GapAnalyzerJob.lease_expires_at: lease_expires_at,
                        GapAnalyzerJob.started_at: case(
                            (GapAnalyzerJob.started_at.is_(None), now),
                            else_=GapAnalyzerJob.started_at,
                        ),
                        GapAnalyzerJob.attempt_count: case(
                            (GapAnalyzerJob.attempt_count.is_(None), 1),
                            else_=GapAnalyzerJob.attempt_count + 1,
                        ),
                        GapAnalyzerJob.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if updated_rows == 0:
                continue
            self._db.flush()
            job = self._db.get(GapAnalyzerJob, job_id)
            if job is None:
                return None
            return GapJobRecord(
                job_id=job.id,
                tenant_id=job.tenant_id,
                job_kind=_gap_job_kind(job.job_kind),
                status=_gap_job_status(job.status),
                trigger=job.trigger,
                attempt_count=int(job.attempt_count or 0),
                max_attempts=int(job.max_attempts or 0),
            )
        return None

    def refresh_gap_job_lease(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=_GAP_JOB_LEASE_SECONDS)
        updated_rows = (
            self._db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .filter(GapAnalyzerJob.status == GapJobStatus.in_progress)
            .update(
                {
                    GapAnalyzerJob.leased_at: now,
                    GapAnalyzerJob.lease_expires_at: lease_expires_at,
                    GapAnalyzerJob.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        self._db.flush()
        return updated_rows > 0

    def release_gap_job_for_retry(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> bool:
        capabilities = _repository_capabilities(self._db)
        now = datetime.now(UTC)
        updated_rows = (
            self._db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .filter(GapAnalyzerJob.status == GapJobStatus.in_progress)
            .update(
                {
                    GapAnalyzerJob.status: _enum_value(GapJobStatus.retry, capabilities=capabilities),
                    GapAnalyzerJob.leased_at: None,
                    GapAnalyzerJob.lease_expires_at: None,
                    GapAnalyzerJob.available_at: now,
                    GapAnalyzerJob.updated_at: now,
                    GapAnalyzerJob.last_error: _truncate_gap_job_error(
                        f"released_for_graceful_shutdown: {reason}"
                    ),
                },
                synchronize_session=False,
            )
        )
        self._db.flush()
        return updated_rows > 0

    def complete_gap_job(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        now = datetime.now(UTC)
        updated_rows = (
            self._db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .filter(GapAnalyzerJob.status == GapJobStatus.in_progress)
            .update(
                {
                    GapAnalyzerJob.status: _enum_value(GapJobStatus.completed, capabilities=_repository_capabilities(self._db)),
                    GapAnalyzerJob.finished_at: now,
                    GapAnalyzerJob.leased_at: None,
                    GapAnalyzerJob.lease_expires_at: None,
                    GapAnalyzerJob.updated_at: now,
                    GapAnalyzerJob.last_error: None,
                },
                synchronize_session=False,
            )
        )
        self._db.flush()
        if updated_rows == 0:
            logger.warning(
                "gap_analyzer_job_finalize_skipped_unexpected_tenant job_id=%s tenant_id=%s",
                job_id,
                tenant_id,
            )
            return False
        return True

    def fail_gap_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        error_message: str,
        failure_kind: OpenAIFailureKind = OpenAIFailureKind.UNKNOWN,
        retry_after_seconds: float | None = None,
    ) -> bool:
        job = (
            self._db.query(GapAnalyzerJob)
            .filter(
                GapAnalyzerJob.id == job_id,
                GapAnalyzerJob.tenant_id == tenant_id,
                GapAnalyzerJob.status == GapJobStatus.in_progress,
            )
            .first()
        )
        if job is None:
            logger.warning(
                "gap_analyzer_job_finalize_skipped_unexpected_tenant job_id=%s tenant_id=%s",
                job_id,
                tenant_id,
            )
            return False
        capabilities = _repository_capabilities(self._db)
        now = datetime.now(UTC)
        attempt_count = int(job.attempt_count or 0)
        max_attempts = effective_max_attempts(job, failure_kind)
        final_failure = (
            failure_kind == OpenAIFailureKind.PERMANENT or attempt_count >= max_attempts
        )
        if final_failure:
            job.status = _enum_value(GapJobStatus.failed, capabilities=capabilities)
            job.finished_at = now
            job.available_at = now
            logger.warning(
                "gap_analyzer_job_final_failure",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "job_kind": str(job.job_kind),
                    "attempt_count": attempt_count,
                    "failure_kind": failure_kind.value,
                    "last_error_preview": error_message[:200],
                },
            )
        else:
            retry_delay = retry_delay_for_kind(
                attempt_count=attempt_count,
                failure_kind=failure_kind,
                retry_after_seconds=retry_after_seconds,
            )
            job.status = _enum_value(GapJobStatus.retry, capabilities=capabilities)
            job.available_at = now + timedelta(seconds=retry_delay)
            logger.info(
                "gap_analyzer_job_retry_scheduled",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "attempt_count": attempt_count,
                    "next_attempt": attempt_count + 1,
                    "failure_kind": failure_kind.value,
                    "delay_seconds": retry_delay,
                    "retry_after_hint": retry_after_seconds,
                },
            )
        job.leased_at = None
        job.lease_expires_at = None
        job.updated_at = now
        job.last_error = _truncate_gap_job_error(error_message)
        self._db.add(job)
        self._db.flush()
        return True

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> GapJobEnqueueResult:
        results: list[GapJobEnqueueResult] = []
        if mode in {GapRunMode.mode_a, GapRunMode.both}:
            results.append(
                self.enqueue_gap_job(
                    tenant_id=tenant_id,
                    job_kind=GapJobKind.mode_a,
                    trigger="manual",
                )
            )
        if mode in {GapRunMode.mode_b, GapRunMode.both}:
            results.append(
                self.enqueue_gap_job(
                    tenant_id=tenant_id,
                    job_kind=GapJobKind.mode_b,
                    trigger="manual",
                )
            )
        if any(result.enqueued for result in results):
            return GapJobEnqueueResult(status=GapCommandStatus.accepted, enqueued=True)
        retry_after_seconds = max(
            [result.retry_after_seconds for result in results if result.retry_after_seconds is not None],
            default=None,
        )
        return GapJobEnqueueResult(
            status=GapCommandStatus.in_progress if results else GapCommandStatus.accepted,
            enqueued=False,
            retry_after_seconds=retry_after_seconds,
        )
