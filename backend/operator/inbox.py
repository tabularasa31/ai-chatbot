"""Read side of the operator console: the queue and one conversation.

Nothing here is stored separately — "needs a human" is derived from the
chat's ``operator_state`` and its escalation tickets, exactly the way the
widget derives its own waiting/live state, so the console and the visitor
can never disagree about whether somebody is on the way.

Rows are sessions, not chats. A visitor's session spans several ``Chat``
rows once idle rotation kicks in, and an operator thinks in visitors, not in
rotation boundaries: the row points at the chat that needs attention (or the
newest one), and the thread view renders every chat of the session with a
divider where one ended and the next began.

All DB work is sync, bridged from the async routes via ``run_sync`` like the
rest of the operator domain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session

from backend.escalation.service import ACTIVE_TICKET_STATUSES
from backend.models import Chat, EscalationTicket, Message, OperatorState, User

InboxScope = Literal["attention", "all"]
HandoffState = Literal["waiting", "live", "bot"]

PREVIEW_MAX_LEN = 140


@dataclass(frozen=True)
class Visitor:
    email: str | None
    name: str | None


@dataclass(frozen=True)
class InboxRow:
    session_id: uuid.UUID
    chat_id: uuid.UUID
    handoff_state: HandoffState
    ticket: EscalationTicket | None
    assigned_operator_id: uuid.UUID | None
    assigned_operator_email: str | None
    waiting_since: datetime | None
    last_message_role: str | None
    last_message_preview: str | None
    last_activity: datetime
    message_count: int
    visitor: Visitor


@dataclass(frozen=True)
class InboxCounts:
    waiting: int
    attention: int


@dataclass(frozen=True)
class ThreadMessage:
    message: Message
    author_label: str | None


@dataclass(frozen=True)
class Thread:
    session_id: uuid.UUID
    chat: Chat
    handoff_state: HandoffState
    ticket: EscalationTicket | None
    assigned_operator_email: str | None
    visitor: Visitor
    messages: list[ThreadMessage]


def _active_ticket_exists():
    return exists().where(
        EscalationTicket.chat_id == Chat.id,
        EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
    )


def _needs_attention():
    return or_(Chat.operator_state == OperatorState.live, _active_ticket_exists())


def _is_waiting():
    return (Chat.operator_state != OperatorState.live) & _active_ticket_exists()


def handoff_state(chat: Chat, ticket: EscalationTicket | None) -> HandoffState:
    if chat.operator_state is OperatorState.live:
        return "live"
    if ticket is not None and ticket.status in ACTIVE_TICKET_STATUSES:
        return "waiting"
    return "bot"


def visitor_of(chat: Chat, ticket: EscalationTicket | None) -> Visitor:
    """Who the visitor is, as best the data says.

    The ticket wins because it holds what the visitor typed when asked for
    contact details; the identified-session context is the fallback.
    """
    ctx = chat.user_context if isinstance(chat.user_context, dict) else {}
    email = (ticket.user_email if ticket else None) or ctx.get("email")
    name = (ticket.user_name if ticket else None) or ctx.get("name")
    return Visitor(email=email or None, name=name or None)


def _tickets_by_chat(
    db: Session, chat_ids: list[uuid.UUID]
) -> dict[uuid.UUID, EscalationTicket]:
    """One ticket per chat: the newest active one, else the newest of any."""
    if not chat_ids:
        return {}
    rows = (
        db.query(EscalationTicket)
        .filter(EscalationTicket.chat_id.in_(chat_ids))
        .order_by(EscalationTicket.created_at.desc())
        .all()
    )
    chosen: dict[uuid.UUID, EscalationTicket] = {}
    for ticket in rows:
        if ticket.status in ACTIVE_TICKET_STATUSES:
            chosen.setdefault(ticket.chat_id, ticket)
    for ticket in rows:
        chosen.setdefault(ticket.chat_id, ticket)
    return chosen


def _emails_by_user(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    return dict(db.query(User.id, User.email).filter(User.id.in_(user_ids)).all())


def _last_messages(
    db: Session, chat_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[Message, int]]:
    """The newest message of each chat and the chat's message count."""
    if not chat_ids:
        return {}
    counts = dict(
        db.query(Message.chat_id, func.count(Message.id))
        .filter(Message.chat_id.in_(chat_ids))
        .group_by(Message.chat_id)
        .all()
    )
    ranked = (
        db.query(
            Message.id.label("id"),
            func.row_number()
            .over(
                partition_by=Message.chat_id,
                order_by=(Message.created_at.desc(), Message.id.desc()),
            )
            .label("rn"),
        )
        .filter(Message.chat_id.in_(chat_ids))
        .subquery()
    )
    newest = (
        db.query(Message)
        .join(ranked, ranked.c.id == Message.id)
        .filter(ranked.c.rn == 1)
        .all()
    )
    return {m.chat_id: (m, counts.get(m.chat_id, 0)) for m in newest}


def _preview(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > PREVIEW_MAX_LEN:
        return text[:PREVIEW_MAX_LEN].rstrip() + "…"
    return text


def list_inbox(db: Session, *, tenant_id: uuid.UUID, scope: InboxScope) -> list[InboxRow]:
    """The queue: one row per session, pointing at the chat that matters.

    ``attention`` keeps only chats that need a human — waiting on a ticket or
    being served live — ordered longest wait first, then whoever is being
    served, newest activity first. ``all`` is every conversation the tenant
    has, newest activity first, each still carrying its handoff state so a
    conversation the bot is handling reads as such.
    """
    q = db.query(Chat).filter(Chat.tenant_id == tenant_id)
    if scope == "attention":
        q = q.filter(_needs_attention())
    chats = q.order_by(Chat.session_id, Chat.created_at.desc()).all()

    per_session: dict[uuid.UUID, Chat] = {}
    for chat in chats:
        per_session.setdefault(chat.session_id, chat)
    chosen = list(per_session.values())
    chat_ids = [c.id for c in chosen]

    tickets = _tickets_by_chat(db, chat_ids)
    last = _last_messages(db, chat_ids)
    emails = _emails_by_user(
        db, {c.assigned_operator_id for c in chosen if c.assigned_operator_id}
    )

    rows: list[InboxRow] = []
    for chat in chosen:
        ticket = tickets.get(chat.id)
        state = handoff_state(chat, ticket)
        newest, count = last.get(chat.id, (None, 0))
        last_activity = newest.created_at if newest is not None else chat.created_at
        rows.append(
            InboxRow(
                session_id=chat.session_id,
                chat_id=chat.id,
                handoff_state=state,
                ticket=ticket,
                assigned_operator_id=chat.assigned_operator_id,
                assigned_operator_email=emails.get(chat.assigned_operator_id),
                waiting_since=ticket.created_at if state == "waiting" and ticket else None,
                last_message_role=newest.role.value if newest is not None else None,
                last_message_preview=_preview(newest.content) if newest is not None else None,
                last_activity=last_activity,
                message_count=count,
                visitor=visitor_of(chat, ticket),
            )
        )

    if scope == "attention":
        # Waiting first, the longest wait on top; then the chats somebody is
        # already serving, most recently active first.
        rows.sort(
            key=lambda r: (
                0 if r.handoff_state == "waiting" else 1,
                r.waiting_since or datetime.max
                if r.handoff_state == "waiting"
                else -r.last_activity.timestamp(),
            )
        )
    else:
        rows.sort(key=lambda r: r.last_activity, reverse=True)
    return rows


def inbox_counts(db: Session, *, tenant_id: uuid.UUID) -> InboxCounts:
    """How many chats wait for a human, and how many need one at all.

    ``waiting`` is the sidebar badge: it drops to zero when every request has
    been picked up. ``attention`` is the size of the default queue.
    """
    base = db.query(func.count(Chat.id)).filter(Chat.tenant_id == tenant_id)
    waiting = base.filter(_is_waiting()).scalar() or 0
    attention = base.filter(_needs_attention()).scalar() or 0
    return InboxCounts(waiting=waiting, attention=attention)


def load_thread(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> Thread | None:
    """One visitor's whole session, every chat of it, oldest first.

    The operator actions apply to the newest chat: it is the only one that
    can be live, and the one the visitor's widget is attached to. ``None``
    when the session is not this tenant's — indistinguishable from one that
    does not exist.
    """
    chats = (
        db.query(Chat)
        .filter(Chat.session_id == session_id, Chat.tenant_id == tenant_id)
        .order_by(Chat.created_at.asc())
        .all()
    )
    if not chats:
        return None
    current = chats[-1]
    chat_ids = [c.id for c in chats]

    rows = (
        db.query(Message, User.email)
        .outerjoin(User, User.id == Message.operator_user_id)
        .filter(Message.chat_id.in_(chat_ids))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    messages = [
        ThreadMessage(message=m, author_label=email or m.operator_label)
        for m, email in rows
    ]

    ticket = _tickets_by_chat(db, [current.id]).get(current.id)
    emails = _emails_by_user(
        db, {current.assigned_operator_id} if current.assigned_operator_id else set()
    )
    return Thread(
        session_id=session_id,
        chat=current,
        handoff_state=handoff_state(current, ticket),
        ticket=ticket,
        assigned_operator_email=emails.get(current.assigned_operator_id),
        visitor=visitor_of(current, ticket),
        messages=messages,
    )
