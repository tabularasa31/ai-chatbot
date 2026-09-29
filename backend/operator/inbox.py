"""Read side of the operator console: the queue and one conversation.

Nothing here is stored separately — "needs a human" is derived from the
chat's ``operator_state``, its escalation tickets and the transcript. A
visitor waits for a human while a ticket of theirs is active and nobody has
answered since that ticket was (last) raised — no operator wrote in any of
their sessions and no reply was forwarded to them by mail; once someone
answered, the ticket may stay ``in_progress`` (the request is not closed) but
the conversation is no longer in anyone's queue — until the visitor asks for
a human again, which stamps ``requested_again_at`` and starts a new wait. The
widget asks a different question — "might a human still answer here?" — so
it keeps polling on any active ticket; only the console narrows to "has
nobody answered yet".

The unit is the visitor, not the ``Chat`` row nor the widget session. A
session spans several chats once idle rotation kicks in, and one visitor
opens several sessions — another device, cleared storage, a second request
days later. Sessions fold together when they share the tenant's own user id
(:func:`visitor_key`); every other session is its own row. A row points at
the visitor's newest chat, the one their widget is attached to, and its
ticket is looked up across every chat of every session. The thread view and
the resolve intent use the same rule, so what the queue shows is what
resolving clears.

All DB work is sync, bridged from the async routes via ``run_sync`` like the
rest of the operator domain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Literal

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from backend.escalation.service import ACTIVE_TICKET_STATUSES
from backend.models import (
    Chat,
    EscalationTicket,
    Message,
    MessageRole,
    OperatorState,
    Tenant,
    User,
)

InboxScope = Literal["attention", "all"]
HandoffState = Literal["waiting", "live", "bot"]

PREVIEW_MAX_LEN = 140
HINT_USER_ID_PREFIX = "hint:"


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
    session_ids: list[uuid.UUID]
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


def _waiting_session_ids(tenant_id: uuid.UUID):
    """Sessions with an active ticket nobody has answered within the session.

    "Answered" is an operator turn anywhere in the session written after the
    request was last raised, or a reply forwarded to the visitor by mail
    since then. A claim with no reply behind it does not count. This is the
    SQL prefilter for the queue: an answer in another session of the same
    visitor also counts, which :class:`_VisitorSessions` applies on top.
    Mirrors :func:`backend.escalation.service.operator_answered_since_request`.
    """
    ticket_chat = aliased(Chat)
    answer_chat = aliased(Chat)
    raised_at = func.coalesce(
        EscalationTicket.requested_again_at, EscalationTicket.created_at
    )
    answered_in_thread = exists().where(
        answer_chat.session_id == ticket_chat.session_id,
        Message.chat_id == answer_chat.id,
        Message.role == MessageRole.operator,
        Message.created_at >= raised_at,
    )
    not_answered_by_mail = or_(
        EscalationTicket.forwarded_reply_at.is_(None),
        EscalationTicket.forwarded_reply_at < raised_at,
    )
    q = (
        select(ticket_chat.session_id)
        .join(EscalationTicket, EscalationTicket.chat_id == ticket_chat.id)
        .where(
            ticket_chat.tenant_id == tenant_id,
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
            ~answered_in_thread,
            not_answered_by_mail,
        )
        .distinct()
    )
    return q


def _needs_attention(tenant_id: uuid.UUID):
    return or_(
        Chat.operator_state == OperatorState.live,
        Chat.session_id.in_(_waiting_session_ids(tenant_id)),
    )


def _has_messages():
    return exists().where(Message.chat_id == Chat.id)


def _session_ids_where(tenant_id: uuid.UUID, predicate):
    return (
        select(Chat.session_id)
        .where(Chat.tenant_id == tenant_id, predicate)
        .distinct()
    )


def handoff_state(chat: Chat, *, waiting: bool) -> HandoffState:
    if chat.operator_state is OperatorState.live:
        return "live"
    if waiting:
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


def visitor_key(chat: Chat, *, strict: bool) -> str | None:
    """The tenant's own id for the visitor, when their site passed one.

    Sessions sharing it are one visitor. An e-mail never folds sessions —
    neither one typed into a ticket nor one passed as a hint, which the widget
    turns into a ``hint:`` id: anyone can claim an address on the public
    widget, and folding would route an operator's reply for one person into a
    stranger's chat. For a tenant holding a signing secret (``strict``), the
    same risk applies to an unverified user id — it folds only once the
    widget has proven it with a signature.
    """
    ctx = chat.user_context if isinstance(chat.user_context, dict) else {}
    user_id = ctx.get("user_id")
    if not isinstance(user_id, str) or not user_id or user_id.startswith(HINT_USER_ID_PREFIX):
        return None
    if strict and ctx.get("identity_verified") is not True:
        return None
    return user_id


def _is_strict_tenant(db: Session, *, tenant_id: uuid.UUID) -> bool:
    """Whether the tenant holds a widget identity secret (signed user ids)."""
    return bool(
        db.query(exists().where(Tenant.id == tenant_id, Tenant.widget_identity_secret.isnot(None))).scalar()
    )


def _matches(query: str):
    """Chats whose visitor name, e-mail or ticket number contains ``query``."""
    return or_(
        Chat.user_context["name"].as_string().icontains(query, autoescape=True),
        Chat.user_context["email"].as_string().icontains(query, autoescape=True),
        exists().where(
            EscalationTicket.chat_id == Chat.id,
            or_(
                EscalationTicket.ticket_number.icontains(query, autoescape=True),
                EscalationTicket.user_email.icontains(query, autoescape=True),
                EscalationTicket.user_name.icontains(query, autoescape=True),
            ),
        ),
    )


@dataclass(frozen=True)
class _Request:
    raised_at: datetime
    forwarded_reply_at: datetime | None


@dataclass(frozen=True)
class _SessionState:
    chat: Chat
    ticket: EscalationTicket | None
    active_requests: list[_Request]
    last_operator_turn: datetime | None


@dataclass(frozen=True)
class _VisitorSessions:
    """One visitor's sessions, newest first."""

    sessions: list[_SessionState]

    @property
    def current(self) -> Chat:
        return self.sessions[0].chat

    @property
    def session_ids(self) -> list[uuid.UUID]:
        return [s.chat.session_id for s in self.sessions]

    @cached_property
    def unanswered_since(self) -> list[datetime]:
        """When each active request nobody has answered yet was raised.

        An operator turn in any of the visitor's sessions answers every
        request raised before it: the console replies in the newest chat
        whichever session the request came from.
        """
        answered_at = max(
            (s.last_operator_turn for s in self.sessions if s.last_operator_turn),
            default=None,
        )
        return [
            r.raised_at
            for s in self.sessions
            for r in s.active_requests
            if (answered_at is None or answered_at < r.raised_at)
            and (r.forwarded_reply_at is None or r.forwarded_reply_at < r.raised_at)
        ]

    @property
    def ticket(self) -> EscalationTicket | None:
        tickets = sorted(
            (s.ticket for s in self.sessions if s.ticket is not None),
            key=lambda t: t.created_at,
            reverse=True,
        )
        if not tickets:
            return None
        return min(tickets, key=lambda t: t.status not in ACTIVE_TICKET_STATUSES)

    @property
    def handoff_state(self) -> HandoffState:
        return handoff_state(self.current, waiting=bool(self.unanswered_since))

    @property
    def waiting_since(self) -> datetime | None:
        if self.handoff_state != "waiting":
            return None
        return min(self.unanswered_since)

    @property
    def visitor(self) -> Visitor:
        known = [visitor_of(s.chat, s.ticket) for s in self.sessions]
        return Visitor(
            email=next((v.email for v in known if v.email), None),
            name=next((v.name for v in known if v.name), None),
        )


def _active_requests(
    db: Session, *, tenant_id: uuid.UUID, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[_Request]]:
    rows = (
        db.query(
            Chat.session_id,
            func.coalesce(EscalationTicket.requested_again_at, EscalationTicket.created_at),
            EscalationTicket.forwarded_reply_at,
        )
        .join(Chat, Chat.id == EscalationTicket.chat_id)
        .filter(
            Chat.tenant_id == tenant_id,
            Chat.session_id.in_(session_ids),
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
        )
        .all()
    )
    requests: dict[uuid.UUID, list[_Request]] = {}
    for session_id, raised_at, forwarded_at in rows:
        requests.setdefault(session_id, []).append(_Request(raised_at, forwarded_at))
    return requests


def _last_operator_turns(
    db: Session, *, tenant_id: uuid.UUID, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    return dict(
        db.query(Chat.session_id, func.max(Message.created_at))
        .join(Message, Message.chat_id == Chat.id)
        .filter(
            Chat.tenant_id == tenant_id,
            Chat.session_id.in_(session_ids),
            Message.role == MessageRole.operator,
        )
        .group_by(Chat.session_id)
        .all()
    )


def _session_states(
    db: Session, *, tenant_id: uuid.UUID, chats: list[Chat]
) -> list[_SessionState]:
    """``chats`` are the newest chat of each session."""
    if not chats:
        return []
    session_ids = [c.session_id for c in chats]
    tickets = _tickets_by_session(db, tenant_id=tenant_id, session_ids=session_ids)
    requests = _active_requests(db, tenant_id=tenant_id, session_ids=session_ids)
    answers = _last_operator_turns(db, tenant_id=tenant_id, session_ids=session_ids)
    return [
        _SessionState(
            chat=c,
            ticket=tickets.get(c.session_id),
            active_requests=requests.get(c.session_id, []),
            last_operator_turn=answers.get(c.session_id),
        )
        for c in chats
    ]


def _visitors_of(
    db: Session, *, tenant_id: uuid.UUID, chats: list[Chat]
) -> list[_VisitorSessions]:
    """Group sessions by visitor, pulling in the visitor's sessions outside ``chats``."""
    strict = _is_strict_tenant(db, tenant_id=tenant_id)
    keys = {key for c in chats if (key := visitor_key(c, strict=strict))}
    if keys:
        seen = {c.session_id for c in chats}
        id_matches = Chat.user_context["user_id"].as_string().in_(keys)
        if strict:
            id_matches = and_(
                id_matches, Chat.user_context["identity_verified"].as_boolean().is_(True)
            )
        same_visitor = _session_ids_where(tenant_id, id_matches)
        chats = chats + [
            c
            for c in _newest_chats(db, tenant_id=tenant_id, session_ids=same_visitor)
            if c.session_id not in seen
        ]
    groups: dict[object, list[_SessionState]] = {}
    for state in _session_states(db, tenant_id=tenant_id, chats=chats):
        groups.setdefault(
            visitor_key(state.chat, strict=strict) or state.chat.session_id, []
        ).append(state)
    return [
        _VisitorSessions(sorted(g, key=lambda s: s.chat.created_at, reverse=True))
        for g in groups.values()
    ]


def _visitor_of_session(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> _VisitorSessions | None:
    own = _newest_chats(db, tenant_id=tenant_id, session_ids=[session_id])
    if not own:
        return None
    return _visitors_of(db, tenant_id=tenant_id, chats=own)[0]


def visitor_session_ids(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> list[uuid.UUID]:
    """Every session of the visitor who owns ``session_id``, newest first.

    Empty when the session is not this tenant's.
    """
    visitor = _visitor_of_session(db, tenant_id=tenant_id, session_id=session_id)
    return visitor.session_ids if visitor else []


def list_inbox(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    scope: InboxScope,
    query: str | None = None,
    limit: int = 200,
) -> list[InboxRow]:
    """The queue: one row per visitor, pointing at their newest chat.

    ``attention`` keeps only visitors who need a human — a chat of theirs is
    live, or a ticket of theirs is active and nobody has answered it yet —
    ordered longest wait first, then whoever is being served, newest
    activity first. ``all`` is every conversation the tenant has, newest
    first, capped at ``limit`` sessions because a tenant's history is
    unbounded and the console is a queue, not an archive.
    Sessions without a single message are left out of ``all`` unless they
    need a human: a mount the visitor never typed into is not a conversation
    anyone can act on, and ``all`` must still contain the whole queue.
    ``query`` narrows either scope to visitors whose name, e-mail or ticket
    number contains it. A row always covers every session of its visitor,
    including the ones the filter or the cap left out.
    """
    if scope == "attention":
        predicate = _needs_attention(tenant_id)
    else:
        predicate = or_(_has_messages(), _needs_attention(tenant_id))
    if query:
        predicate = and_(
            predicate, Chat.session_id.in_(_session_ids_where(tenant_id, _matches(query)))
        )
    chats = _newest_chats(
        db,
        tenant_id=tenant_id,
        session_ids=_session_ids_where(tenant_id, predicate),
        limit=None if scope == "attention" else limit,
    )
    visitors = _visitors_of(db, tenant_id=tenant_id, chats=chats)
    if scope == "attention":
        visitors = [v for v in visitors if v.handoff_state != "bot"]

    last = _last_messages(db, [s.chat.id for v in visitors for s in v.sessions])
    emails = _emails_by_user(
        db, {v.current.assigned_operator_id for v in visitors if v.current.assigned_operator_id}
    )

    rows: list[InboxRow] = []
    for v in visitors:
        current = v.current
        ticket = v.ticket
        per_chat = [last[s.chat.id] for s in v.sessions if s.chat.id in last]
        newest = max((m for m, _ in per_chat), key=lambda m: m.created_at, default=None)
        rows.append(
            InboxRow(
                session_id=current.session_id,
                chat_id=current.id,
                handoff_state=v.handoff_state,
                ticket=ticket,
                assigned_operator_id=current.assigned_operator_id,
                assigned_operator_email=emails.get(current.assigned_operator_id),
                waiting_since=v.waiting_since,
                last_message_role=newest.role.value if newest is not None else None,
                last_message_preview=_preview(newest.content) if newest is not None else None,
                last_activity=newest.created_at if newest is not None else current.created_at,
                message_count=sum(count for _, count in per_chat),
                session_ids=v.session_ids,
                visitor=v.visitor,
            )
        )

    rows.sort(key=lambda r: r.last_activity, reverse=True)
    if scope == "attention":
        # Stable: within each group the activity order above is kept.
        rows.sort(key=lambda r: r.waiting_since or datetime.max)
        rows.sort(key=lambda r: 0 if r.handoff_state == "waiting" else 1)
    return rows


def counts_of(states: list[HandoffState]) -> InboxCounts:
    """Counts for the attention queue, given the handoff state of each row."""
    return InboxCounts(waiting=states.count("waiting"), attention=len(states))


def inbox_counts(db: Session, *, tenant_id: uuid.UUID) -> InboxCounts:
    """How many visitors wait for a human, and how many need one at all.

    ``waiting`` is the sidebar badge: it drops to zero when every request has
    been answered or is being served. ``attention`` is the size of the default
    queue. Both count visitors, like the queue does; a visitor with a live
    chat is being served whatever their other tickets say.
    """
    chats = _newest_chats(
        db, tenant_id=tenant_id, session_ids=_session_ids_where(tenant_id, _needs_attention(tenant_id))
    )
    states = [v.handoff_state for v in _visitors_of(db, tenant_id=tenant_id, chats=chats)]
    return counts_of([state for state in states if state != "bot"])


def load_thread(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> Thread | None:
    """One visitor's whole history, every chat of every session, oldest first.

    The operator actions apply to the visitor's newest chat. ``None`` when
    the session is not this tenant's — indistinguishable from one that does
    not exist.
    """
    visitor = _visitor_of_session(db, tenant_id=tenant_id, session_id=session_id)
    if visitor is None:
        return None
    current = visitor.current
    chat_ids = select(Chat.id).where(
        Chat.tenant_id == tenant_id, Chat.session_id.in_(visitor.session_ids)
    )

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

    emails = _emails_by_user(
        db, {current.assigned_operator_id} if current.assigned_operator_id else set()
    )
    return Thread(
        session_id=session_id,
        chat=current,
        handoff_state=visitor.handoff_state,
        ticket=visitor.ticket,
        assigned_operator_email=emails.get(current.assigned_operator_id),
        visitor=visitor.visitor,
        messages=messages,
    )
