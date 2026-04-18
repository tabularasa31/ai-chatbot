"""Signal ingestion and query operations."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer._repo.records import StoredGapSignalState
from backend.gap_analyzer.events import GapSignal
from backend.models import GapQuestion, GapQuestionMessageLink

logger = logging.getLogger(__name__)


class _SignalsOps:
    def __init__(self, db: Session) -> None:
        self._db = db

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
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
        self._db.add(gap_question)
        self._db.flush()

        self._db.add(
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
        self._db.flush()

    def get_signal_state_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
    ) -> StoredGapSignalState | None:
        matches = (
            self._db.query(GapQuestion)
            .join(
                GapQuestionMessageLink,
                GapQuestionMessageLink.gap_question_id == GapQuestion.id,
            )
            .filter(
                GapQuestion.tenant_id == tenant_id,
                GapQuestionMessageLink.assistant_message_id == assistant_message_id,
            )
            .order_by(GapQuestion.created_at.desc(), GapQuestion.id.desc())
            .all()
        )
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                "gap_analyzer_multiple_signal_links_for_assistant_message: tenant_id=%s assistant_message_id=%s matches=%s",
                tenant_id,
                assistant_message_id,
                len(matches),
            )

        gap_question = matches[0]
        return StoredGapSignalState(
            gap_question_id=gap_question.id,
            answer_confidence=gap_question.answer_confidence,
            had_fallback=bool(gap_question.had_fallback),
            # Phase 2 persists reject/fallback turns in the same underlying bucket.
            had_rejected=bool(gap_question.had_fallback),
            had_escalation=bool(gap_question.had_escalation),
        )

    def update_signal_weight(
        self,
        *,
        gap_question_id: UUID,
        signal_weight: float,
    ) -> None:
        gap_question = self._db.get(GapQuestion, gap_question_id)
        if gap_question is None:
            raise ValueError(f"GapQuestion not found for id={gap_question_id}")
        gap_question.gap_signal_weight = signal_weight
        self._db.add(gap_question)
        self._db.flush()
