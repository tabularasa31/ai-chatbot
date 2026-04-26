"""Language context resolution helpers for the chat pipeline."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from backend.chat.language import (
    STICKY_WINDOW,
    LanguageDetectionResult,
    ResolvedLanguageContext,
    _decide_language_lock,
    resolve_language_context,
)
from backend.core.crypto import decrypt_value
from backend.models import Chat, Message, MessageRole, Tenant, TenantProfile
from backend.support_config import public_support_config_dict

logger = logging.getLogger(__name__)


def _decrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_value(value)
    except RuntimeError:
        logger.warning("Failed to decrypt stored original content")
        return None


def _is_bootstrap_question(text: str) -> bool:
    """Return True when text is empty/whitespace-only — the canonical bootstrap turn test."""
    return not text.strip()


def _resolve_fallback_locale(
    user_context: dict[str, Any] | None,
    browser_locale: str | None = None,
) -> str | None:
    if user_context:
        locale = str(user_context.get("locale") or "").strip()
        if locale:
            return locale
        stored_browser_locale = str(user_context.get("browser_locale") or "").strip()
        if stored_browser_locale:
            return stored_browser_locale
    if browser_locale and browser_locale.strip():
        return browser_locale.strip()
    return None


def _load_recent_user_turn_texts(
    db: Session,
    chat: Chat,
    current_turn_text: str,
    *,
    limit: int,
) -> list[str]:
    recent_rows = (
        db.query(Message.content_original_encrypted, Message.content_redacted, Message.content)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.user)
        .order_by(Message.created_at.desc())
        .limit(max(limit - 1, 0))
        .all()
    )
    historical_texts = []
    for encrypted_original, redacted_content, plain_content in recent_rows:
        historical_texts.append(
            _decrypt_optional(encrypted_original) or redacted_content or plain_content or ""
        )
    texts = [current_turn_text, *historical_texts]
    return [text for text in texts if text and text.strip()][:limit]


def _assistant_turn_index(chat: Chat) -> int:
    return sum(1 for message in (chat.messages or []) if message.role == MessageRole.assistant) + 1


def _user_message_count(chat: Chat) -> int:
    return sum(1 for message in (chat.messages or []) if message.role == MessageRole.user)


def _maybe_lock_language(
    *,
    db: Session,
    chat: Chat,
    language_context: ResolvedLanguageContext,
    previous_response_language: str | None,
) -> None:
    """Set chat.language_locked = True when this turn's detection meets the lock rules."""
    detection = LanguageDetectionResult(
        detected_language=language_context.detected_language,
        confidence=language_context.confidence,
        is_reliable=language_context.is_reliable,
    )
    is_first_user_turn = _user_message_count(chat) == 0
    if not _decide_language_lock(
        detection=detection,
        previous_response_language=previous_response_language,
        is_first_user_turn=is_first_user_turn,
    ):
        return
    chat.language_locked = True
    db.add(chat)
    logger.info(
        "language_locked",
        extra={
            "chat_id": str(chat.id),
            "tenant_id": str(chat.tenant_id),
            "locked_to": chat.last_response_language or detection.detected_language,
            "rule": "first_turn_high_conf" if is_first_user_turn else "two_consistent_turns",
            "detected_language": detection.detected_language,
            "confidence": detection.confidence,
        },
    )


def _set_last_response_language(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    response_language: str | None,
    resolution_reason: str | None,
    language_context: ResolvedLanguageContext | None = None,
) -> None:
    if not response_language:
        return
    previous_language = chat.last_response_language
    if previous_language != response_language:
        logger.info(
            "response_language_changed",
            extra={
                "chat_id": str(chat.id),
                "tenant_id": str(tenant_id),
                "previous": previous_language,
                "next": response_language,
                "reason": resolution_reason,
                "turn_index": _assistant_turn_index(chat),
            },
        )
    chat.last_response_language = response_language
    db.add(chat)
    if (
        language_context is not None
        and not chat.language_locked
        and language_context.response_language_resolution_reason != "locked"
    ):
        _maybe_lock_language(
            db=db,
            chat=chat,
            language_context=language_context,
            previous_response_language=previous_language,
        )


def _resolve_chat_language_context(
    *,
    current_turn_text: str,
    tenant_row: Tenant | None,
    tenant_profile: TenantProfile | None,
    bootstrap_user_locale: str | None,
    browser_locale: str | None,
    is_bootstrap_turn: bool,
    chat: Chat | None = None,
    db: Session | None = None,
) -> ResolvedLanguageContext:
    support_config = public_support_config_dict(
        tenant_row.settings if tenant_row and isinstance(tenant_row.settings, dict) else None
    )
    previous_response_language = chat.last_response_language if chat is not None else None
    recent_user_turn_texts = (
        _load_recent_user_turn_texts(
            db,
            chat,
            current_turn_text,
            limit=STICKY_WINDOW,
        )
        if chat is not None and db is not None
        else [current_turn_text]
    )
    return resolve_language_context(
        current_turn_text=current_turn_text,
        is_bootstrap_turn=is_bootstrap_turn,
        bootstrap_user_locale=bootstrap_user_locale,
        browser_locale=browser_locale,
        tenant_escalation_language=(
            support_config.get("escalation_language")
            or getattr(tenant_profile, "escalation_language", None)
        ),
        previous_response_language=previous_response_language,
        recent_user_turn_texts=recent_user_turn_texts,
        language_locked=bool(getattr(chat, "language_locked", False)) if chat is not None else False,
        tenant_id=getattr(tenant_row, "public_id", None) if tenant_row is not None else None,
        chat_id=str(chat.id) if chat is not None else None,
    )
