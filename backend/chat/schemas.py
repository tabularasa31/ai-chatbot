"""Pydantic schemas for chat API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ClarificationReason(str, Enum):
    ambiguous_intent = "ambiguous_intent"
    missing_critical_slot = "missing_critical_slot"
    low_retrieval_confidence = "low_retrieval_confidence"


class ClarificationType(str, Enum):
    disambiguation = "disambiguation"
    slot_request = "slot_request"
    context_request = "context_request"
    partial_plus_question = "partial_plus_question"


class ChatMessageType(str, Enum):
    answer = "answer"
    clarification = "clarification"
    partial_with_clarification = "partial_with_clarification"


class ClarificationOptionResponse(BaseModel):
    id: str
    label: str


class ClarificationPayloadResponse(BaseModel):
    reason: ClarificationReason
    type: ClarificationType
    options: list[ClarificationOptionResponse] = Field(default_factory=list)
    requested_fields: list[str] = Field(default_factory=list)
    original_user_message: str | None = None
    turn_index: int = 1


class MessageFeedbackValue(str, Enum):
    up = "up"
    down = "down"
    none = "none"


class MessageFeedbackRequest(BaseModel):
    feedback: MessageFeedbackValue
    ideal_answer: str | None = None


class MessageFeedbackResponse(BaseModel):
    id: UUID
    feedback: MessageFeedbackValue
    ideal_answer: str | None


class BadAnswerItem(BaseModel):
    message_id: UUID
    session_id: UUID
    question: str | None
    answer: str
    ideal_answer: str | None
    created_at: datetime


class BadAnswerListResponse(BaseModel):
    items: list[BadAnswerItem]


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    question: str = Field(
        ...,
        max_length=1000,
        description="User question",
    )
    session_id: UUID | None = Field(
        default=None,
        description="Optional session ID; auto-generated if not provided",
    )
    clarification_option_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional structured quick-reply option ID for clarification flows",
    )


class ChatResponse(BaseModel):
    """Response from chat endpoint."""

    text: str
    answer: str
    message_type: ChatMessageType = ChatMessageType.answer
    clarification: ClarificationPayloadResponse | None = None
    session_id: UUID
    source_documents: list[UUID]
    tokens_used: int
    validation: dict | None = None
    chat_ended: bool = False

    @model_validator(mode="after")
    def validate_clarification_payload(self) -> ChatResponse:
        if self.message_type != ChatMessageType.answer and self.clarification is None:
            raise ValueError("clarification payload is required when message_type is not answer")
        return self


class MessageResponse(BaseModel):
    """Single message in chat history."""

    id: UUID
    role: str
    content: str
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    """Chat history for a session."""

    session_id: UUID
    messages: list[MessageResponse]


# --- Inbox / logs DTOs ---


class ChatSessionSummaryResponse(BaseModel):
    """Summary of a chat session for inbox list."""

    session_id: UUID
    message_count: int
    last_question: str | None = None
    last_answer_preview: str | None = None
    last_activity: datetime


class ChatSessionListResponse(BaseModel):
    """List of chat sessions for a client."""

    sessions: list[ChatSessionSummaryResponse]


class ChatMessageLogItem(BaseModel):
    """Single message in chat logs (read-only)."""

    id: UUID
    session_id: UUID
    role: Literal["user", "assistant"]
    content: str
    content_original: str | None = None
    content_original_available: bool = False
    feedback: Literal["none", "up", "down"]
    ideal_answer: str | None
    created_at: datetime


class ChatMessageLogResponse(BaseModel):
    """Full message log for a session."""

    messages: list[ChatMessageLogItem]
