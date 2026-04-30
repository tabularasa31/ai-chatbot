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


def get_chat_history(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> list[Message]:
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.tenant_id == tenant_id,
    ).first()
    if not chat:
        return []
    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return list(messages)


def list_chat_sessions(tenant_id: uuid.UUID, db: Session) -> list[SessionSummary]:
    chats = (
        db.query(Chat)
        .filter(Chat.tenant_id == tenant_id)
        .options(joinedload(Chat.messages))
        .all()
    )
    result: list[SessionSummary] = []
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

        result.append(
            SessionSummary(
                session_id=chat.session_id,
                message_count=msg_count,
                last_question=last_question,
                last_answer_preview=last_answer_preview,
                last_activity=last_activity,
            )
        )

    result.sort(key=lambda s: s.last_activity, reverse=True)
    return result


def get_session_logs(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
    *,
    include_original: bool = False,
) -> list[tuple[uuid.UUID, uuid.UUID, str, str, str | None, bool, str, str | None, datetime]] | None:
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.tenant_id == tenant_id,
    ).first()
    if not chat:
        return None

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [
        (
            m.id,
            chat.session_id,
            m.role.value,
            _display_message_content(m, include_original=False),
            _display_message_content(m, include_original=True) if include_original else None,
            _message_original_available(m),
            (m.feedback or MessageFeedback.none).value,
            m.ideal_answer,
            m.created_at,
        )
        for m in messages
    ]


def delete_session_original_content(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> tuple[Chat | None, int]:
    chat = db.query(Chat).filter(
        Chat.session_id == session_id,
        Chat.tenant_id == tenant_id,
    ).first()
    if not chat:
        return None, 0

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
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
