"""Background entrypoints for Gap Analyzer."""

from __future__ import annotations

import logging
from uuid import UUID

from backend.core import db as core_db
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import Client, Document, DocumentStatus, UrlSource, SourceStatus

logger = logging.getLogger(__name__)


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

    run_mode_a_for_tenant_best_effort(tenant_id)


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
        for (tenant_id,) in db.query(Client.id).order_by(Client.id.asc()).yield_per(1000):
            run_mode_b_weekly_reclustering_for_tenant_best_effort(tenant_id)
    finally:
        db.close()
