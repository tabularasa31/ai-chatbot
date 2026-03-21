"""Pydantic schemas for escalation API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EscalationTicketOut(BaseModel):
    id: UUID
    ticket_number: str
    primary_question: str
    conversation_summary: Optional[str] = None
    trigger: str
    best_similarity_score: Optional[float] = None
    retrieved_chunks_preview: Optional[list[dict[str, Any]]] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    plan_tier: Optional[str] = None
    user_note: Optional[str] = None
    priority: str
    status: str
    resolution_text: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    chat_id: Optional[UUID] = None
    session_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


class EscalationListResponse(BaseModel):
    tickets: list[EscalationTicketOut]


class EscalationResolveRequest(BaseModel):
    resolution_text: str = Field(..., min_length=1, max_length=8000)


class ManualEscalateRequest(BaseModel):
    user_note: Optional[str] = Field(default=None, max_length=2000)
    trigger: Literal["user_request", "answer_rejected"] = "user_request"


class ManualEscalateResponse(BaseModel):
    message: str
    ticket_number: str
