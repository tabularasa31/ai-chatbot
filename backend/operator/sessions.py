"""Lifecycle of an operator-served stretch: open it, stamp it, close it.

One module so the writers cannot drift. A stretch is opened wherever a chat
goes ``live`` (``/take`` and the implicit claim inside
``ingest_from_operator``), stamped with its first human reply on the same
ingest seam, and closed wherever the chat is handed back — the explicit
release, the lazy release on the visitor's next turn, and the sweeper's
backstop for the chat nobody writes in again. The sweeper is the primary
closer, not the release button: most support conversations end when they end
and nobody clicks anything.

**Nothing here commits.** Every write is staged on the caller's session so
that the stretch lands in the same transaction as the state change it
describes — a release and the close of the stretch it ends are one atomic
write, not two. :func:`close_operator_session` returns a
:class:`ClosedStretch`, fully computed *before* that commit, which the caller
hands to :func:`emit_operator_session_ended` *after* it. The event is
therefore at-most-once for the same reason ``chat_session_ended`` is — a
crash before the commit reports nothing, and a crash after it cannot re-find
a stretch already closed — and the emit itself touches no database, so it
cannot fail a request that has already succeeded.

Concurrency is handled by the database, not by hope. The open predicate
``ended_at IS NULL`` is backed by a unique partial index, so "one open
stretch per chat" is an invariant rather than an assumption; the stamp and
the close are conditional writes that re-read under their own row lock, so
two racing closers produce one event and a reply racing a close starts the
new stretch it belongs to.

All DB work is sync, bridged from the async routes via ``run_sync`` like the
rest of the operator domain.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    Chat,
    EscalationTicket,
    OperatorSession,
    OperatorSessionEndReason,
)
from backend.models.base import _utcnow


@dataclass(frozen=True)
class ClosedStretch:
    """Everything ``operator_session_ended`` needs, read before the commit.

    Frozen and self-contained on purpose. The emit runs after the transaction
    that closed the stretch, when the ORM objects it came from are expired;
    resolving them again there would put four lazy SELECTs — and four chances
    to raise — after the point where the release has already succeeded. On the
    visitor's turn that is a live request; a telemetry read must not be able
    to turn a completed release into a 500.
    """

    tenant_public_id: str | None
    bot_public_id: str | None
    chat_id: str
    session_id: str | None
    operator_session_id: str
    operator_user_id: str | None
    duration_ms: int | None
    first_response_ms: int | None
    answered: bool
    ended_reason: str


def get_open_operator_session(db: Session, *, chat_id: uuid.UUID) -> OperatorSession | None:
    """The stretch currently being served in this chat, or ``None``.

    ``ended_at IS NULL`` is the open predicate, and at most one row per chat
    satisfies it — enforced by the unique partial index
    ``uq_operator_sessions_open``, not merely intended. Ordered newest-first
    so that a database predating that index degrades into "the latest stretch
    wins" rather than resurrecting an ancient one.
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


def _unanchored_ticket_id(db: Session, *, chat_id: uuid.UUID) -> uuid.UUID | None:
    """The request this stretch is answering, for the first-response clock.

    The chat's most recent ticket that is still being worked **and that no
    earlier stretch has already claimed**. Both halves matter:

    * Resolved once, when the stretch opens, rather than at close time — by
      then the ticket may be resolved or auto-closed and the stretch would
      lose the anchor of the ask it answered.
    * Skipping a ticket an earlier stretch already anchored is what keeps a
      repeat takeover from reporting a second ``first_response_ms`` for one
      ask. Nothing moves a ticket out of ``in_progress`` on release, so
      without this the second stretch would re-measure from the *original*
      ``created_at`` — hours earlier, and already answered in minutes — and
      quietly inflate the team's first-response average. A second takeover
      with no new escalation is not a response to a new ask, and reports no
      response time at all. A genuinely new escalation in between mints a new
      ticket, which is unanchored and is picked up here.

    Deliberately not restricted to ``open``: both operator entry points call
    ``mark_ticket_in_progress`` before opening the stretch, so the ticket just
    picked up already reads ``in_progress`` by the time this runs.
    """
    from backend.escalation.service import ACTIVE_TICKET_STATUSES

    already_anchored = (
        select(OperatorSession.id)
        .where(OperatorSession.escalation_ticket_id == EscalationTicket.id)
        .exists()
    )
    return (
        db.query(EscalationTicket.id)
        .filter(
            EscalationTicket.chat_id == chat_id,
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
            ~already_anchored,
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

    Staged on the caller's session and not committed: both callers are
    mid-transaction (``/take``'s claim, ``ingest_from_operator``'s message
    write) and the stretch must land or fail together with the state change
    that started it.

    A chat that already has an open stretch keeps it. Two colleagues
    answering one thread — assignment is advisory, and a shared support inbox
    has no single claimant — is one stretch with one clock, not two.

    That reuse is a read-then-write, so two simultaneous ingests can both find
    nothing open and both insert. The unique partial index turns the loser's
    flush into an ``IntegrityError`` instead of a silent second row, which
    would have produced two ``operator_session_ended`` events for one
    human-served stretch — the double counting this whole design exists to
    avoid. The insert runs in a savepoint so losing that race costs the
    operator's reply nothing: the outer transaction is untouched and the
    winner's row is returned.
    """
    existing = get_open_operator_session(db, chat_id=chat_id)
    if existing is not None:
        return existing
    session = OperatorSession(
        tenant_id=tenant_id,
        chat_id=chat_id,
        operator_user_id=operator_user_id,
        escalation_ticket_id=_unanchored_ticket_id(db, chat_id=chat_id),
        # Naive UTC — the column is ``DateTime`` with no timezone and asyncpg
        # refuses aware values. See ``models/base._utcnow``.
        joined_at=joined_at or _utcnow(),
    )
    try:
        with db.begin_nested():
            db.add(session)
            db.flush()
    except IntegrityError:
        winner = get_open_operator_session(db, chat_id=chat_id)
        if winner is None:
            # Not the race this guards: re-raise rather than swallow a
            # constraint violation we have no story for.
            raise
        return winner
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
    for a person", so a later message in the same stretch must not push it —
    hence ``COALESCE`` rather than an assignment.

    The stamp is conditional on the stretch still being open, and that
    condition is the interesting part. A reply can arrive in the instant a
    closer is committing — the sweeper releasing a chat it judged idle, while
    the operator it gave up on is typing. The conditional write re-reads under
    the closer's row lock, matches nothing, and the reply opens the new
    stretch it actually belongs to, instead of landing on a stretch that is
    being closed and vanishing with it.

    Opens the stretch outright when none is open: a chat that went ``live``
    before this table existed, or an operator answering an unclaimed chat, who
    never pressed "take".
    """
    replied_at = at or _utcnow()
    session = get_open_operator_session(db, chat_id=chat_id)
    if session is not None:
        stamped = (
            db.query(OperatorSession)
            .filter(
                OperatorSession.id == session.id,
                OperatorSession.ended_at.is_(None),
            )
            .update(
                {
                    "first_reply_at": func.coalesce(
                        OperatorSession.first_reply_at, replied_at
                    )
                },
                synchronize_session=False,
            )
        )
        if stamped:
            # synchronize_session=False leaves the loaded row stale.
            db.expire(session)
            return session

    session = open_operator_session(
        db,
        chat_id=chat_id,
        tenant_id=tenant_id,
        operator_user_id=operator_user_id,
        joined_at=replied_at,
    )
    if session.first_reply_at is None:
        session.first_reply_at = replied_at
        db.add(session)
    return session


def close_operator_session(
    db: Session,
    *,
    chat: Chat,
    reason: OperatorSessionEndReason,
    ended_at: datetime | None = None,
) -> ClosedStretch | None:
    """Close this chat's open stretch. Returns what to report, or ``None``.

    ``None`` when there was nothing open — releasing a chat no operator ever
    held, or a double release — and when another closer got there first. Both
    are no-ops rather than errors, matching the release paths themselves, and
    the second is why the write is conditional: a visitor turn releasing while
    the sweeper closes the same stretch must produce one event, not two.

    Staged, never committed. The caller's commit ends the stretch and the
    release together — there is no window in which the chat is back with the
    bot while its stretch is still open, so an operator who answers in that
    instant cannot have their new stretch merged into the one being closed and
    then closed along with it.

    Emit the returned payload with :func:`emit_operator_session_ended` after
    that commit, never before.
    """
    session = get_open_operator_session(db, chat_id=chat.id)
    if session is None:
        return None
    closed_at = ended_at or _utcnow()
    won = (
        db.query(OperatorSession)
        .filter(
            OperatorSession.id == session.id,
            OperatorSession.ended_at.is_(None),
        )
        .update(
            {"ended_at": closed_at, "ended_reason": reason},
            synchronize_session=False,
        )
    )
    if not won:
        return None
    return ClosedStretch(
        tenant_public_id=getattr(getattr(chat, "tenant", None), "public_id", None),
        bot_public_id=getattr(getattr(chat, "bot", None), "public_id", None),
        chat_id=str(chat.id),
        session_id=str(chat.session_id) if chat.session_id else None,
        operator_session_id=str(session.id),
        operator_user_id=(
            str(session.operator_user_id) if session.operator_user_id else None
        ),
        duration_ms=_duration_ms(session.joined_at, closed_at),
        first_response_ms=_first_response_ms(db, session),
        answered=session.first_reply_at is not None,
        ended_reason=reason.value,
    )


def emit_operator_session_ended(stretch: ClosedStretch | None) -> None:
    """Report a closed stretch. Call only after the commit that closed it.

    Accepts ``None`` so callers can hand on whatever
    :func:`close_operator_session` gave them without branching.
    """
    if stretch is None:
        return
    from backend.chat.events import _emit_operator_session_ended_event

    _emit_operator_session_ended_event(
        tenant_public_id=stretch.tenant_public_id,
        bot_public_id=stretch.bot_public_id,
        chat_id=stretch.chat_id,
        session_id=stretch.session_id,
        operator_session_id=stretch.operator_session_id,
        operator_user_id=stretch.operator_user_id,
        duration_ms=stretch.duration_ms,
        first_response_ms=stretch.first_response_ms,
        answered=stretch.answered,
        ended_reason=stretch.ended_reason,
    )


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    from backend.chat.events import _session_duration_ms

    return _session_duration_ms(start, end)


def _first_response_ms(db: Session, session: OperatorSession) -> int | None:
    """Milliseconds from the customer asking for a human to a human replying.

    Measured from the escalation ticket's ``created_at`` — the moment the ask
    was recorded — because that is the clock support teams live by. Measuring
    from ``joined_at`` would measure nothing: an operator takes a chat and
    answers it in the same breath.

    ``None`` when the stretch produced no reply, or when it anchored no ticket
    — an operator opening a conversation nobody escalated, or a repeat
    takeover with no new ask behind it (see :func:`_unanchored_ticket_id`).
    """
    if session.first_reply_at is None or session.escalation_ticket_id is None:
        return None
    asked_at = (
        db.query(EscalationTicket.created_at)
        .filter(EscalationTicket.id == session.escalation_ticket_id)
        .limit(1)
        .scalar()
    )
    return _duration_ms(asked_at, session.first_reply_at)
