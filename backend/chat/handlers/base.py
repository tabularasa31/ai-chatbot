"""Base types for chat pipeline handlers."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.chat.language import ResolvedLanguageContext
    from backend.models import Chat, Tenant
    from backend.observability import TraceHandle


@dataclass(frozen=True)
class ChatTurnOutcome:
    text: str
    document_ids: list[uuid.UUID]
    tokens_used: int
    chat_ended: bool
    ticket_number: str | None = None


@dataclass
class HandlerContext:
    """Inputs assembled at the top of process_chat_message before handler dispatch.

    Fields are intentionally narrow for PR 1/4 (only what GreetingHandler needs).
    Subsequent PRs will widen this dataclass as SmallTalk / RAG / Escalation
    handlers are migrated.
    """

    tenant_id: uuid.UUID
    chat: Chat
    tenant_row: Tenant | None
    question: str
    redacted_question: str
    question_text: str
    language_context: ResolvedLanguageContext
    api_key: str
    optional_entity_types: set[str] | None
    is_new_session: bool
    trace: TraceHandle | None
    db: Session


class PipelineHandler(ABC):
    """A pipeline-stage handler.

    HandlerRouter walks its registered handlers in order and dispatches the turn
    to the first one whose ``can_handle`` returns True.
    """

    @abstractmethod
    def can_handle(self, ctx: HandlerContext) -> bool: ...

    @abstractmethod
    def handle(self, ctx: HandlerContext) -> ChatTurnOutcome: ...
