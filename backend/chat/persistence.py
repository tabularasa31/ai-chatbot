"""Message persistence helpers for the chat pipeline."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from backend.chat.language import ResolvedLanguageContext
from backend.chat.language_context import _set_last_response_language
from backend.chat.pii import redact
from backend.contact_sessions.service import record_user_session_turn
from backend.core.crypto import encrypt_value
from backend.models import Chat, Message, MessageRole, PiiEvent, PiiEventDirection

logger = logging.getLogger(__name__)


def _source_docs_for_db(db: Session, document_ids: list[uuid.UUID]) -> list[uuid.UUID] | None:
    return document_ids if "postgresql" in str(db.bind.url) else None


def _create_message(
    db: Session,
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    role: MessageRole,
    content: str,
    source_documents: list[uuid.UUID] | None = None,
    direction: PiiEventDirection = PiiEventDirection.message_storage,
    optional_entity_types: set[str] | None = None,
) -> Message:
    redaction = redact(content, optional_entity_types=optional_entity_types)
    message = Message(
        chat_id=chat.id,
        role=role,
        content=redaction.redacted_text,
        content_original_encrypted=encrypt_value(content),
        content_redacted=redaction.redacted_text,
        source_documents=source_documents,
    )
    db.add(message)
    db.flush()
    if redaction.was_redacted:
        for entity in redaction.entities_found:
            db.add(
                PiiEvent(
                    tenant_id=tenant_id,
                    chat_id=chat.id,
                    message_id=message.id,
                    direction=direction,
                    entity_type=entity.type,
                    count=entity.count,
                )
            )
    return message


def _finalize_persisted_messages(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    extra_tokens: int,
) -> None:
    chat.tokens_used = int(chat.tokens_used or 0) + int(extra_tokens)
    db.add(chat)
    try:
        with db.begin_nested():
            record_user_session_turn(
                db,
                tenant_id=tenant_id,
                user_context=chat.user_context,
                ended_at=chat.ended_at,
            )
    except Exception:
        logger.warning(
            "user_session_turn_tracking_failed: tenant_id=%s session_id=%s",
            tenant_id,
            chat.session_id,
            exc_info=True,
        )
    db.commit()


def _persist_turn(
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    user_content: str,
    assistant_content: str,
    document_ids: list[uuid.UUID],
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
) -> tuple[Message, Message]:
    user_message = _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.user,
        content=user_content,
        optional_entity_types=optional_entity_types,
    )
    assistant_message = _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.assistant,
        content=assistant_content,
        source_documents=_source_docs_for_db(db, document_ids),
        optional_entity_types=optional_entity_types,
    )
    _finalize_persisted_messages(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        extra_tokens=extra_tokens,
    )
    return user_message, assistant_message


def _persist_turn_with_response_language(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    response_language: str | None,
    resolution_reason: str | None,
    user_content: str,
    assistant_content: str,
    document_ids: list[uuid.UUID],
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
    language_context: ResolvedLanguageContext | None = None,
) -> tuple[Message, Message]:
    _set_last_response_language(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        response_language=response_language,
        resolution_reason=resolution_reason,
        language_context=language_context,
    )
    return _persist_turn(
        db,
        chat,
        tenant_id,
        user_content,
        assistant_content,
        document_ids,
        extra_tokens,
        optional_entity_types=optional_entity_types,
    )


def _persist_assistant_message(
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    assistant_content: str,
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
) -> None:
    _create_message(
        db,
        chat=chat,
        tenant_id=tenant_id,
        role=MessageRole.assistant,
        content=assistant_content,
        source_documents=None,
        optional_entity_types=optional_entity_types,
    )
    _finalize_persisted_messages(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        extra_tokens=extra_tokens,
    )


def _persist_assistant_message_with_response_language(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    response_language: str | None,
    resolution_reason: str | None,
    assistant_content: str,
    extra_tokens: int,
    optional_entity_types: set[str] | None = None,
    language_context: ResolvedLanguageContext | None = None,
) -> None:
    _set_last_response_language(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        response_language=response_language,
        resolution_reason=resolution_reason,
        language_context=language_context,
    )
    _persist_assistant_message(
        db,
        chat,
        tenant_id,
        assistant_content,
        extra_tokens,
        optional_entity_types=optional_entity_types,
    )
