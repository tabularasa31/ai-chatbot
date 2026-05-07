"""Pydantic schemas for chat API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.chat.llm_unavailable import LlmFailureState


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


class ChatTurnResponse(BaseModel):
    """Response for a single chat turn.

    Returned by `/chat` as JSON.
    """

    text: str
    session_id: UUID
    chat_ended: bool = False
    ticket_number: str | None = None
    # Trace fields — populated only by the private API; widget always omits these.
    source_documents: list[UUID] | None = None
    tokens_used: int | None = None


class WidgetChatTurnResponse(BaseModel):
    """Widget `done` event payload for `/widget/chat` SSE responses.

    ``outcome`` and ``failure_state`` are populated only for the degraded
    LLM-unavailable path. Old widgets that ignore them still render ``text``
    (backward-compat — AC5 of LLM Unavailable spec).
    """

    text: str
    session_id: UUID
    chat_ended: bool = False
    ticket_number: str | None = None
    outcome: Literal["llm_unavailable"] | None = None
    failure_state: LlmFailureState | None = None


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
