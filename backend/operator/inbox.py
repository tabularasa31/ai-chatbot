"""Read side of the operator console: the queue and one conversation.

Nothing here is stored separately — "needs a human" is derived from the
chat's ``operator_state`` and its escalation tickets, exactly the way the
widget derives its own waiting/live state, so the console and the visitor
can never disagree about whether somebody is on the way.

The unit is the visitor's session, not the ``Chat`` row. A session spans
several chats once idle rotation kicks in, and the ticket that put a visitor
in the queue may sit on an older chat than the one they are writing in now.
So a row is a session, it points at the session's newest chat (the only one
that can be live and the one the widget is attached to), and its ticket is
looked up across every chat of the session. The thread view and the resolve
intent use the same rule, so what the queue shows is what resolving clears.

All DB work is sync, bridged from the async routes via ``run_sync`` like the
rest of the operator domain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import case, exists, func, or_, select
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


def _session_ids_where(tenant_id: uuid.UUID, predicate):
    return (
        select(Chat.session_id)
        .where(Chat.tenant_id == tenant_id, predicate)
        .distinct()
    )


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


def _newest_chats(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    session_ids=None,
    limit: int | None = None,
) -> list[Chat]:
    """The newest chat of each session, done in SQL with a window rank."""
    ranked = select(
        Chat.id.label("id"),
        Chat.created_at.label("created_at"),
        func.row_number()
        .over(partition_by=Chat.session_id, order_by=Chat.created_at.desc())
        .label("rn"),
    ).where(Chat.tenant_id == tenant_id)
    if session_ids is not None:
        ranked = ranked.where(Chat.session_id.in_(session_ids))
    ranked = ranked.subquery()
    q = (
        db.query(Chat)
        .join(ranked, ranked.c.id == Chat.id)
        .filter(ranked.c.rn == 1)
        .order_by(ranked.c.created_at.desc())
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def _tickets_by_session(
    db: Session, *, tenant_id: uuid.UUID, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, EscalationTicket]:
    """One ticket per session: the newest active one, else the newest of any.

    Looked up through the session's chats rather than ``tickets.session_id``,
    which older rows never had filled in.
    """
    if not session_ids:
        return {}
    active_first = case(
        (EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES), 0), else_=1
    )
    ranked = (
        select(
            EscalationTicket.id.label("id"),
            Chat.session_id.label("session_id"),
            func.row_number()
            .over(
                partition_by=Chat.session_id,
                order_by=(active_first, EscalationTicket.created_at.desc()),
            )
            .label("rn"),
        )
        .join(Chat, Chat.id == EscalationTicket.chat_id)
        .where(Chat.tenant_id == tenant_id, Chat.session_id.in_(session_ids))
        .subquery()
    )
    rows = (
        db.query(EscalationTicket, ranked.c.session_id)
        .join(ranked, ranked.c.id == EscalationTicket.id)
        .filter(ranked.c.rn == 1)
        .all()
    )
    return {session_id: ticket for ticket, session_id in rows}


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
        select(
            Message.id.label("id"),
            func.row_number()
            .over(
                partition_by=Message.chat_id,
                order_by=(Message.created_at.desc(), Message.id.desc()),
            )
            .label("rn"),
        )
        .where(Message.chat_id.in_(chat_ids))
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


def list_inbox(
    db: Session, *, tenant_id: uuid.UUID, scope: InboxScope, limit: int = 200
) -> list[InboxRow]:
    """The queue: one row per session, pointing at the session's newest chat.

    ``attention`` keeps only sessions that need a human — a chat of theirs is
    live, or a ticket of theirs is still active — ordered longest wait first,
    then whoever is being served, newest activity first. ``all`` is every
    conversation the tenant has, newest first, capped at ``limit`` because a
    tenant's history is unbounded and the console is a queue, not an archive.
    """
    if scope == "attention":
        chats = _newest_chats(
            db,
            tenant_id=tenant_id,
            session_ids=_session_ids_where(tenant_id, _needs_attention()),
        )
    else:
        chats = _newest_chats(db, tenant_id=tenant_id, limit=limit)

    tickets = _tickets_by_session(
        db, tenant_id=tenant_id, session_ids=[c.session_id for c in chats]
    )
    last = _last_messages(db, [c.id for c in chats])
    emails = _emails_by_user(
        db, {c.assigned_operator_id for c in chats if c.assigned_operator_id}
    )

    rows: list[InboxRow] = []
    for chat in chats:
        ticket = tickets.get(chat.session_id)
        state = handoff_state(chat, ticket)
        newest, count = last.get(chat.id, (None, 0))
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
                last_activity=newest.created_at if newest is not None else chat.created_at,
                message_count=count,
                visitor=visitor_of(chat, ticket),
            )
        )

    rows.sort(key=lambda r: r.last_activity, reverse=True)
    if scope == "attention":
        # Stable: within each group the activity order above is kept.
        rows.sort(key=lambda r: r.waiting_since or datetime.max)
        rows.sort(key=lambda r: 0 if r.handoff_state == "waiting" else 1)
    return rows


def inbox_counts(db: Session, *, tenant_id: uuid.UUID) -> InboxCounts:
    """How many sessions wait for a human, and how many need one at all.

    ``waiting`` is the sidebar badge: it drops to zero when every request has
    been picked up. ``attention`` is the size of the default queue. Both
    count sessions, like the queue does; a session with a live chat is being
    served whatever its older chats' tickets say.
    """
    live_sessions = _session_ids_where(tenant_id, Chat.operator_state == OperatorState.live)
    waiting = (
        db.query(func.count(func.distinct(Chat.session_id)))
        .filter(
            Chat.tenant_id == tenant_id,
            _active_ticket_exists(),
            Chat.session_id.not_in(live_sessions),
        )
        .scalar()
        or 0
    )
    attention = (
        db.query(func.count(func.distinct(Chat.session_id)))
        .filter(Chat.tenant_id == tenant_id, _needs_attention())
        .scalar()
        or 0
    )
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

    rows = (
        db.query(Message, User.email)
        .outerjoin(User, User.id == Message.operator_user_id)
        .filter(Message.chat_id.in_([c.id for c in chats]))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    messages = [
        ThreadMessage(message=m, author_label=email or m.operator_label)
        for m, email in rows
    ]

    ticket = _tickets_by_session(db, tenant_id=tenant_id, session_ids=[session_id]).get(
        session_id
    )
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
