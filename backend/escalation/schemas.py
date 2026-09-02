"""Pydantic schemas for escalation API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ManualEscalateRequest(BaseModel):
    user_note: str | None = Field(default=None, max_length=2000)
    trigger: Literal["user_request", "answer_rejected", "llm_unavailable"] = "user_request"
    # Populated only when trigger == "llm_unavailable". Used to enrich the
    # ticket without requiring a DB migration: failure_type is prefixed into
    # user_note, and original_user_message becomes primary_question.
    failure_type: str | None = Field(default=None, max_length=64)
    original_user_message: str | None = Field(default=None, max_length=4000)


class ManualEscalateResponse(BaseModel):
    message: str
    ticket_number: str
