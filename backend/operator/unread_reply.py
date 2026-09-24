"""A visitor who missed an operator's reply gets it by e-mail.

An operator answers from the console, the visitor has long since closed the
tab, and the reply sits in a widget nobody is looking at. Five minutes after
every operator reply a deferred job checks whether the visitor has had it on
screen; if not, the reply goes out by mail to the address the conversation
already knows.

"Had it on screen" is a signal from the widget, not an inference from the
poll: the widget keeps polling with its panel collapsed, so a message the
server handed out is not a message the visitor saw. The widget reports the
newest message it rendered while its panel was open in a visible tab, and
that lands in ``Chat.visitor_read_message_id``. A visitor's own message after
the reply counts as reading it too — the widget shows the reply above the
composer they typed into.

One mail covers every unread reply at the time it fires, and
``Chat.unread_reply_mailed_message_id`` remembers how far it got, so three
replies in a row produce one message and the jobs behind the second and third
find nothing left to send. The e-mail lane stamps the same marker when it
forwards a seated member's mailed reply to the visitor, so that reply is not
sent twice.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from arq import Retry
from sqlalchemy.exc import DBAPIError, MissingGreenlet
from sqlalchemy.orm import Session
from sqlalchemy.util import await_only

from backend.core.queue import enqueue, register_job
from backend.core.redis import run_coro_sync
from backend.email.service import send_email
from backend.models import Chat, EscalationTicket, Message, MessageRole

logger = logging.getLogger(__name__)

UNREAD_REPLY_GRACE_SECONDS = 300
_JOB_NAME = "mail_unread_operator_reply"
_MAX_ATTEMPTS = 3
_RETRY_SECONDS = 120
_ENQUEUE_WAIT_SECONDS = 5
_SUBJECT_PREVIEW_CHARS = 60

Position = tuple[Any, uuid.UUID]


def _position(message: Message) -> Position:
    return (message.created_at, message.id)


def _lock_chat(db: Session, chat_id: uuid.UUID) -> Chat | None:
    """The chat row, locked for this transaction.

    Every writer of the two cursors goes through here, so a receipt racing a
    receipt, or a job racing a job for the next reply, serialises on the row
    instead of both reading the same state and both acting on it.
    """
    return (
        db.query(Chat)
        .filter(Chat.id == chat_id)
        .populate_existing()
        .with_for_update()
        .first()
    )


def _message_in_chat(db: Session, chat_id: uuid.UUID, message_id: uuid.UUID | None) -> Message | None:
    if message_id is None:
        return None
    return (
        db.query(Message)
        .filter(Message.chat_id == chat_id, Message.id == message_id)
        .first()
    )


def _thread(db: Session, chat_id: uuid.UUID) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )


def mark_visitor_read(db: Session, *, chat: Chat, message_id: uuid.UUID) -> bool:
    """Advance the visitor's read cursor. ``False`` if the message is not in this chat.

    Never moves backwards: a stale receipt from a widget that was showing an
    older tail must not un-read what a fresher one already reported.
    """
    target = _message_in_chat(db, chat.id, message_id)
    if target is None:
        return False
    locked = _lock_chat(db, chat.id)
    if locked is None:
        return False
    current = _message_in_chat(db, chat.id, locked.visitor_read_message_id)
    if current is None or _position(target) > _position(current):
        locked.visitor_read_message_id = message_id
        db.add(locked)
    db.commit()
    return True


def mark_reply_mailed(db: Session, *, chat: Chat, message: Message) -> None:
    """Record that the visitor already holds this reply in their mailbox."""
    locked = _lock_chat(db, chat.id)
    if locked is None:
        return
    current = _message_in_chat(db, chat.id, locked.unread_reply_mailed_message_id)
    if current is None or _position(message) > _position(current):
        locked.unread_reply_mailed_message_id = message.id
        db.add(locked)
    db.commit()


def unread_operator_replies(
    db: Session, *, chat: Chat, message_id: uuid.UUID
) -> list[Message]:
    """The operator replies still owed to the visitor, or ``[]`` if this one is not.

    The floor is whichever is latest of: what the visitor has read, what they
    were already mailed, and their own last message. A reply at or below the
    floor is settled; everything above it is what the mail carries.
    """
    thread = _thread(db, chat.id)
    target = next((m for m in thread if m.id == message_id), None)
    if target is None or target.role is not MessageRole.operator:
        return []
    by_id = {m.id: _position(m) for m in thread}
    floors = [
        by_id.get(chat.visitor_read_message_id),
        by_id.get(chat.unread_reply_mailed_message_id),
        next(
            (_position(m) for m in reversed(thread) if m.role is MessageRole.user),
            None,
        ),
    ]
    known = [p for p in floors if p is not None]
    floor = max(known) if known else None
    if floor is not None and _position(target) <= floor:
        return []
    return [
        m
        for m in thread
        if m.role is MessageRole.operator and (floor is None or _position(m) > floor)
    ]


def _latest_ticket(db: Session, chat_id: uuid.UUID) -> EscalationTicket | None:
    return (
        db.query(EscalationTicket)
        .filter(EscalationTicket.chat_id == chat_id)
        .order_by(EscalationTicket.created_at.desc())
        .first()
    )


def _recipient(chat: Chat, ticket: EscalationTicket | None) -> str | None:
    from backend.escalation.service import _is_valid_email

    candidates = [ticket.user_email if ticket else None]
    context = chat.user_context if isinstance(chat.user_context, dict) else {}
    candidates.append(context.get("email"))
    for value in candidates:
        if isinstance(value, str) and _is_valid_email(value):
            return value.strip()
    return None


def _subject(chat: Chat, ticket: EscalationTicket | None) -> str:
    from backend.escalation.service import _safe_ticket_question

    if ticket is not None:
        preview = _safe_ticket_question(ticket).replace("\n", " ").strip()
        return f"[{ticket.ticket_number}] {preview[:_SUBJECT_PREVIEW_CHARS]}".rstrip(" —-")
    if chat.bot is not None and chat.bot.name:
        return chat.bot.name
    return chat.tenant.name


def _body(replies: list[Message]) -> str:
    """The replies as written and nothing else — no copy of ours to localize."""
    return "\n\n".join(m.content.strip() for m in replies if m.content and m.content.strip())


def mail_unread_operator_replies(
    db: Session, *, chat_id: uuid.UUID, message_id: uuid.UUID
) -> str:
    """Mail the visitor what they have not read. Returns the outcome for logs.

    The marker is claimed under the row lock and committed *before* the send,
    so two jobs for replies typed seconds apart cannot both find the same
    unread tail, and the lock is not held across a ten-second provider call
    that a visitor's turn on the same chat would otherwise wait behind. A
    failed send hands the marker back so the retry finds the replies again —
    unless a later job has already moved it on, in which case those earlier
    replies stay claimed rather than re-mailing the later job's tail. At most
    once by design: a worker killed between the claim and the provider's
    answer leaves the claim standing, and the reply is still on screen in the
    widget.
    """
    from backend.escalation.service import _support_inbox_recipient

    chat = _lock_chat(db, chat_id)
    if chat is None:
        return "no_chat"
    replies = unread_operator_replies(db, chat=chat, message_id=message_id)
    if not replies:
        db.rollback()
        return "nothing_unread"
    ticket = _latest_ticket(db, chat.id)
    recipient = _recipient(chat, ticket)
    if recipient is None:
        db.rollback()
        logger.info("unread_reply_mail_skipped_no_recipient chat_id=%s", chat.id)
        return "no_recipient"

    subject = _subject(chat, ticket)
    body = _body(replies)
    reply_to = _support_inbox_recipient(chat.tenant, db) if chat.tenant is not None else None
    previous_marker = chat.unread_reply_mailed_message_id
    claimed = replies[-1].id
    chat.unread_reply_mailed_message_id = claimed
    db.add(chat)
    db.commit()
    logger.info("unread_reply_claimed chat_id=%s through=%s", chat_id, claimed)

    sent = send_email(recipient, subject, body, reply_to=reply_to)
    if sent is None:
        locked = _lock_chat(db, chat_id)
        if locked is not None and locked.unread_reply_mailed_message_id == claimed:
            locked.unread_reply_mailed_message_id = previous_marker
            db.add(locked)
        db.commit()
        logger.warning("unread_reply_mail_send_failed chat_id=%s", chat_id)
        return "send_failed"

    logger.info(
        "unread_reply_mailed chat_id=%s replies=%d ticket=%s",
        chat_id,
        len(replies),
        ticket.ticket_number if ticket else None,
    )
    return "sent"


def _mail_in_thread(chat_id: uuid.UUID, message_id: uuid.UUID) -> str:
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        return mail_unread_operator_replies(db, chat_id=chat_id, message_id=message_id)
    finally:
        db.close()


@register_job(name=_JOB_NAME, max_attempts=_MAX_ATTEMPTS)
async def mail_unread_operator_reply(ctx: dict[str, Any], chat_id: str, message_id: str) -> str:
    try:
        outcome = await asyncio.to_thread(
            _mail_in_thread, uuid.UUID(chat_id), uuid.UUID(message_id)
        )
    except DBAPIError as exc:
        logger.warning("unread_reply_job_db_error chat_id=%s: %s", chat_id, exc)
        raise Retry(defer=_RETRY_SECONDS) from exc
    if outcome == "send_failed":
        raise Retry(defer=_RETRY_SECONDS)
    return outcome


def _bridge_to_loop(make: Callable[[], Awaitable[str | None]]) -> str | None:
    """Run an enqueue coroutine from sync code, wherever that code is running.

    Inside a ``run_sync`` greenlet on the event-loop thread ``await_only``
    is the bridge; from a thread-pool handler the coroutine is submitted to
    the main loop instead. ``await_only`` closes the coroutine it was handed
    before raising ``MissingGreenlet``, hence a factory rather than a
    coroutine.
    """
    try:
        return await_only(make())
    except MissingGreenlet:
        pass
    return run_coro_sync(
        make,
        timeout=_ENQUEUE_WAIT_SECONDS,
        default=None,
        label="unread_reply_enqueue_sync",
        cancel_on_timeout=False,
        warn_on_failure=True,
    )


def schedule_unread_reply_email(*, chat: Chat, message: Message) -> str | None:
    """Queue the five-minute check for one operator reply. Returns the job id."""

    def make() -> Awaitable[str | None]:
        return enqueue(
            _JOB_NAME,
            str(chat.id),
            str(message.id),
            kind=_JOB_NAME,
            tenant_id=chat.tenant_id,
            payload={"chat_id": str(chat.id), "message_id": str(message.id)},
            job_id=f"unread_reply_{message.id.hex}",
            _defer_by=UNREAD_REPLY_GRACE_SECONDS,
        )

    try:
        return _bridge_to_loop(make)
    except Exception:
        logger.warning("unread_reply_enqueue_failed chat_id=%s", chat.id, exc_info=True)
        return None
