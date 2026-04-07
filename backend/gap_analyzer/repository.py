"""Phase 1 persistence seams for Gap Analyzer.

This file intentionally defines repository interfaces only. Query/read semantics
and concrete implementations land in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.schemas import GapRunMode
from backend.models import GapQuestion, GapQuestionMessageLink

logger = logging.getLogger(__name__)


class GapAnalyzerRepository(Protocol):
    """Phase 1 command-side persistence boundary."""

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
        ...

    def reweight_signal_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
        signal_weight: float,
    ) -> bool:
        ...

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> None:
        ...


@dataclass
class SqlAlchemyGapAnalyzerRepository:
    """Phase 2 command-side persistence implementation."""

    db: Session

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
        if signal.chat_id is None or signal.session_id is None:
            raise ValueError("GapSignal requires chat_id and session_id for Phase 2 ingestion")
        if signal.user_message_id is None or signal.assistant_message_id is None:
            raise ValueError(
                "GapSignal requires user_message_id and assistant_message_id for Phase 2 ingestion"
            )

        attempt_index = signal.attempt_index
        if attempt_index == 0:
            attempt_index = (
                self.db.query(GapQuestionMessageLink)
                .filter(
                    GapQuestionMessageLink.chat_id == signal.chat_id,
                    GapQuestionMessageLink.user_message_id == signal.user_message_id,
                )
                .count()
            )

        gap_question = GapQuestion(
            tenant_id=signal.tenant_id,
            question_text=signal.question_text,
            gap_signal_weight=signal_weight,
            answer_confidence=signal.answer_confidence,
            had_fallback=signal.had_fallback,
            had_escalation=signal.was_escalated,
            language=signal.language,
            created_at=signal.created_at,
        )
        self.db.add(gap_question)
        self.db.flush()

        self.db.add(
            GapQuestionMessageLink(
                gap_question_id=gap_question.id,
                user_message_id=signal.user_message_id,
                assistant_message_id=signal.assistant_message_id,
                chat_id=signal.chat_id,
                session_id=signal.session_id,
                attempt_index=attempt_index,
                created_at=signal.created_at,
            )
        )
        self.db.flush()

    def reweight_signal_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
        signal_weight: float,
    ) -> bool:
        matches = (
            self.db.query(GapQuestion)
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
            return False
        if len(matches) > 1:
            logger.warning(
                "gap_analyzer_multiple_signal_links_for_assistant_message: tenant_id=%s assistant_message_id=%s matches=%s",
                tenant_id,
                assistant_message_id,
                len(matches),
            )

        gap_question = matches[0]

        gap_question.gap_signal_weight = signal_weight
        self.db.add(gap_question)
        self.db.flush()
        return True

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> None:
        raise NotImplementedError("Async recalc orchestration lands in Phase 5")
