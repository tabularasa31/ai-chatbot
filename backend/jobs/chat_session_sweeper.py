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

from sqlalchemy.orm import Session

from backend.chat.events import _emit_chat_session_ended_event, _session_duration_ms
from backend.models import Chat
from backend.models.base import _utcnow

logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()
_sweeper_thread: threading.Thread | None = None

_INACTIVITY_THRESHOLD_SECONDS = 3600  # 60 min of no activity ends a session
_CHECK_INTERVAL_SECONDS = 300
_STARTUP_DELAY_SECONDS = 60


def sweep_inactive_chats(db: Session, *, now: datetime | None = None) -> int:
    """Close chats inactive past the threshold and emit chat_session_ended.

    Returns the number of sessions ended. The ``ended_at IS NULL`` filter makes
    this idempotent: a chat closed in one pass is excluded from the next.
    """
    reference = now or _utcnow()
    cutoff = reference - timedelta(seconds=_INACTIVITY_THRESHOLD_SECONDS)
    rows = (
        db.query(Chat)
        .filter(Chat.ended_at.is_(None), Chat.updated_at < cutoff)
        .all()
    )
    count = 0
    for chat in rows:
        try:
            # ended_at tracks the last activity (updated_at), not the sweep
            # time, so the reported duration reflects the real session length.
            chat.ended_at = chat.updated_at
            db.add(chat)
            tenant = getattr(chat, "tenant", None)
            bot = getattr(chat, "bot", None)
            _emit_chat_session_ended_event(
                tenant_public_id=getattr(tenant, "public_id", None),
                bot_public_id=getattr(bot, "public_id", None),
                chat_id=str(chat.id),
                session_id=str(chat.session_id) if chat.session_id else None,
                duration_ms=_session_duration_ms(chat.created_at, chat.ended_at),
                outcome="timeout",
            )
            count += 1
        except Exception:
            logger.exception("chat_session_sweeper failed for chat %s", chat.id)
    db.commit()
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
