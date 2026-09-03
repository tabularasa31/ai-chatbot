"""GapQuestion signal ingestion + read helpers (Phase 2)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.gap_analyzer.events import GapSignal
from backend.models import GapQuestion, GapQuestionMessageLink


def store_signal(db: Session, signal: GapSignal, *, signal_weight: float) -> None:
    if signal.chat_id is None or signal.session_id is None:
        raise ValueError("GapSignal requires chat_id and session_id for Phase 2 ingestion")
    if signal.user_message_id is None or signal.assistant_message_id is None:
        raise ValueError(
            "GapSignal requires user_message_id and assistant_message_id for Phase 2 ingestion"
        )

    gap_question = GapQuestion(
        tenant_id=signal.tenant_id,
        question_text=signal.question_text,
        gap_signal_weight=signal_weight,
        answer_confidence=signal.answer_confidence,
        had_fallback=signal.had_fallback or signal.was_rejected,
        had_escalation=signal.was_escalated,
        language=signal.language,
        created_at=signal.created_at,
    )
    db.add(gap_question)
    db.flush()

    db.add(
        GapQuestionMessageLink(
            gap_question_id=gap_question.id,
            user_message_id=signal.user_message_id,
            assistant_message_id=signal.assistant_message_id,
            chat_id=signal.chat_id,
            session_id=signal.session_id,
            attempt_index=signal.attempt_index,
            created_at=signal.created_at,
        )
    )
    db.flush()
