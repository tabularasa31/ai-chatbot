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
find nothing left to send.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from arq import Retry
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.orm import Session
from sqlalchemy.util import await_only

from backend.core.queue import enqueue, get_main_loop, register_job
from backend.email.service import send_email
from backend.models import Chat, EscalationTicket, Message, MessageRole

logger = logging.getLogger(__name__)

UNREAD_REPLY_GRACE_SECONDS = 300
_JOB_NAME = "mail_unread_operator_reply"
_MAX_ATTEMPTS = 3
_SEND_RETRY_SECONDS = 120
_ENQUEUE_WAIT_SECONDS = 5
_SUBJECT_PREVIEW_CHARS = 60

Position = tuple[Any, uuid.UUID]


def _position(message: Message) -> Position:
    return (message.created_at, message.id)


def _thread(db: Session, chat_id: uuid.UUID) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )


def _position_of(thread: list[Message], message_id: uuid.UUID | None) -> Position | None:
    if message_id is None:
        return None
    return next((_position(m) for m in thread if m.id == message_id), None)


def mark_visitor_read(db: Session, *, chat: Chat, message_id: uuid.UUID) -> bool:
    """Advance the visitor's read cursor. ``False`` if the message is not in this chat.

    Never moves backwards: a stale receipt from a widget that was showing an
    older tail must not un-read what a fresher one already reported.
    """
    thread = _thread(db, chat.id)
    target = _position_of(thread, message_id)
    if target is None:
        return False
    current = _position_of(thread, chat.visitor_read_message_id)
    if current is None or target > current:
        chat.visitor_read_message_id = message_id
        db.add(chat)
        db.commit()
    return True


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
    floors = [
        _position_of(thread, chat.visitor_read_message_id),
        _position_of(thread, chat.unread_reply_mailed_message_id),
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
    return chat.bot.name if chat.bot is not None else "Re:"


def _body(replies: list[Message]) -> str:
    """The replies as written and nothing else — no copy of ours to localize."""
    return "\n\n".join(m.content.strip() for m in replies if m.content and m.content.strip())


def mail_unread_operator_replies(
    db: Session, *, chat_id: uuid.UUID, message_id: uuid.UUID
) -> str:
    """Mail the visitor what they have not read. Returns the outcome for logs."""
    from backend.escalation.service import _support_inbox_recipient

    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if chat is None:
        return "no_chat"
    replies = unread_operator_replies(db, chat=chat, message_id=message_id)
    if not replies:
        return "nothing_unread"
    ticket = _latest_ticket(db, chat.id)
    recipient = _recipient(chat, ticket)
    if recipient is None:
        logger.info("unread_reply_mail_skipped_no_recipient chat_id=%s", chat.id)
        return "no_recipient"

    reply_to = _support_inbox_recipient(chat.tenant, db) if chat.tenant is not None else None
    sent = send_email(
        recipient,
        _subject(chat, ticket),
        _body(replies),
        reply_to=reply_to,
    )
    if sent is None:
        logger.warning("unread_reply_mail_send_failed chat_id=%s", chat.id)
        return "send_failed"

    chat.unread_reply_mailed_message_id = replies[-1].id
    db.add(chat)
    db.commit()
    logger.info(
        "unread_reply_mailed chat_id=%s replies=%d ticket=%s",
        chat.id,
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
    outcome = await asyncio.to_thread(
        _mail_in_thread, uuid.UUID(chat_id), uuid.UUID(message_id)
    )
    if outcome == "send_failed":
        raise Retry(defer=_SEND_RETRY_SECONDS)
    return outcome


def _bridge_to_loop(make: Callable[[], Awaitable[str | None]]) -> str | None:
    """Run an enqueue coroutine from sync code, wherever that code is running.

    Inside a ``run_sync`` greenlet on the event-loop thread ``await_only``
    is the bridge; from a thread-pool handler the coroutine is submitted to
    the main loop instead. Either way the caller gets the job id or ``None``.
    """
    try:
        return await_only(make())
    except MissingGreenlet:
        pass
    loop = get_main_loop()
    if loop is None or not loop.is_running():
        logger.warning("unread_reply_enqueue_skipped reason=no_loop")
        return None
    future = asyncio.run_coroutine_threadsafe(make(), loop)
    try:
        return future.result(timeout=_ENQUEUE_WAIT_SECONDS)
    except Exception:
        logger.warning("unread_reply_enqueue_failed", exc_info=True)
        return None


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
