"""Pydantic schemas for chat API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from backend.chat.llm_unavailable import LlmFailureState


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
    bot_public_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Optional bot to address. If omitted (or empty), the tenant's "
            "default bot is used. Must belong to the authenticated tenant "
            "and be active."
        ),
    )

    @field_validator("bot_public_id", mode="before")
    @classmethod
    def _blank_bot_public_id_means_default(cls, value: object) -> object:
        # JS form clients commonly send "" for missing inputs; treat that as
        # "omitted" rather than letting it fall through to a guaranteed 404.
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ChatTurnResponse(BaseModel):
    """Response for a single chat turn.

    Returned by `/chat` as JSON.

    ``delivered_to_operator`` marks the muted path, exactly as on
    ``WidgetChatTurnResponse``: a human operator holds the chat, so the
    message was recorded and handed on and ``text`` is empty by design.
    Without it this contour — the X-API-Key one, used by custom server-side
    integrations — has no way to tell "a human is handling this" from "the
    turn broke", since both look like ``{"text": "", ...}``. Defaults False,
    so existing callers are unaffected.
    """

    text: str
    session_id: UUID
    ticket_number: str | None = None
    delivered_to_operator: bool = False
    # Trace fields — populated only by the private API; widget always omits these.
    source_documents: list[UUID] | None = None
    tokens_used: int | None = None


class WidgetChatTurnResponse(BaseModel):
    """Widget `done` event payload for `/widget/chat` SSE responses.

    ``outcome`` and ``failure_state`` are populated only for the degraded
    LLM-unavailable path. Old widgets that ignore them still render ``text``
    (backward-compat — AC5 of LLM Unavailable spec).

    ``delivered_to_operator`` marks the muted path: a human operator holds the
    chat, so the visitor's message was recorded and handed on and ``text`` is
    empty by design. Old widgets that ignore the flag see an empty ``text``
    and render nothing, which is the correct behaviour anyway.
    """

    text: str
    session_id: UUID
    ticket_number: str | None = None
    outcome: Literal["llm_unavailable"] | None = None
    failure_state: LlmFailureState | None = None
    delivered_to_operator: bool = False
    #: True when this reply offered to hand the conversation to support and is
    #: waiting for the visitor's yes/no. Read off the pre-confirm gate, so it is
    #: the same truth on every path and in every language — no phrase matching.
    escalation_offered: bool = False


