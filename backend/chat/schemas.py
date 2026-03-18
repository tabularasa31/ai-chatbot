"""Pydantic schemas for chat API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for chat endpoint."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="User question",
    )
    session_id: Optional[UUID] = Field(
        default=None,
        description="Optional session ID; auto-generated if not provided",
    )


class ChatResponse(BaseModel):
    """Response from chat endpoint."""

    answer: str
    session_id: UUID
    source_documents: list[UUID]
    tokens_used: int


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
