"""Background job: close inactive chat sessions and emit chat_session_ended.

Widget chats are stateless per-turn HTTP with no explicit "close" signal, so
the end of a session is detected by inactivity: a chat whose ``updated_at``
(last activity) is older than the threshold and which has not been closed yet
is marked ended and reported to PostHog.

Runs in a daemon thread (single-process safe — one Railway dyno), mirroring
``backend.jobs.kb_language_snapshot``.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, joinedload

from backend.chat.events import _emit_chat_session_ended_event, _session_duration_ms
from backend.models import Chat
from backend.models.base import _utcnow

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()
_sweeper_thread: threading.Thread | None = None

_INACTIVITY_THRESHOLD_SECONDS = 3600  # 60 min of no activity ends a session
_CHECK_INTERVAL_SECONDS = 300
_STARTUP_DELAY_SECONDS = 60
# Cap rows per pass so a large backlog drains over several passes (oldest
# first) instead of loading every inactive chat into memory at once.
_MAX_SESSIONS_PER_SWEEP = 500


def sweep_inactive_chats(db: Session, *, now: datetime | None = None) -> int:
    """Close chats inactive past the threshold and emit chat_session_ended.

    Returns the number of sessions ended. The ``ended_at IS NULL`` filter makes
    this idempotent: a chat closed in one pass is excluded from the next.
    """
    reference = now or _utcnow()
    cutoff = reference - timedelta(seconds=_INACTIVITY_THRESHOLD_SECONDS)
    rows = (
        db.query(Chat)
        .options(joinedload(Chat.tenant), joinedload(Chat.bot))
        .filter(Chat.ended_at.is_(None), Chat.updated_at < cutoff)
        .order_by(Chat.updated_at)
        .limit(_MAX_SESSIONS_PER_SWEEP)
        .all()
    )
    count = 0
    for chat in rows:
        # ended_at tracks the last activity (updated_at), not the sweep time,
        # so the reported duration reflects the real session length.
        last_activity = chat.updated_at
        tenant_public_id = getattr(getattr(chat, "tenant", None), "public_id", None)
        bot_public_id = getattr(getattr(chat, "bot", None), "public_id", None)
        session_id = str(chat.session_id) if chat.session_id else None
        duration_ms = _session_duration_ms(chat.created_at, last_activity)
        try:
            chat.ended_at = last_activity
            db.add(chat)
            db.commit()
        except Exception:
            logger.exception("chat_session_sweeper failed to close chat %s", chat.id)
            db.rollback()
            continue
        # Emit only after the close is durably committed: a crash mid-pass can
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


def _run_sweeper_loop() -> None:
    _shutdown_event.wait(_STARTUP_DELAY_SECONDS)
    while not _shutdown_event.is_set():
        from backend.core.db import SessionLocal

        db = SessionLocal()
        try:
            count = sweep_inactive_chats(db)
            if count:
                logger.info("chat_session_sweeper: ended %d inactive sessions", count)
        except Exception:
            logger.exception("chat_session_sweeper loop error")
        finally:
            db.close()
        _shutdown_event.wait(_CHECK_INTERVAL_SECONDS)


def start_chat_session_sweeper_thread() -> None:
    global _sweeper_thread
    if _sweeper_thread is not None:
        return
    t = threading.Thread(
        target=_run_sweeper_loop,
        daemon=True,
        name="chat-session-sweeper",
    )
    _sweeper_thread = t
    t.start()


def shutdown_chat_session_sweeper_thread() -> None:
    _shutdown_event.set()
    if _sweeper_thread is not None:
        _sweeper_thread.join(timeout=5)
