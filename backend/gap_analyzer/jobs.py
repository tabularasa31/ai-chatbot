"""Background entrypoints for Gap Analyzer."""

from __future__ import annotations

import logging
from uuid import UUID

from backend.core import db as core_db
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository

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
