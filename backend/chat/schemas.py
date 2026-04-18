"""Pydantic schemas for chat API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


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


class ChatResponse(BaseModel):
    """Response from chat endpoint."""

    text: str
    session_id: UUID
    source_documents: list[UUID]
    tokens_used: int
    validation: dict | None = None
    chat_ended: bool = False


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
