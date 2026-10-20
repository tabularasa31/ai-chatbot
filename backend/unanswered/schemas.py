"""DTOs for the Knowledge "Unanswered" queue read and triage API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from backend.models import UnansweredDismissReason, UnansweredGroupStatus, UnansweredReason


class UnansweredGroupResponse(BaseModel):
    id: uuid.UUID
    status: UnansweredGroupStatus
    question: str
    question_message_id: uuid.UUID
    chat_id: uuid.UUID
    conversations_30d: int
    last_asked_at: datetime
    reasons: list[UnansweredReason]
    reopened_after_answer: bool
    faq_id: uuid.UUID | None
    has_draft: bool
    dismiss_reason: UnansweredDismissReason | None


class UnansweredListResponse(BaseModel):
    items: list[UnansweredGroupResponse]
    next_offset: int | None
    tenant_conversations_30d: int
    tenant_has_conversations: bool


class UnansweredBadgeResponse(BaseModel):
    new_count: int


class UnansweredDismissRequest(BaseModel):
    reason: UnansweredDismissReason
