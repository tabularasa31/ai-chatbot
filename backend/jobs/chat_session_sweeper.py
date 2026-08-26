"""Background job: report inactive chat sessions, and age out stale tickets.

Two passes per tick, sharing the ``conversation_idle_timeout_seconds`` window:
:func:`sweep_inactive_chats` emits ``chat_session_ended``, and
:func:`auto_close_stale_tickets` closes escalation tickets whose conversation
is over (see its docstring for why tickets never leave ``open`` otherwise).


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
from backend.jobs._periodic import LockSpec, PeriodicJob
from backend.models import Chat, EscalationStatus, EscalationTicket, Message
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
    """
    reference = now or _utcnow()
    cutoff = reference - timedelta(seconds=settings.conversation_idle_timeout_seconds)
    stale = (
        db.query(EscalationTicket)
        .join(Chat, EscalationTicket.chat_id == Chat.id)
        .filter(
            EscalationTicket.status == EscalationStatus.open,
            Chat.updated_at < cutoff,
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


def _sweep_once() -> None:
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        count = sweep_inactive_chats(db)
        if count:
            logger.info("chat_session_sweeper: reported %d inactive sessions", count)
        closed = auto_close_stale_tickets(db)
        if closed:
            logger.info("chat_session_sweeper: auto-closed %d stale tickets", closed)
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
