"""Boundary event types for Gap Analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


@dataclass(frozen=True)
class GapSignal:
    tenant_id: UUID
    question_text: str
    answer_confidence: float
    was_rejected: bool
    was_escalated: bool
    user_thumbed_down: bool
    session_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
