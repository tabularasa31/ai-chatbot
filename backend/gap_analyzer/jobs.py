"""Background entrypoints for Gap Analyzer."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from uuid import UUID

from backend.core import db as core_db
from backend.core.openai_errors import OpenAIFailureKind, classify_openai_error
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import GapJobRecord, SqlAlchemyGapAnalyzerRepository
from backend.models import Document, DocumentStatus, SourceStatus, Tenant, UrlSource

logger = logging.getLogger(__name__)
_GAP_JOB_HEARTBEAT_SECONDS = 300
_DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = float(os.getenv("GAP_SHUTDOWN_TIMEOUT_SECONDS", "25.0"))


@dataclass
class _GapJobRunnerState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: bool = False
    restart_requested: bool = False
    thread: threading.Thread | None = None

    def request_start(self) -> bool:
        with self.lock:
            if self.active:
                self.restart_requested = True
                return False
            self.active = True
            return True

    def set_thread(self, thread: threading.Thread) -> None:
        with self.lock:
            self.thread = thread

    def current_thread(self) -> threading.Thread | None:
        with self.lock:
            return self.thread

    def finish_cycle(self) -> bool:
        with self.lock:
            if self.restart_requested:
                self.restart_requested = False
                return True
            self.active = False
            self.thread = None
            return False


_job_runner_state = _GapJobRunnerState()
_shutdown_event = threading.Event()
_active_worker_lock = threading.Lock()
_active_worker_job: tuple[UUID, UUID] | None = None


def enqueue_gap_job_for_tenant_best_effort(
    tenant_id: UUID,
    *,
    job_kind: GapJobKind,
    trigger: str,
) -> None:
    db = core_db.SessionLocal()
    try:
        repository = SqlAlchemyGapAnalyzerRepository(db)
        _ = repository.enqueue_gap_job(
            tenant_id=tenant_id,
            job_kind=job_kind,
            trigger=trigger,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "gap_analyzer_enqueue_failed tenant_id=%s job_kind=%s trigger=%s",
            tenant_id,
            job_kind.value,
            trigger,
            exc_info=True,
        )
        return
    finally:
        db.close()

    start_gap_analyzer_job_runner()


def start_gap_analyzer_job_runner() -> None:
    if not _job_runner_state.request_start():
        return

    def _runner() -> None:
        while not _shutdown_event.is_set():
            run_pending_gap_analyzer_jobs_best_effort()
            if _shutdown_event.is_set():
                _job_runner_state.finish_cycle()
                return
            if not _job_runner_state.finish_cycle():
                return

    thread = threading.Thread(target=_runner, name="gap-analyzer-job-runner", daemon=True)
    _job_runner_state.set_thread(thread)
    thread.start()


def run_pending_gap_analyzer_jobs_best_effort(*, max_jobs: int | None = None) -> int:
    processed = 0
    while max_jobs is None or processed < max_jobs:
        if _shutdown_event.is_set():
            break
        job = _claim_next_gap_job()
        if job is None:
            break
        _register_active_job(job)
        try:
            _run_claimed_gap_job(job)
            processed += 1
        finally:
            _clear_active_job()
    return processed


def _claim_next_gap_job() -> GapJobRecord | None:
    if _shutdown_event.is_set():
        return None
    db = core_db.SessionLocal()
    try:
        repository = SqlAlchemyGapAnalyzerRepository(db)
        job = repository.claim_next_gap_job()
        if job is None:
            db.rollback()
            return None
        db.commit()
        return job
    except Exception:
        db.rollback()
        logger.warning("gap_analyzer_claim_failed", exc_info=True)
        return None
    finally:
        db.close()


def _run_claimed_gap_job(job: GapJobRecord) -> None:
    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_refresh_gap_job_lease_until_stopped,
        args=(job.job_id, job.tenant_id, stop_heartbeat),
        daemon=True,
        name=f"gap-analyzer-heartbeat-{job.job_id}",
    )
    heartbeat_thread.start()
    db = core_db.SessionLocal()
    try:
        repository = SqlAlchemyGapAnalyzerRepository(db)
        orchestrator = GapAnalyzerOrchestrator(repository=repository)
        if job.job_kind == GapJobKind.mode_a:
            orchestrator.run_mode_a(job.tenant_id)
        elif job.job_kind == GapJobKind.mode_b:
            orchestrator.run_mode_b(job.tenant_id)
        else:
            orchestrator.run_mode_b_weekly_reclustering(job.tenant_id)
        if not repository.complete_gap_job(job_id=job.job_id, tenant_id=job.tenant_id):
            logger.error(
                "gap_analyzer_job_complete_no_rows job_id=%s tenant_id=%s",
                job.job_id,
                job.tenant_id,
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        classified = classify_openai_error(exc)
        _fail_gap_job(
            job.job_id,
            job.tenant_id,
            str(exc),
            failure_kind=classified.kind,
            retry_after_seconds=classified.retry_after_seconds,
        )
        logger.warning(
            "gap_analyzer_job_failed tenant_id=%s job_kind=%s attempt=%s",
            job.tenant_id,
            job.job_kind.value,
            job.attempt_count,
            exc_info=True,
        )
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1.0)
        db.close()


def _fail_gap_job(
    job_id: UUID,
    tenant_id: UUID,
    error_message: str,
    *,
    failure_kind: OpenAIFailureKind,
    retry_after_seconds: float | None,
) -> None:
    db = core_db.SessionLocal()
    try:
        repository = SqlAlchemyGapAnalyzerRepository(db)
        if not repository.fail_gap_job(
            job_id=job_id,
            tenant_id=tenant_id,
            error_message=error_message,
            failure_kind=failure_kind,
            retry_after_seconds=retry_after_seconds,
        ):
            logger.error(
                "gap_analyzer_job_fail_no_rows job_id=%s tenant_id=%s",
                job_id,
                tenant_id,
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("gap_analyzer_job_finalize_failure_failed job_id=%s", job_id, exc_info=True)
    finally:
        db.close()


def _refresh_gap_job_lease_until_stopped(
    job_id: UUID,
    tenant_id: UUID,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(_GAP_JOB_HEARTBEAT_SECONDS):
        if _shutdown_event.is_set():
            return
        db = core_db.SessionLocal()
        try:
            repository = SqlAlchemyGapAnalyzerRepository(db)
            refreshed = repository.refresh_gap_job_lease(job_id=job_id, tenant_id=tenant_id)
            db.commit()
            if not refreshed:
                return
        except Exception:
            db.rollback()
            logger.warning("gap_analyzer_job_lease_refresh_failed job_id=%s", job_id, exc_info=True)
        finally:
            db.close()


def request_graceful_shutdown(timeout_seconds: float = _DEFAULT_SHUTDOWN_TIMEOUT_SECONDS) -> None:
    if _shutdown_event.is_set():
        return

    logger.info("gap_analyzer_graceful_shutdown_requested timeout=%s", timeout_seconds)
    _shutdown_event.set()

    thread = _job_runner_state.current_thread()
    if thread is None or not thread.is_alive():
        logger.info("gap_analyzer_graceful_shutdown_runner_joined timeout=%s", timeout_seconds)
        return

    thread.join(timeout=timeout_seconds)
    if not thread.is_alive():
        logger.info("gap_analyzer_graceful_shutdown_runner_joined timeout=%s", timeout_seconds)
        return

    with _active_worker_lock:
        active_job = _active_worker_job
    if active_job is None:
        return

    _release_job_for_shutdown(*active_job)


def _register_active_job(job: GapJobRecord) -> None:
    with _active_worker_lock:
        global _active_worker_job
        _active_worker_job = (job.job_id, job.tenant_id)


def _clear_active_job() -> None:
    with _active_worker_lock:
        global _active_worker_job
        _active_worker_job = None


def _release_job_for_shutdown(job_id: UUID, tenant_id: UUID) -> None:
    db = core_db.SessionLocal()
    try:
        repository = SqlAlchemyGapAnalyzerRepository(db)
        released = repository.release_gap_job_for_retry(
            job_id=job_id,
            tenant_id=tenant_id,
            reason="graceful_shutdown_timeout",
        )
        db.commit()
        logger.warning(
            "gap_analyzer_graceful_shutdown_released job_id=%s released=%s",
            job_id,
            released,
        )
    except Exception:
        db.rollback()
        logger.error(
            "gap_analyzer_graceful_shutdown_release_failed job_id=%s",
            job_id,
            exc_info=True,
        )
    finally:
        db.close()


def run_mode_a_for_tenant_best_effort(tenant_id: UUID) -> None:
    db = core_db.SessionLocal()
    try:
        orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db))
        orchestrator.run_mode_a(tenant_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "gap_analyzer_mode_a_best_effort_failed tenant_id=%s",
            tenant_id,
            exc_info=True,
        )
    finally:
        db.close()


def run_mode_a_for_tenant_when_queue_empty_best_effort(tenant_id: UUID) -> None:
    db = core_db.SessionLocal()
    try:
        pending_document_count = (
            db.query(Document)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.status.in_([DocumentStatus.processing.value, DocumentStatus.embedding.value]))
            .count()
        )
        pending_source_count = (
            db.query(UrlSource)
            .filter(UrlSource.tenant_id == tenant_id)
            .filter(UrlSource.status.in_([SourceStatus.queued.value, SourceStatus.indexing.value]))
            .count()
        )
        if pending_document_count > 0 or pending_source_count > 0:
            logger.info(
                "gap_analyzer_mode_a_skipped_queue_not_empty tenant_id=%s pending_documents=%s pending_sources=%s",
                tenant_id,
                pending_document_count,
                pending_source_count,
            )
            return
    finally:
        db.close()

    enqueue_gap_job_for_tenant_best_effort(
        tenant_id,
        job_kind=GapJobKind.mode_a,
        trigger="queue_empty",
    )


def run_mode_b_for_tenant_best_effort(tenant_id: UUID) -> None:
    db = core_db.SessionLocal()
    try:
        orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db))
        orchestrator.run_mode_b(tenant_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "gap_analyzer_mode_b_best_effort_failed tenant_id=%s",
            tenant_id,
            exc_info=True,
        )
    finally:
        db.close()


def run_mode_b_weekly_reclustering_for_tenant_best_effort(tenant_id: UUID) -> None:
    db = core_db.SessionLocal()
    try:
        orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db))
        orchestrator.run_mode_b_weekly_reclustering(tenant_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "gap_analyzer_mode_b_weekly_reclustering_failed tenant_id=%s",
            tenant_id,
            exc_info=True,
        )
    finally:
        db.close()


def run_mode_b_weekly_reclustering_for_all_tenants_best_effort() -> None:
    db = core_db.SessionLocal()
    try:
        tenant_ids = [tenant_id for (tenant_id,) in db.query(Tenant.id).order_by(Tenant.id.asc()).all()]
    finally:
        db.close()

    for tenant_id in tenant_ids:
        enqueue_gap_job_for_tenant_best_effort(
            tenant_id,
            job_kind=GapJobKind.mode_b_weekly_reclustering,
            trigger="weekly_reclustering",
        )
