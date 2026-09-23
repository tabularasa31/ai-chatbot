"""Best-effort background work kicked off after a chat turn is persisted."""

from __future__ import annotations

import logging
import uuid

from backend.core import db as core_db
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.jobs import enqueue_gap_job_for_tenant_best_effort
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import Chat, Message

logger = logging.getLogger(__name__)


def _try_ingest_gap_signal(
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    user_message: Message,
    assistant_message: Message,
    question_text: str,
    answer_confidence: float | None,
    was_rejected: bool,
    had_fallback: bool,
    was_escalated: bool,
    language: str | None = None,
) -> None:
    ingestion_db = core_db.SessionLocal()
    try:
        orchestrator = GapAnalyzerOrchestrator(
            repository=SqlAlchemyGapAnalyzerRepository(ingestion_db)
        )
        orchestrator.ingest_signal(
            GapSignal(
                tenant_id=tenant_id,
                chat_id=chat.id,
                session_id=session_id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                question_text=question_text,
                answer_confidence=answer_confidence,
                was_rejected=was_rejected,
                had_fallback=had_fallback,
                was_escalated=was_escalated,
                language=language,
            )
        )
        ingestion_db.commit()
        _start_mode_b_followup(tenant_id)
    except ValueError:
        ingestion_db.rollback()
        logger.warning(
            "gap_analyzer_signal_ingestion_contract_failed: tenant_id=%s session_id=%s assistant_message_id=%s",
            tenant_id,
            session_id,
            assistant_message.id,
            exc_info=True,
        )
    except Exception:
        ingestion_db.rollback()
        logger.exception(
            "gap_analyzer_signal_ingestion_failed: tenant_id=%s session_id=%s assistant_message_id=%s",
            tenant_id,
            session_id,
            assistant_message.id,
        )
    finally:
        ingestion_db.close()


def _start_mode_b_followup(tenant_id: uuid.UUID) -> None:
    enqueue_gap_job_for_tenant_best_effort(
        tenant_id,
        job_kind=GapJobKind.mode_b,
        trigger="chat_signal",
    )


def _trigger_log_analysis_threshold(
    tenant_id: uuid.UUID,
    api_key: str,
) -> None:
    import threading

    def _run() -> None:
        try:
            from backend.jobs.analyze_chat_logs import (
                increment_and_check_threshold,
            )
            increment_and_check_threshold(tenant_id=tenant_id, api_key=api_key)
        except Exception:
            logger.debug("Log analysis threshold check failed", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
