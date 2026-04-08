"""Background entrypoints for Gap Analyzer."""

from __future__ import annotations

import logging
import threading
from uuid import UUID

from backend.core import db as core_db
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import GapJobRecord, SqlAlchemyGapAnalyzerRepository
from backend.models import Client, Document, DocumentStatus, UrlSource, SourceStatus

logger = logging.getLogger(__name__)
_job_runner_lock = threading.Lock()
_job_runner_active = False
_job_runner_restart_requested = False
_GAP_JOB_HEARTBEAT_SECONDS = 300


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
    global _job_runner_active, _job_runner_restart_requested
    with _job_runner_lock:
        if _job_runner_active:
            _job_runner_restart_requested = True
            return
        _job_runner_active = True

    def _runner() -> None:
        global _job_runner_active, _job_runner_restart_requested
        while True:
            run_pending_gap_analyzer_jobs_best_effort()
            with _job_runner_lock:
                if _job_runner_restart_requested:
                    _job_runner_restart_requested = False
                    continue
                _job_runner_active = False
                return

    threading.Thread(target=_runner, daemon=True).start()


def run_pending_gap_analyzer_jobs_best_effort(*, max_jobs: int | None = None) -> int:
    processed = 0
    while max_jobs is None or processed < max_jobs:
        job = _claim_next_gap_job()
        if job is None:
            break
        _run_claimed_gap_job(job)
        processed += 1
    return processed


def _claim_next_gap_job() -> GapJobRecord | None:
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
    threading.Thread(
        target=_refresh_gap_job_lease_until_stopped,
        args=(job.job_id, stop_heartbeat),
        daemon=True,
    ).start()
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
        repository.complete_gap_job(job_id=job.job_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        _fail_gap_job(job.job_id, str(exc))
        logger.warning(
            "gap_analyzer_job_failed tenant_id=%s job_kind=%s attempt=%s",
            job.tenant_id,
            job.job_kind.value,
            job.attempt_count,
            exc_info=True,
        )
    finally:
        stop_heartbeat.set()
        db.close()


def _fail_gap_job(job_id: UUID, error_message: str) -> None:
    db = core_db.SessionLocal()
    try:
        repository = SqlAlchemyGapAnalyzerRepository(db)
        repository.fail_gap_job(job_id=job_id, error_message=error_message)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("gap_analyzer_job_finalize_failure_failed job_id=%s", job_id, exc_info=True)
    finally:
        db.close()


def _refresh_gap_job_lease_until_stopped(job_id: UUID, stop_event: threading.Event) -> None:
    while not stop_event.wait(_GAP_JOB_HEARTBEAT_SECONDS):
        db = core_db.SessionLocal()
        try:
            repository = SqlAlchemyGapAnalyzerRepository(db)
            refreshed = repository.refresh_gap_job_lease(job_id=job_id)
            db.commit()
            if not refreshed:
                return
        except Exception:
            db.rollback()
            logger.warning("gap_analyzer_job_lease_refresh_failed job_id=%s", job_id, exc_info=True)
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
            .filter(Document.client_id == tenant_id)
            .filter(Document.status.in_([DocumentStatus.processing.value, DocumentStatus.embedding.value]))
            .count()
        )
        pending_source_count = (
            db.query(UrlSource)
            .filter(UrlSource.client_id == tenant_id)
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
        tenant_ids = [tenant_id for (tenant_id,) in db.query(Client.id).order_by(Client.id.asc()).all()]
    finally:
        db.close()

    for tenant_id in tenant_ids:
        enqueue_gap_job_for_tenant_best_effort(
            tenant_id,
            job_kind=GapJobKind.mode_b_weekly_reclustering,
            trigger="weekly_reclustering",
        )
