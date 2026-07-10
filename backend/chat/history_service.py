"""Chat history and session listing helpers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, joinedload

from backend.chat.language_context import _decrypt_optional
from backend.models import (
    Chat,
    Message,
    MessageFeedback,
    MessageRole,
    Tenant,
)
from backend.privacy_config import public_redaction_config_dict

logger = logging.getLogger(__name__)

PREVIEW_MAX_LEN = 120


def _tenant_optional_entity_types(tenant: Tenant | None) -> set[str] | None:
    if not tenant:
        return None
    raw = tenant.settings if isinstance(tenant.settings, dict) else None
    cfg = public_redaction_config_dict(raw)
    return set(cfg["optional_entity_types"])


def _display_message_content(message: Message, *, include_original: bool) -> str:
    if include_original:
        original = _decrypt_optional(message.content_original_encrypted)
        if original is not None:
            return original
    if message.content_redacted:
        return message.content_redacted
    return message.content


def _message_original_available(message: Message) -> bool:
    return bool(message.content_original_encrypted)


@dataclass
class SessionSummary:
    """Summary of a chat session for inbox list."""

    session_id: uuid.UUID
    message_count: int
    last_question: str | None
    last_answer_preview: str | None
    last_activity: datetime


def _session_chat_ids(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> list[uuid.UUID]:
    """All conversation (Chat) ids of a session, oldest first.

    A session spans several Chat rows once conversation rotation kicks in;
    session-level reads must cover every conversation, not just the first row.
    """
    rows = (
        db.query(Chat.id)
        .filter(
            Chat.session_id == session_id,
            Chat.tenant_id == tenant_id,
        )
        .order_by(Chat.created_at.asc())
        .all()
    )
    return [row[0] for row in rows]


def get_chat_history(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> list[Message]:
    chat_ids = _session_chat_ids(session_id, tenant_id, db)
    if not chat_ids:
        return []
    messages = (
        db.query(Message)
        .filter(Message.chat_id.in_(chat_ids))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    return list(messages)


def list_chat_sessions(tenant_id: uuid.UUID, db: Session) -> list[SessionSummary]:
    chats = (
        db.query(Chat)
        .filter(Chat.tenant_id == tenant_id)
        .options(joinedload(Chat.messages))
        .order_by(Chat.created_at.asc())
        .all()
    )
    # One inbox row per session: a session's conversations (Chat rows created
    # by rotation) are folded together, newest activity wins the preview.
    by_session: dict[uuid.UUID, SessionSummary] = {}
    for chat in chats:
        messages = sorted(chat.messages, key=lambda m: m.created_at or datetime.min)
        msg_count = len(messages)
        last_activity = chat.created_at or datetime.min
        last_question: str | None = None
        last_answer_preview: str | None = None

        for m in messages:
            if m.created_at and m.created_at > last_activity:
                last_activity = m.created_at
            if m.role == MessageRole.user:
                last_question = _display_message_content(m, include_original=False)
            elif m.role == MessageRole.assistant:
                preview = _display_message_content(m, include_original=False)
                if len(preview) > PREVIEW_MAX_LEN:
                    preview = preview[:PREVIEW_MAX_LEN].rstrip() + "..."
                last_answer_preview = preview

        existing = by_session.get(chat.session_id)
        if existing is None:
            by_session[chat.session_id] = SessionSummary(
                session_id=chat.session_id,
                message_count=msg_count,
                last_question=last_question,
                last_answer_preview=last_answer_preview,
                last_activity=last_activity,
            )
            continue
        existing.message_count += msg_count
        if last_activity >= existing.last_activity:
            existing.last_activity = last_activity
            if last_question is not None:
                existing.last_question = last_question
            if last_answer_preview is not None:
                existing.last_answer_preview = last_answer_preview

    result = list(by_session.values())
    result.sort(key=lambda s: s.last_activity, reverse=True)
    return result


def get_session_logs(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
    *,
    include_original: bool = False,
) -> list[tuple[uuid.UUID, uuid.UUID, str, str, str | None, bool, str, str | None, datetime, uuid.UUID]] | None:
    chat_ids = _session_chat_ids(session_id, tenant_id, db)
    if not chat_ids:
        return None

    messages = (
        db.query(Message)
        .filter(Message.chat_id.in_(chat_ids))
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    return [
        (
            m.id,
            session_id,
            m.role.value,
            _display_message_content(m, include_original=False),
            _display_message_content(m, include_original=True) if include_original else None,
            _message_original_available(m),
            (m.feedback or MessageFeedback.none).value,
            m.ideal_answer,
            m.created_at,
            # Conversation boundary marker: the dashboard renders a divider
            # whenever chat_id changes between consecutive messages.
            m.chat_id,
        )
        for m in messages
    ]


def delete_session_original_content(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> tuple[Chat | None, int]:
    # Privacy deletion must cover every conversation of the session, not just
    # the latest one — rotated-away chats still hold original content.
    chats = (
        db.query(Chat)
        .filter(
            Chat.session_id == session_id,
            Chat.tenant_id == tenant_id,
        )
        .order_by(Chat.created_at.desc())
        .all()
    )
    if not chats:
        return None, 0
    chat = chats[0]

    messages = (
        db.query(Message)
        .filter(Message.chat_id.in_([c.id for c in chats]))
        .all()
    )
    deleted_count = 0
    for message in messages:
        if message.content_original_encrypted is None:
            continue
        message.content_original_encrypted = None
        message.content = message.content_redacted or ""
        db.add(message)
        deleted_count += 1
    return chat, deleted_count
