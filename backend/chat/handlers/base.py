"""Base types for chat pipeline handlers."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

    from backend.chat.language import ResolvedLanguageContext
    from backend.models import Bot, Chat, Tenant, TenantProfile
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

    Greeting / SmallTalk handlers only read the core fields; Escalation /
    RagHandler also use the bot, disclosure, callback, and per-turn metadata
    fields populated below.
    """

    # Core — used by every handler
    tenant_id: uuid.UUID
    chat: Chat
    tenant_row: Tenant | None
    tenant_profile: TenantProfile | None
    question: str
    redacted_question: str
    question_text: str
    language_context: ResolvedLanguageContext
    api_key: str
    optional_entity_types: set[str] | None
    is_new_session: bool
    trace: TraceHandle | None

    # Sync session for handler use. Populated inside the handler's
    # ``await run_sync(ctx.async_db, ...)`` block; ``None`` outside that
    # window. Handlers that wrap their sync body in ``run_sync`` should set
    # ``ctx.db`` to the inner sync session at the start of the wrapped fn
    # so existing sync helpers continue to work unchanged.
    db: Session | None = None

    # Async session — populated by ``_async_dispatch`` for the lifetime of
    # the dispatch call. Handlers use it directly when calling native-async
    # helpers (e.g. ``async_match_faq``) or pass it to ``run_sync`` to bridge
    # sync persistence helpers.
    async_db: AsyncSession | None = None

    # Used by EscalationStateMachine + RagHandler
    session_id: uuid.UUID | None = None
    # The raw per-request user_context arg from process_chat_message — used as
    # the "identified on this turn" analytics signal. Distinct from
    # effective_user_ctx, which prefers the persisted chat.user_context (i.e.
    # carries identity from earlier turns and would inflate the metric).
    user_context: dict[str, Any] | None = None
    effective_user_ctx: dict[str, Any] | None = None
    bot_public_id: str | None = None

    # Used by RagHandler only
    bot_id: uuid.UUID | None = None
    bot: Bot | None = None
    bot_agent_instructions: str | None = None
    disclosure_config: dict[str, Any] | None = None
    allow_clarification: bool = True
    user_context_line: str | None = None
    stream_callback: Callable[[str], None] | None = None
    status_callback: Callable[[str], None] | None = None
    explicit_human_request: bool = False

    # Per-turn metrics
    turn_started_at: float = 0.0

    # Mutable scratch space for handlers — currently unused; reserved for future
    # cross-handler state (e.g. precomputed injection result threaded between
    # injection guard and async_run_chat_pipeline).
    extras: dict[str, Any] = field(default_factory=dict)


class PipelineHandler(ABC):
    """A pipeline-stage handler.

    HandlerRouter walks its registered handlers in order and dispatches the turn
    to the first one whose ``can_handle`` returns True. If ``handle`` returns
    ``None`` the router moves on to the next handler — used by handlers that
    opt-in optimistically (``can_handle`` is True) but discover at runtime that
    they cannot complete the turn (e.g. EscalationStateMachine T-3 path failing
    falls back to RagHandler).
    """

    @abstractmethod
    def can_handle(self, ctx: HandlerContext) -> bool: ...

    @abstractmethod
    async def handle(self, ctx: HandlerContext) -> ChatTurnOutcome | None: ...
