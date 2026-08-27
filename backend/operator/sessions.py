"""Lifecycle of an operator-served stretch: open it, stamp it, close it.

One module so the three writers cannot drift. A stretch is opened wherever a
chat goes ``live`` (``/take`` and the implicit claim inside
``ingest_from_operator``), stamped with its first human reply on the same
ingest seam, and closed wherever the chat is handed back — the explicit
release, the lazy release on the visitor's next turn, and the sweeper's
backstop for the chat nobody writes in again. The sweeper is the primary
closer, not the release button: most support conversations end when they end
and nobody clicks anything.

Closing is also where ``operator_session_ended`` is emitted, always after the
row is durably committed, so the event is at-most-once for the same reason
``chat_session_ended`` is — a crash mid-close can never re-find a stretch it
already reported.

All DB work is sync, bridged from the async routes via ``run_sync`` like the
rest of the operator domain.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models import (
    Chat,
    EscalationTicket,
    OperatorSession,
    OperatorSessionEndReason,
)
from backend.models.base import _utcnow

logger = logging.getLogger(__name__)


def get_open_operator_session(db: Session, *, chat_id: uuid.UUID) -> OperatorSession | None:
    """The stretch currently being served in this chat, or ``None``.

    ``ended_at IS NULL`` is the open predicate, and at most one row per chat
    satisfies it: every path that opens a stretch goes through
    :func:`open_operator_session`, which reuses an open row rather than
    stacking a second one. Ordered newest-first anyway so a row left open by
    some future bug degrades into "the latest stretch wins" instead of
    resurrecting an ancient one.
    """
    return (
        db.query(OperatorSession)
        .filter(
            OperatorSession.chat_id == chat_id,
            OperatorSession.ended_at.is_(None),
        )
        .order_by(OperatorSession.joined_at.desc())
        .first()
    )


def _active_ticket_id(db: Session, *, chat_id: uuid.UUID) -> uuid.UUID | None:
    """The request this stretch is working, for the first-response clock.

    The chat's most recent ticket that is still being worked. Resolved once,
    when the stretch opens, rather than at close time: by then the ticket may
    have been resolved or auto-closed, and the stretch would lose the anchor
    of the ask it answered.

    Deliberately not restricted to ``open`` — both operator entry points call
    ``mark_ticket_in_progress`` before opening the stretch, so by this point
    the ticket the operator just picked up already reads ``in_progress``.
    """
    from backend.escalation.service import ACTIVE_TICKET_STATUSES

    return (
        db.query(EscalationTicket.id)
        .filter(
            EscalationTicket.chat_id == chat_id,
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
        )
        .order_by(EscalationTicket.created_at.desc())
        .limit(1)
        .scalar()
    )


def open_operator_session(
    db: Session,
    *,
    chat_id: uuid.UUID,
    tenant_id: uuid.UUID,
    operator_user_id: uuid.UUID | None,
    joined_at: datetime | None = None,
) -> OperatorSession:
    """Start recording a stretch. Returns the open row, new or existing.

    Staged on the caller's session and **not committed**: both callers are
    mid-transaction (``/take``'s claim, ``ingest_from_operator``'s message
    write) and the stretch must land or fail together with the state change
    that started it. A row for a chat that is not actually ``live`` would
    stay open until the reconciliation pass noticed.

    A chat that already has an open stretch keeps it. Two operators can
    legitimately answer one thread — assignment is advisory — and that is one
    stretch with one clock, not two.
    """
    existing = get_open_operator_session(db, chat_id=chat_id)
    if existing is not None:
        return existing
    session = OperatorSession(
        tenant_id=tenant_id,
        chat_id=chat_id,
        operator_user_id=operator_user_id,
        escalation_ticket_id=_active_ticket_id(db, chat_id=chat_id),
        # Naive UTC — the column is ``DateTime`` with no timezone and asyncpg
        # refuses aware values. See ``models/base._utcnow``.
        joined_at=joined_at or _utcnow(),
    )
    db.add(session)
    return session


def record_operator_reply(
    db: Session,
    *,
    chat_id: uuid.UUID,
    tenant_id: uuid.UUID,
    operator_user_id: uuid.UUID | None,
    at: datetime | None = None,
) -> OperatorSession:
    """Stamp the first human reply of the current stretch.

    Only the first: ``first_reply_at`` answers "how long did the customer wait
    for a person", so a later message in the same stretch must not push it.

    Opens the stretch when none is (a chat that went ``live`` before this
    table existed, or an operator answering an unclaimed chat, where the same
    call both opens and stamps). Staged, not committed — the caller's message
    write commits both together.
    """
    session = open_operator_session(
        db,
        chat_id=chat_id,
        tenant_id=tenant_id,
        operator_user_id=operator_user_id,
        joined_at=at,
    )
    if session.first_reply_at is None:
        session.first_reply_at = at or _utcnow()
        db.add(session)
    return session


def close_operator_session(
    db: Session,
    *,
    chat: Chat,
    reason: OperatorSessionEndReason,
    ended_at: datetime | None = None,
) -> OperatorSession | None:
    """Close this chat's open stretch and report it. Returns it, or ``None``.

    ``None`` when there is nothing open — releasing a chat no operator ever
    held, or a double release. Both are no-ops rather than errors, matching
    the release paths themselves.

    Commits, unlike the two functions above, and only then emits. Callers on
    the ORM release path have their own ``commit()`` pending on the same
    session, so the release columns and the closed stretch land in one
    transaction; the sweeper's bulk-UPDATE path has already committed its
    release, and a crash between the two leaves a row the reconciliation pass
    closes. The emit follows the commit for the same reason the session
    sweeper's does: an event that is never sent twice is worth more than one
    that is never missed.
    """
    session = get_open_operator_session(db, chat_id=chat.id)
    if session is None:
        return None
    session.ended_at = ended_at or _utcnow()
    session.ended_reason = reason
    db.add(session)
    try:
        db.commit()
    except Exception:
        logger.exception(
            "failed to close operator session %s on chat %s", session.id, chat.id
        )
        db.rollback()
        return None
    _emit_for(db, chat=chat, session=session)
    return session


def _first_response_ms(db: Session, session: OperatorSession) -> int | None:
    """Milliseconds from the customer asking for a human to a human replying.

    Measured from the escalation ticket's ``created_at`` — the moment the ask
    was recorded — because that is the clock support teams live by. Measuring
    from ``joined_at`` would measure nothing: an operator takes a chat and
    answers it in the same breath.

    ``None`` when the stretch produced no reply, or when there was no ticket
    behind it (an operator opening a conversation nobody escalated).
    """
    from backend.chat.events import _session_duration_ms

    if session.first_reply_at is None or session.escalation_ticket_id is None:
        return None
    asked_at = (
        db.query(EscalationTicket.created_at)
        .filter(EscalationTicket.id == session.escalation_ticket_id)
        .limit(1)
        .scalar()
    )
    return _session_duration_ms(asked_at, session.first_reply_at)


def _emit_for(db: Session, *, chat: Chat, session: OperatorSession) -> None:
    from backend.chat.events import (
        _emit_operator_session_ended_event,
        _session_duration_ms,
    )

    _emit_operator_session_ended_event(
        tenant_public_id=getattr(getattr(chat, "tenant", None), "public_id", None),
        bot_public_id=getattr(getattr(chat, "bot", None), "public_id", None),
        chat_id=str(chat.id),
        session_id=str(chat.session_id) if chat.session_id else None,
        operator_session_id=str(session.id),
        operator_user_id=(
            str(session.operator_user_id) if session.operator_user_id else None
        ),
        duration_ms=_session_duration_ms(session.joined_at, session.ended_at),
        first_response_ms=_first_response_ms(db, session),
        answered=session.first_reply_at is not None,
        ended_reason=session.ended_reason.value if session.ended_reason else None,
    )
