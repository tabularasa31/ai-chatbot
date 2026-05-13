"""Boundary event types for Gap Analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID


def _naive_utcnow() -> datetime:
    # Local helper mirroring ``backend/models/base._utcnow``. Kept inline to
    # preserve the architecture invariant that ``gap_analyzer/events.py`` is
    # a pure boundary module with no ``backend.*`` imports (see
    # ``tests/test_gap_analyzer_architecture.py``). The value flows into
    # ``GapQuestion.created_at`` (naive ``DateTime`` column) via
    # ``_repo/signals.py``; the ``before_flush`` listener in
    # ``models/base.py`` would also strip tzinfo at write time, but using
    # naive UTC from the source avoids accidental aware-vs-naive
    # comparisons elsewhere.
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class GapSignal:
    tenant_id: UUID
    question_text: str
    answer_confidence: float | None
    was_rejected: bool
    was_escalated: bool
    user_thumbed_down: bool
    had_fallback: bool = False
    chat_id: UUID | None = None
    session_id: UUID | None = None
    user_message_id: UUID | None = None
    assistant_message_id: UUID | None = None
    attempt_index: int = 0
    language: str | None = None
    created_at: datetime = field(default_factory=_naive_utcnow)
