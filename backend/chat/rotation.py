"""Conversation rotation: decide when a returning session starts a new Chat.

A ``session_id`` identifies the visitor (widget localStorage, 24h sliding
TTL); a ``Chat`` row is one conversation. Historically the two were 1:1 and a
session's single Chat lived forever, leaking per-conversation state (history,
clarification budget, loop window, greeting, language lock) across visits.

Rotation is lazy: when a message arrives and the session's latest Chat has
been idle past ``settings.conversation_idle_timeout_seconds``, the caller
creates a fresh Chat with the same ``session_id``. The old row stays as an
archived conversation (dashboard history, analytics) and never receives new
messages. Idle is measured on ``Chat.updated_at`` — the same signal the
``chat_session_ended`` analytics sweeper uses, so the whole system shares one
definition of an ended conversation.

The one exception: a live escalation ticket still collecting the user's email
(``escalation_awaiting_ticket_id``) blocks rotation — abandoning it would
leave the ticket without contact info. The other escalation flags are mere
pending questions with no ticket behind them; they are abandoned with the old
row. All their readers are scoped to the current chat, so stale flags on an
archived row are inert.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, select

from backend.core.config import settings
from backend.models import Chat
from backend.models.base import _utcnow


def latest_chat_query(
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    bot_id: uuid.UUID | None = None,
):
    """Select the newest Chat for a session (a session may span several).

    With rotation a session accumulates one Chat per conversation; every
    lookup that used to assume a single row must take the latest instead.
    """
    stmt = select(Chat).where(
        Chat.tenant_id == tenant_id,
        Chat.session_id == session_id,
    )
    if bot_id is not None:
        stmt = stmt.where(or_(Chat.bot_id == bot_id, Chat.bot_id.is_(None)))
    return stmt.order_by(Chat.created_at.desc()).limit(1)


def should_rotate(chat: Chat, *, now: datetime | None = None) -> bool:
    """True when the next message must open a new conversation for the session.

    A chat idle past the shared threshold rotates — including chats closed by
    escalation (``ended_at`` set), so a returning visitor gets a fresh
    conversation instead of a "session closed" dead end. Within the window a
    closed chat keeps today's acknowledge_closed_or_start_new behavior.
    """
    if chat.escalation_awaiting_ticket_id is not None:
        return False
    reference = now or _utcnow()
    idle_cutoff = reference - timedelta(
        seconds=settings.conversation_idle_timeout_seconds
    )
    return chat.updated_at < idle_cutoff
