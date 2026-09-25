"""Pydantic schemas for chat API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from backend.chat.llm_unavailable import LlmFailureState


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


