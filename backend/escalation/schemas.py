"""Pydantic schemas for escalation API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EscalationTicketOut(BaseModel):
    id: UUID
    ticket_number: str
    primary_question: str
    primary_question_original: str | None = None
    primary_question_original_available: bool = False
    conversation_summary: str | None = None
    trigger: str
    best_similarity_score: float | None = None
    retrieved_chunks_preview: list[dict[str, Any]] | None = None
    user_id: str | None = None
    user_email: str | None = None
    user_name: str | None = None
    plan_tier: str | None = None
    user_note: str | None = None
    priority: str
    status: str
    resolution_text: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    chat_id: UUID | None = None
    session_id: UUID | None = None

    model_config = {"from_attributes": True}


class EscalationListResponse(BaseModel):
    tickets: list[EscalationTicketOut]


class EscalationResolveRequest(BaseModel):
    resolution_text: str = Field(..., min_length=1, max_length=8000)


class ManualEscalateRequest(BaseModel):
    user_note: str | None = Field(default=None, max_length=2000)
    trigger: Literal["user_request", "answer_rejected"] = "user_request"


class ManualEscalateResponse(BaseModel):
    message: str
    ticket_number: str
