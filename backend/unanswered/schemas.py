"""DTOs for the Knowledge "Unanswered" queue read and triage API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from backend.models import UnansweredDismissReason, UnansweredGroupStatus, UnansweredReason


class UnansweredGroupResponse(BaseModel):
    id: uuid.UUID
    status: UnansweredGroupStatus
    question: str
    question_message_id: uuid.UUID
    chat_id: uuid.UUID
    session_id: uuid.UUID
    conversations_30d: int
    last_asked_at: datetime
    reasons: list[UnansweredReason]
    reopened_after_answer: bool
    faq_id: uuid.UUID | None
    has_draft: bool
    generating: bool
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


QUESTION_MAX_LENGTH = 1000
ANSWER_MAX_LENGTH = 20000
INSTRUCTIONS_MAX_LENGTH = 2000


def _non_blank(max_length: int) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


Question = Annotated[str, _non_blank(QUESTION_MAX_LENGTH)]
Answer = Annotated[str, _non_blank(ANSWER_MAX_LENGTH)]
Instructions = Annotated[str, _non_blank(INSTRUCTIONS_MAX_LENGTH)]
DraftVersion = Annotated[int, Field(ge=0)]


class DraftResponse(BaseModel):
    question: str
    answer: str
    language: str
    version: int
    generating: bool


class DraftSaveRequest(BaseModel):
    question: Question
    answer: Answer
    version: DraftVersion


class DraftRefineRequest(BaseModel):
    instructions: Instructions
    version: DraftVersion


class PublishRequest(BaseModel):
    version: DraftVersion


class PublishResponse(BaseModel):
    group: UnansweredGroupResponse
    faq_id: uuid.UUID
