"""Background job: report inactive chat sessions, and age out stale tickets.

Five passes per tick, in this order:

1. :func:`release_idle_operator_chats` hands back a chat whose human operator
   went silent past ``OPERATOR_RELEASE_IDLE_SECONDS``. It runs first because
   passes 2 and 4 skip ``live`` chats entirely, so a chat pinned by a vanished
   operator would otherwise be invisible to both forever.
2. :func:`sweep_inactive_chats` emits ``chat_session_ended``.
3. :func:`bounce_abandoned_claims` returns a ticket an operator claimed and
   never answered to ``open``, re-notifying support once. Ahead of pass 4 so
   the bounced ticket is ``open`` again before that pass looks at it.
4. :func:`auto_close_stale_tickets` closes escalation tickets whose
   conversation is over (see its docstring for why tickets never leave
   ``open`` otherwise).
5. :func:`close_orphaned_operator_sessions` closes an ``operator_sessions``
   row whose chat is already back in ``bot`` — the backstop for a release
   whose stretch-close did not land. Last, because it is a backstop and
   because it must not disturb the ordering the four passes above rely on; it
   writes to no table any of them read.

Pass 1 is where most operator stretches are reported: a support conversation
ends when it ends and nobody presses "release", so the idle backstop, not the
button, is what closes the typical stretch and emits
``operator_session_ended``.

Passes 2 and 4 share the ``conversation_idle_timeout_seconds`` window. Pass 1
uses ``OPERATOR_RELEASE_IDLE_SECONDS`` and pass 3
``OPERATOR_CLAIM_BOUNCE_SECONDS``, both measured on operator activity rather
than visitor activity — and deliberately far apart from each other, because
one faces the waiting visitor and the other the team's inbox.


Widget chats are stateless per-turn HTTP with no explicit "close" signal, so
the end of a session is detected by inactivity: a chat whose ``updated_at``
(last activity) is older than the threshold is reported to PostHog once.

Idempotency uses ``Chat.session_ended_event_at`` (an analytics-only marker),
NOT ``Chat.ended_at``. ``ended_at`` closes the conversation and routes later
turns to the escalation "chat already closed" handler, so a returning user
would be told the chat is closed. Reporting a session as ended for analytics
must leave the chat resumable, hence the dedicated marker.

Runs as a :class:`~backend.jobs._periodic.PeriodicJob` daemon thread. Across
workers a Redis distributed lock gates each tick so only one worker sweeps per
interval (the emit is already idempotent via the committed marker, but the lock
avoids N concurrent duplicate scans). Without Redis (local dev) it runs
unguarded — single-process safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from backend.chat.events import _emit_chat_session_ended_event, _session_duration_ms
from backend.core.config import settings
from backend.escalation.service import (
    ACTIVE_TICKET_STATUSES,
    notify_support_of_abandoned_claim,
)
from backend.jobs._periodic import LockSpec, PeriodicJob
from backend.models import (
    Chat,
    EscalationStatus,
    EscalationTicket,
    Message,
    MessageRole,
    OperatorSession,
    OperatorSessionEndReason,
    OperatorState,
)
from backend.models.base import _utcnow

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 300
_STARTUP_DELAY_SECONDS = 60
# Comfortably above a bounded sweep (≤500 rows) yet below the interval, so a
# crashed holder's lock expires and the next tick recovers within one cycle.
_LOCK_TTL_SECONDS = 120
# Cap rows per pass so a large backlog drains over several passes (oldest
# first) instead of loading every inactive chat into memory at once.
_MAX_SESSIONS_PER_SWEEP = 500


def sweep_inactive_chats(db: Session, *, now: datetime | None = None) -> int:
    """Report chats inactive past the threshold via chat_session_ended.

    Returns the number of sessions for which an event was emitted. Chats with
    at least one ``Message`` emit ``chat_session_ended outcome=timeout``;
    empty chats — /widget/session/init creates a Chat per widget mount before
    the user writes anything, observed 154 mounts per real session in prod —
    are stamped silently. Both branches set ``session_ended_event_at`` so the
    row drops out of the partial index ``ix_chats_sweeper_pending`` and is
    excluded from the next pass; otherwise the empty-chat backlog would
    accumulate in the index unbounded as widget impressions add up.

    Chats the visitor already closed (``ended_at`` set — they answered "no"
    to the post-escalation "anything else?" follow-up; escalation on its own
    does not set it) are skipped: that path emits its own event.

    Chats a human operator currently holds (``OperatorState.live``) are
    skipped for the same reason ``auto_close_stale_tickets`` skips them:
    ``updated_at`` tracks visitor turns only, so a handoff being actively
    worked looks idle. The consequence here is worse than a premature event —
    ``session_ended_event_at`` makes ``should_rotate`` return True, so the
    visitor's next message would open a *new* chat with ``operator_state =
    bot`` and the bot would answer over the operator, whose thread is now
    orphaned. ``release_idle_operator_chats`` runs first each tick, so a chat
    whose operator really has gone is already back in ``bot`` by the time this
    pass looks at it.

    Two idle windows, read at call time so tests can override settings:

    * Chats with messages use ``conversation_idle_timeout_seconds`` — the same
      knob as lazy conversation rotation (backend/chat/rotation.py), so
      analytics and behavior share one definition of an ended conversation.
    * Message-less mount chats use ``empty_chat_idle_timeout_seconds`` (short).
      They never emit an event, so reaping them early only stamps the marker to
      drop them from ``ix_chats_sweeper_pending``; decoupling keeps that index
      small even when the conversation window is raised to days.
    """
    reference = now or _utcnow()
    long_cutoff = reference - timedelta(
        seconds=settings.conversation_idle_timeout_seconds
    )
    empty_cutoff = reference - timedelta(
        seconds=settings.empty_chat_idle_timeout_seconds
    )
    has_messages_exists = (
        select(Message.id).where(Message.chat_id == Chat.id).exists()
    )
    rows = (
        db.query(Chat, has_messages_exists.label("has_messages"))
        .options(joinedload(Chat.tenant), joinedload(Chat.bot))
        .filter(
            Chat.session_ended_event_at.is_(None),
            Chat.ended_at.is_(None),
            Chat.operator_state != OperatorState.live,
            or_(
                and_(has_messages_exists, Chat.updated_at < long_cutoff),
                and_(~has_messages_exists, Chat.updated_at < empty_cutoff),
            ),
        )
        .order_by(Chat.updated_at)
        .limit(_MAX_SESSIONS_PER_SWEEP)
        .all()
    )
    count = 0
    for chat, has_messages in rows:
        # Duration spans creation to last activity (updated_at), not the sweep
        # time, so it reflects the real session length.
        last_activity = chat.updated_at
        tenant_public_id = getattr(getattr(chat, "tenant", None), "public_id", None)
        bot_public_id = getattr(getattr(chat, "bot", None), "public_id", None)
        session_id = str(chat.session_id) if chat.session_id else None
        duration_ms = _session_duration_ms(chat.created_at, last_activity)
        try:
            # Query-level update with an explicit updated_at: the marker is an
            # analytics write, not activity, and must not refresh updated_at
            # (the column's onupdate would otherwise stamp sweep time, making
            # the idle chat look fresh to conversation rotation).
            db.query(Chat).filter(Chat.id == chat.id).update(
                {
                    "session_ended_event_at": reference,
                    "updated_at": last_activity,
                },
                synchronize_session=False,
            )
            db.commit()
        except Exception:
            logger.exception("chat_session_sweeper failed to mark chat %s", chat.id)
            db.rollback()
            continue
        if not has_messages:
            # Empty chat: marker set so it exits the partial index, but no
            # analytics event — emitting would inflate the funnel with
            # widget-impressions a real user never participated in.
            continue
        # Emit only after the marker is durably committed: a crash mid-pass can
        # then never re-find this chat, so the event is at-most-once (no
        # duplicate that would double-count the funnel).
        _emit_chat_session_ended_event(
            tenant_public_id=tenant_public_id,
            bot_public_id=bot_public_id,
            chat_id=str(chat.id),
            session_id=session_id,
            duration_ms=duration_ms,
            outcome="timeout",
        )
        count += 1
    return count


def release_idle_operator_chats(db: Session, *, now: datetime | None = None) -> int:
    """Hand back chats whose operator went silent. Returns the count released.

    The chat path releases lazily, on the visitor's next message (see
    ``backend/chat/handlers/operator.py``). That covers every abandoned
    handoff *except* the one nobody writes in again — and that is not a rare
    shape: an operator answers, the visitor is satisfied and leaves. Without
    this pass the chat stays ``live`` with ``assigned_operator_id`` set
    forever, which in turn keeps its open ticket permanently exempt from
    :func:`auto_close_stale_tickets` — re-introducing the very "tickets stay
    open forever" bug that pass exists to fix.

    Idleness is the same rule the turn-time release uses
    (:func:`~backend.chat.handlers.operator.operator_is_idle`: the later of
    ``operator_joined_at`` and the last ``MessageRole.operator`` message, aged
    past ``OPERATOR_RELEASE_IDLE_SECONDS``), and the write is the same column
    set (``released_to_bot_values``). One rule, one shape, two triggers.

    Ordered first in the tick, ahead of the two passes above, and deliberately
    so: a chat released here is eligible for both of them *in the same tick*
    rather than a cycle later. That requires not touching ``updated_at`` — a
    release is an operational state change, not visitor activity — hence the
    query-level UPDATE pinning it, exactly as :func:`sweep_inactive_chats`
    does for its marker. Letting the column's ``onupdate`` fire would make
    every abandoned chat look freshly active at the moment we conclude it was
    abandoned, and its ticket would never age out.
    """
    from backend.chat.handlers.operator import operator_is_idle, released_to_bot_values
    from backend.operator.sessions import (
        close_operator_session,
        emit_operator_session_ended,
    )

    reference = now or _utcnow()
    live = (
        db.query(Chat)
        .filter(Chat.operator_state == OperatorState.live)
        .order_by(Chat.updated_at)
        .limit(_MAX_SESSIONS_PER_SWEEP)
        .all()
    )
    count = 0
    for chat in live:
        if not operator_is_idle(db, chat):
            continue
        try:
            db.query(Chat).filter(
                Chat.id == chat.id,
                # Re-check under the write: a visitor turn between the read
                # above and here may have released the chat already (or an
                # operator may have replied). Losing that race must be a
                # no-op, not a second release stamping a later timestamp.
                Chat.operator_state == OperatorState.live,
            ).update(
                {
                    **released_to_bot_values(),
                    "operator_released_at": reference,
                    "updated_at": chat.updated_at,
                },
                synchronize_session=False,
            )
            # Same transaction as the release, deliberately. Closing the
            # stretch in a second transaction leaves a window in which the
            # chat reads ``bot`` while its stretch is still open — and an
            # operator answering in that window would have their *new* stretch
            # merged into the old open row and then closed along with it,
            # losing the second stretch permanently: the chat is ``live``
            # again with nothing open, and the reconciliation pass below only
            # looks at chats that are not ``live``. Reported nowhere, which is
            # the exact failure this table exists to end.
            released = close_operator_session(
                db,
                chat=chat,
                reason=OperatorSessionEndReason.idle_timeout,
                ended_at=reference,
            )
            db.commit()
        except Exception:
            logger.exception(
                "chat_session_sweeper failed to release chat %s", chat.id
            )
            db.rollback()
            continue
        # Only after the commit, so the event is at-most-once — the same rule
        # as the marker-then-emit order above.
        emit_operator_session_ended(released)
        count += 1
    return count


def _operator_answered_exists():
    """True for chats where an operator actually wrote something.

    Shared by the two passes that must tell "a human answered" apart from "a
    human claimed the request and said nothing": the bounce, which acts on the
    second, and auto-close, which must not bury it. One definition, so the two
    cannot drift into disagreeing about what an answer is.
    """
    return (
        select(Message.id)
        .where(
            Message.chat_id == Chat.id,
            Message.role == MessageRole.operator,
        )
        .exists()
    )


def bounce_abandoned_claims(db: Session, *, now: datetime | None = None) -> int:
    """Return dropped claims to the queue. Returns the count bounced.

    An operator claimed a conversation and never wrote a word. Nothing else in
    phase 0 can tell that apart from "an operator answered and the
    conversation ended naturally": both chats go quiet, and both tickets age
    out to ``auto_closed`` on the normal idle path. That is the worst outcome
    the feature can produce — a visitor asked for a human, a human took the
    request, said nothing, and the system quietly cleared it from the queue.
    An unclaimed ticket would at least have stayed visibly ``open``.

    The distinguishing signal is whether any ``MessageRole.operator`` message
    exists in the chat, which needs no schema of its own. No message at all
    means the claim produced nothing.

    Two clocks, deliberately not collapsed into one:

    * The chat release (``release_idle_operator_chats``, 15 min) faces the
      *visitor*, who is sitting in the widget waiting, so the bot must resume
      quickly. It is cheap and reversible — an operator who comes back and
      sends a message re-claims the chat through ``ingest_from_operator``.
    * This bounce (``OPERATOR_CLAIM_BOUNCE_SECONDS``, 12 h) faces the *team's
      inbox* and sends an e-mail. On the release clock it would re-notify
      every time an operator stepped away to read the docs or ask a colleague.

    The re-notification is capped at once per ticket via ``claim_bounced_at``.
    Outbound e-mail must not be able to loop, and the status transition alone
    would not cap it: a ticket bounced back to ``open`` can be claimed and
    abandoned again.

    **Known limitation, not a bug to fix here:** an operator who replies "let
    me check" and then disappears for three days does not bounce, because the
    zero-message test cannot see it. Deciding whether a reply was a
    *meaningful answer* is fuzzy, and guessing at it in v1 would trade a
    precise rule for an unpredictable one.

    Runs before :func:`auto_close_stale_tickets` so a bounced ticket is back
    in ``open`` before that pass considers it. With the default windows (12 h
    here against a 7-day conversation window) the two cannot collide anyway.
    """
    reference = now or _utcnow()
    cutoff = reference - timedelta(seconds=settings.operator_claim_bounce_seconds)
    answered_exists = _operator_answered_exists()
    abandoned = (
        db.query(EscalationTicket)
        .join(Chat, EscalationTicket.chat_id == Chat.id)
        .filter(
            EscalationTicket.status == EscalationStatus.in_progress,
            EscalationTicket.claim_bounced_at.is_(None),
            Chat.operator_joined_at.isnot(None),
            Chat.operator_joined_at < cutoff,
            ~answered_exists,
        )
        .order_by(EscalationTicket.created_at)
        .limit(_MAX_SESSIONS_PER_SWEEP)
        .all()
    )
    count = 0
    for ticket in abandoned:
        try:
            ticket.status = EscalationStatus.open
            # Naive UTC — the column is ``DateTime`` with no timezone; writing
            # an aware value crashes asyncpg. See ``models/base._utcnow``.
            ticket.claim_bounced_at = _utcnow()
            db.add(ticket)
            # Commit the cap *before* sending: a send that succeeds and then
            # fails to commit would re-notify on the next tick. Committing
            # first can at worst lose one e-mail, which is the better failure.
            db.commit()
        except Exception:
            logger.exception(
                "chat_session_sweeper failed to bounce ticket %s", ticket.id
            )
            db.rollback()
            continue
        count += 1
        try:
            notify_support_of_abandoned_claim(ticket, db)
            db.commit()
        except Exception:
            logger.exception(
                "chat_session_sweeper failed to notify on bounced ticket %s",
                ticket.id,
            )
            db.rollback()
    return count


def auto_close_stale_tickets(db: Session, *, now: datetime | None = None) -> int:
    """Close open tickets whose conversation is over. Returns the count closed.

    Chat9 has no inbound channel: the notify email carries ``Reply-To: <end
    user>``, so support answers the user directly and we never learn the
    outcome. The only transition off ``open`` is a tenant clicking "Mark as
    resolved" in the dashboard — which tenants working out of their mailbox
    never do. Without this pass every ticket ever created stays ``open``
    forever and the inbox's open count means nothing.

    Keyed on the ticket's chat going idle past
    ``conversation_idle_timeout_seconds`` — the same window lazy rotation and
    the session sweeper use, so "the conversation is over" keeps one definition
    system-wide. Deliberately independent of the ``chat_session_ended`` pass
    above, which skips chats the visitor already closed (``ended_at`` set);
    those carry tickets too and must age out on the same rule.

    Tickets with no ``chat_id`` (direct API creations) are left alone — there is
    no conversation to age them against.

    A claim that produced **no answer at all** is never closed here, whether or
    not it has already bounced. Closing it would destroy the only trace that a
    visitor asked for a human, a human took the request, and nobody ever
    replied — the queue is the only place that shows. Such a ticket stays
    visible until someone deals with it. The class is narrow by construction
    and it is meant to be conspicuous.

    Both *active* statuses age out, ``in_progress`` as well as ``open``. A
    claimed ticket that was answered is not permanently exempt: an operator answered and the
    conversation then ended naturally is the ordinary happy path, and leaving
    it ``in_progress`` forever would recreate the never-closing backlog this
    pass exists to drain, one status over. A claim that produced *no* answer
    is a different animal and is handled by
    :func:`bounce_abandoned_claims`, which runs first and puts such a ticket
    back to ``open`` before this pass sees it.

    Chats a human operator currently holds (``OperatorState.live``) are skipped
    outright, regardless of how idle they look. Idleness is measured on
    ``chats.updated_at``, which only a visitor turn refreshes — an operator
    reading the thread and composing a reply does not touch it — so a live
    handoff can cross the threshold while it is actively being worked. Closing
    the ticket underneath the person answering it is exactly wrong.
    """
    reference = now or _utcnow()
    cutoff = reference - timedelta(seconds=settings.conversation_idle_timeout_seconds)
    stale = (
        db.query(EscalationTicket)
        .join(Chat, EscalationTicket.chat_id == Chat.id)
        .filter(
            EscalationTicket.status.in_(ACTIVE_TICKET_STATUSES),
            Chat.updated_at < cutoff,
            Chat.operator_state != OperatorState.live,
            # An operator took this request and never wrote a word. Closing it
            # destroys the only trace that someone was left waiting, so it
            # stays in the queue until a human deals with it. Also removes any
            # dependence on the relative ordering of
            # ``conversation_idle_timeout_seconds`` and
            # ``operator_claim_bounce_seconds``: a ticket whose bounce is still
            # ahead of it cannot be closed out from under the bounce.
            or_(Chat.operator_joined_at.is_(None), _operator_answered_exists()),
        )
        .order_by(EscalationTicket.created_at)
        .limit(_MAX_SESSIONS_PER_SWEEP)
        .all()
    )
    count = 0
    for ticket in stale:
        try:
            ticket.status = EscalationStatus.auto_closed
            # Naive UTC — the column is ``DateTime`` with no timezone; writing
            # an aware value crashes asyncpg. See ``models/base._utcnow``.
            ticket.resolved_at = _utcnow()
            db.add(ticket)
            db.commit()
        except Exception:
            logger.exception(
                "chat_session_sweeper failed to auto-close ticket %s", ticket.id
            )
            db.rollback()
            continue
        count += 1
    return count


def close_orphaned_operator_sessions(db: Session, *, now: datetime | None = None) -> int:
    """Close stretches left open by a chat that is already back in ``bot``.

    Every release closes its own stretch in the same transaction, so this pass
    normally finds nothing. It exists for the rows no release will ever reach:
    a chat that went ``live`` before ``operator_sessions`` existed, and a
    stretch whose release landed while its close did not (a crash between the
    write and the commit, or a future release path that forgets). Without it
    such a row stays open forever and its stretch is never reported, which is
    the exact failure this table was added to end.

    It cannot cover a chat that is currently ``live`` — that is a stretch in
    progress, not an orphan — which is why the release paths close their own
    stretch atomically rather than relying on this pass to tidy up after them.

    Closed at ``chats.operator_released_at`` rather than sweep time: the
    stretch really ended when the chat was handed back, and stamping the
    moment we noticed would inflate every duration by up to one tick.

    Touches ``operator_sessions`` only — no chat, no ticket — so it cannot
    interfere with :func:`auto_close_stale_tickets`. Ordered last in the tick
    for the same reason: it is a backstop, and the four passes above keep the
    ordering their docstrings describe.
    """
    from backend.operator.sessions import (
        close_operator_session,
        emit_operator_session_ended,
    )

    reference = now or _utcnow()
    # No eager loads: each close commits, which expires everything loaded here,
    # so the tenant/bot the event needs are re-read per row anyway. This pass
    # normally selects nothing at all, and is capped like every other.
    chats = (
        db.query(Chat)
        .join(OperatorSession, OperatorSession.chat_id == Chat.id)
        .filter(
            OperatorSession.ended_at.is_(None),
            Chat.operator_state != OperatorState.live,
        )
        .order_by(OperatorSession.joined_at)
        .limit(_MAX_SESSIONS_PER_SWEEP)
        .all()
    )
    count = 0
    for chat in chats:
        try:
            closed = close_operator_session(
                db,
                chat=chat,
                reason=OperatorSessionEndReason.reconciled,
                ended_at=chat.operator_released_at or reference,
            )
            db.commit()
        except Exception:
            logger.exception(
                "chat_session_sweeper failed to reconcile operator session on chat %s",
                chat.id,
            )
            db.rollback()
            continue
        if closed is not None:
            emit_operator_session_ended(closed)
            count += 1
    return count


def _sweep_once() -> None:
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        # First: a chat still held by a vanished operator is invisible to the
        # two passes that exclude ``live``. Releasing it here makes it
        # eligible for them in this same tick.
        released = release_idle_operator_chats(db)
        if released:
            logger.info(
                "chat_session_sweeper: released %d idle operator chats", released
            )
        count = sweep_inactive_chats(db)
        if count:
            logger.info("chat_session_sweeper: reported %d inactive sessions", count)
        bounced = bounce_abandoned_claims(db)
        if bounced:
            logger.info(
                "chat_session_sweeper: bounced %d abandoned claims", bounced
            )
        closed = auto_close_stale_tickets(db)
        if closed:
            logger.info("chat_session_sweeper: auto-closed %d stale tickets", closed)
        orphaned = close_orphaned_operator_sessions(db)
        if orphaned:
            logger.info(
                "chat_session_sweeper: closed %d orphaned operator sessions", orphaned
            )
    finally:
        db.close()


_job = PeriodicJob(
    name="chat-session-sweeper",
    work=_sweep_once,
    interval_seconds=_CHECK_INTERVAL_SECONDS,
    startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    lock=LockSpec(
        job_kind="chat_session_sweeper",
        key_factory=lambda: "lock:chat_session_sweeper",
        ttl_seconds=_LOCK_TTL_SECONDS,
    ),
)


def start_chat_session_sweeper_thread() -> None:
    _job.start()


def shutdown_chat_session_sweeper_thread() -> None:
    _job.shutdown()
