"""Operator handler — the bot goes silent while a human holds the chat.

Registered first in ``default_router()``. When ``chat.operator_state`` is
``live`` the visitor's message is persisted and nothing is generated: no
guards, no retrieval, no LLM call. The operator is the one answering, and a
bot reply arriving alongside a human one is the failure mode this whole
feature exists to avoid.

Release is lazy and happens here, on the visitor's next message, rather than
in a background sweep. An operator can answer once and vanish — by e-mail, or
by closing the console — and without a release the bot would stay muted for
good. Doing it at turn time keeps the async-only localization helpers on the
event loop where they belong (a sweeper thread would need an ``await_only``
bridge or a queued job for no gain) and removes every race between a sweep and
a live turn. The single uncovered case is a visitor who never writes again,
and then there is nobody to un-mute the bot for.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.core.config import settings
from backend.models import Chat, Message, MessageRole, OperatorState
from backend.models.base import _utcnow


def last_operator_activity_at(db: Session, chat: Chat):
    """When the operator was last active in this chat, or ``None``.

    The later of the moment they joined and their most recent message, so a
    freshly taken chat with no reply yet is not immediately stale.
    """
    last_message_at = (
        db.query(Message.created_at)
        .filter(
            Message.chat_id == chat.id,
            Message.role == MessageRole.operator,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar()
    )
    candidates = [ts for ts in (chat.operator_joined_at, last_message_at) if ts is not None]
    return max(candidates) if candidates else None


def release_to_bot(db: Session, chat: Chat) -> None:
    """Hand the conversation back to the bot.

    ``assigned_operator_id`` is cleared, not kept as a record of who held it:
    the transcript already carries authorship on ``messages.operator_user_id``,
    and leaving a stale assignee behind would both make an open ticket stop
    reading as "waiting for an operator" and permanently block the next
    ``/take`` (whose claim predicate is ``assigned_operator_id IS NULL``).
    """
    chat.operator_state = OperatorState.bot
    chat.assigned_operator_id = None
    # Naive UTC — the column is ``DateTime`` with no timezone and asyncpg
    # refuses aware values. See ``models/base._utcnow``.
    chat.operator_released_at = _utcnow()
    db.add(chat)


def _is_stale(db: Session, chat: Chat) -> bool:
    """True when the operator has been silent past the release threshold."""
    last_activity = last_operator_activity_at(db, chat)
    if last_activity is None:
        # ``live`` with no join stamp and no operator message at all — nothing
        # anchors the silence window, so treat it as stale rather than muting
        # the bot indefinitely on a half-written state.
        return True
    cutoff = _utcnow() - timedelta(seconds=settings.operator_release_idle_seconds)
    return last_activity < cutoff


class OperatorHandler(PipelineHandler):
    """Mutes the bot for the duration of a live operator handoff.

    ``can_handle`` claims every turn of a ``live`` chat. ``handle`` then either
    records the visitor's message and returns a reply-less outcome, or — when
    the operator has gone quiet past ``OPERATOR_RELEASE_IDLE_SECONDS`` —
    releases the chat and returns ``None`` so the router falls through and the
    bot answers this very turn. Returning ``None`` to opt out at runtime is the
    router's documented contract, and it is what keeps the release from
    costing the visitor a turn.
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        return ctx.chat.operator_state is OperatorState.live

    async def handle(self, ctx: HandlerContext) -> ChatTurnOutcome | None:
        from backend.core.db import run_sync

        return await run_sync(ctx.async_db, lambda sync_db: self._handle_sync(ctx, sync_db))

    def _handle_sync(self, ctx: HandlerContext, sync_db: Session) -> ChatTurnOutcome | None:
        # Lazy import: service.py imports the router at module load, so
        # importing the persistence helpers at module top would cycle.
        from backend.chat.service import _persist_user_only_turn

        ctx.db = sync_db
        chat = ctx.chat

        if _is_stale(sync_db, chat):
            release_to_bot(sync_db, chat)
            sync_db.commit()
            if ctx.trace is not None:
                ctx.trace.update(
                    metadata={
                        "operator_state": OperatorState.bot.value,
                        "operator_released": True,
                        "operator_release_reason": "idle",
                    },
                )
            # Fall through: the next handler answers this turn, so the visitor
            # is not made to send a second message to wake the bot up.
            return None

        # Empty bootstrap turns carry no visitor message to hand on. Claim the
        # turn anyway — the bot must stay muted — but persist nothing.
        if ctx.question_text:
            _persist_user_only_turn(
                sync_db,
                chat=chat,
                tenant_id=ctx.tenant_id,
                user_content=ctx.question,
                optional_entity_types=ctx.optional_entity_types,
            )

        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": "", "source": "operator_live"},
                metadata={
                    "chat_ended": False,
                    "escalated": False,
                    "operator_state": OperatorState.live.value,
                    "delivered_to_operator": True,
                },
            )
        return ChatTurnOutcome(
            text="",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
            delivered_to_operator=True,
            chat_id=str(chat.id),
        )
