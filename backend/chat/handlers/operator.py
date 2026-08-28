"""Operator handler — the bot goes silent while a human holds the chat.

Registered first in ``default_router()``. When ``chat.operator_state`` is
``live`` the visitor's message is persisted and nothing is generated: no
retrieval, no LLM call, and no guard standing between the message and a
reply, because there is no reply. The operator is the one answering, and a
bot reply arriving alongside a human one is the failure mode this whole
feature exists to avoid.

One guard still runs, and gates nothing: the structural injection check, whose
verdict goes to ``guard_events`` so a visitor probing the bot mid-handoff is
visible in monitoring rather than silent for as long as the operator holds the
chat. See :func:`_monitor_injection`.

Release happens here, on the visitor's next message. An operator can answer
once and vanish — by e-mail, or by closing the console — and without a release
the bot would stay muted for good. Doing it at turn time removes every race
between a sweep and a live turn, and it costs the visitor nothing: the same
message that triggers the release is answered.

That covers every case *except* the chat nobody writes in again, which stays
pinned ``live`` with an assignee and an open ticket exempt from
``auto_close_stale_tickets`` forever. ``release_idle_operator_chats`` in
``backend/jobs/chat_session_sweeper.py`` is the backstop for exactly that
chat; both paths write the same columns via :func:`released_to_bot_values`
and read the same idleness rule via :func:`operator_is_idle`, so a chat
released by a sweep and one released by a turn are indistinguishable
afterwards.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler
from backend.core.config import settings
from backend.models import (
    Chat,
    Message,
    MessageRole,
    OperatorSessionEndReason,
    OperatorState,
)
from backend.models.base import _utcnow

if TYPE_CHECKING:
    from backend.operator.sessions import ClosedStretch

logger = logging.getLogger(__name__)


def last_operator_activity_at(db: Session, chat: Chat) -> datetime | None:
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


def released_to_bot_values() -> dict[str, object]:
    """The column values that constitute "handed back to the bot".

    A single definition shared by the ORM release below and the sweeper's
    bulk-UPDATE release (``backend/jobs/chat_session_sweeper.py``), which
    cannot go through the ORM because it must pin ``updated_at``. Two release
    paths writing two different shapes is exactly the drift this avoids.

    ``assigned_operator_id`` is cleared, not kept as a record of who held it:
    the transcript already carries authorship on ``messages.operator_user_id``,
    and leaving a stale assignee behind would both make an open ticket stop
    reading as "waiting for an operator" and permanently block the next
    ``/take`` (whose claim predicate is ``assigned_operator_id IS NULL``).
    """
    return {
        "operator_state": OperatorState.bot,
        "assigned_operator_id": None,
        # Naive UTC — the column is ``DateTime`` with no timezone and asyncpg
        # refuses aware values. See ``models/base._utcnow``.
        "operator_released_at": _utcnow(),
    }


def release_to_bot(
    db: Session, chat: Chat, *, reason: OperatorSessionEndReason
) -> ClosedStretch | None:
    """Hand the conversation back to the bot (ORM path) and close the stretch.

    ``reason`` is the caller's, because only the caller knows why: the
    explicit release button and this module's lazy release apply the same
    columns but mean different things to whoever reads the numbers later —
    a stretch an operator ended, versus one the visitor walked back into
    while the operator was silent.

    Everything is staged, nothing is committed: the released columns and the
    closed stretch belong to one transaction, so there is no instant in which
    the chat reads ``bot`` while its stretch is still open. Callers commit and
    then pass the return value to
    :func:`~backend.operator.sessions.emit_operator_session_ended`. ``None``
    means there was no open stretch to close and nothing to report.
    """
    from backend.operator.sessions import close_operator_session

    for column, value in released_to_bot_values().items():
        setattr(chat, column, value)
    db.add(chat)
    return close_operator_session(
        db, chat=chat, reason=reason, ended_at=chat.operator_released_at
    )


def operator_is_idle(db: Session, chat: Chat) -> bool:
    """True when the operator has been silent past the release threshold."""
    last_activity = last_operator_activity_at(db, chat)
    if last_activity is None:
        # ``live`` with no join stamp and no operator message at all — nothing
        # anchors the silence window, so treat it as stale rather than muting
        # the bot indefinitely on a half-written state.
        return True
    cutoff = _utcnow() - timedelta(seconds=settings.operator_release_idle_seconds)
    return last_activity < cutoff


def _monitor_injection(ctx: HandlerContext, chat: Chat) -> None:
    """Record a structural injection verdict for a message the bot swallowed.

    A turn the bot answers is measured by the guards on its way to the LLM. A
    turn a human answers reaches no LLM at all, so before this the guards
    subsystem simply had nothing to say about a handoff — a visitor probing the
    bot went unrecorded for as long as the operator held the chat, and there is
    no limit on how many messages that is.

    Only level 1 runs, and it changes nothing about the turn: the verdict is
    not read here and the message reaches the operator exactly as written. The
    row is written with ``blocked=False`` for that reason — a pattern hit here
    is a detection, not a block, and recording it as a block would put a
    message a human read into our own false-positive numbers. See
    :func:`~backend.guards.injection_detector.monitor_injection_structural` for
    why the semantic level stays out and why blocking is off the table.

    One row per swallowed turn, matching the ``guard_events`` contract of a row
    per guard invocation. Verdicts of ``ok`` are the denominator: without them a
    detection rate cannot be read off the table, and the handoff population
    would not be comparable with the ordinary chat path, which also records its
    passes.

    Best-effort like every other guard-event write — nothing here may cost the
    visitor their message. The lazy import is inside the ``try`` for that
    reason and not only to break the cycle: an import that can fail belongs
    where its failure is caught.
    """
    try:
        from backend.guards.injection_detector import monitor_injection_structural

        monitor_injection_structural(
            ctx.question,
            tenant_id=str(ctx.tenant_id),
            chat_id=str(chat.id),
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("operator handoff injection monitoring failed", exc_info=True)


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
        from backend.escalation.service import notify_support_of_visitor_turn
        from backend.operator.sessions import emit_operator_session_ended

        ctx.db = sync_db
        chat = ctx.chat

        if operator_is_idle(sync_db, chat):
            # ``visitor_returned``, not ``idle_timeout``: the operator went
            # quiet past the same window either way, but here the visitor is
            # still in the conversation and just got handed back to the bot.
            released = release_to_bot(
                sync_db, chat, reason=OperatorSessionEndReason.visitor_returned
            )
            sync_db.commit()
            # After the commit, and touching no database: this is the
            # visitor's live turn, and telemetry must not be able to fail a
            # release that has already succeeded.
            emit_operator_session_ended(released)
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
            _monitor_injection(ctx, chat)
            # The other half of the e-mail lane. An operator answering from
            # their mailbox has no console to watch, so the visitor's reply
            # has to be pushed to them; without it the handoff is one-way and
            # the operator waits for an answer that arrived somewhere they
            # cannot see. Runs after the persist (which commits), so a mail
            # failure cannot cost the visitor their message.
            notify_support_of_visitor_turn(chat.id, sync_db)

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
