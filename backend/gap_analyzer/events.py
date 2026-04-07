"""Boundary event types for Gap Analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
