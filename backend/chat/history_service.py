"""Chat history, session listing, and debug pipeline helpers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, joinedload

from backend.chat.language_context import (
    _decrypt_optional,
    _is_bootstrap_question,
    _resolve_chat_language_context,
)
from backend.models import (
    Bot,
    Chat,
    Message,
    MessageFeedback,
    MessageRole,
    Tenant,
    TenantProfile,
)
from backend.privacy_config import public_redaction_config_dict
from backend.search.service import build_reliability_projection

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


def run_debug(
    tenant_id: uuid.UUID,
    question: str,
    db: Session,
    *,
    api_key: str,
) -> tuple[str, int, dict]:
    """Run full RAG pipeline for debug purposes — no DB persistence, no side effects."""
    from backend.chat.pii import redact

    tenant_row = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    optional_entity_types = _tenant_optional_entity_types(tenant_row)
    redacted_question = redact(
        question,
        optional_entity_types=optional_entity_types,
    ).redacted_text

    first_bot = (
        db.query(Bot)
        .filter(Bot.tenant_id == tenant_id, Bot.is_active.is_(True))
        .order_by(Bot.created_at.asc())
        .first()
    )
    debug_disclosure_cfg: dict[str, Any] | None = (
        first_bot.disclosure_config
        if first_bot and isinstance(first_bot.disclosure_config, dict)
        else None
    )
    debug_agent_instructions: str | None = first_bot.agent_instructions if first_bot else None

    tenant_profile = (
        db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant_id).first()
        if tenant_row is not None
        else None
    )
    language_context = _resolve_chat_language_context(
        current_turn_text=redacted_question,
        tenant_row=tenant_row,
        tenant_profile=tenant_profile,
        is_bootstrap_turn=_is_bootstrap_question(redacted_question),
        bootstrap_user_locale=None,
        browser_locale=None,
    )

    # Use _svc lookup so that tests patching backend.chat.service.run_chat_pipeline
    # continue to work (service re-exports run_chat_pipeline from handlers.rag).
    from backend.chat import service as _svc
    result = _svc.run_chat_pipeline(
        tenant_id,
        redacted_question,
        db,
        api_key=api_key,
        language_context=language_context,
        disclosure_config=debug_disclosure_cfg,
        agent_instructions=debug_agent_instructions,
    )
    retrieval = result.retrieval
    if retrieval is not None:
        chunks_debug = [
            {
                "document_id": str(doc_id),
                "score": score,
                "preview": (text[:200] + "..." if len(text) > 200 else text),
            }
            for doc_id, score, text in zip(
                retrieval.document_ids, retrieval.scores, retrieval.chunk_texts, strict=True
            )
        ]
        reliability_projection = build_reliability_projection(retrieval.reliability)
        debug: dict[str, Any] = {
            "mode": retrieval.mode,
            "best_rank_score": retrieval.best_rank_score,
            "best_confidence_score": retrieval.best_confidence_score,
            "confidence_source": retrieval.confidence_source,
            **reliability_projection,
            "chunks": chunks_debug,
        }
    else:
        debug = {
            "mode": "none",
            "best_rank_score": None,
            "best_confidence_score": None,
            "confidence_source": None,
            "reliability": None,
            "chunks": [],
        }

    debug["validation"] = result.validation
    debug["strategy"] = result.strategy
    debug["reject_reason"] = result.reject_reason
    debug["is_reject"] = result.is_reject
    debug["is_faq_direct"] = result.is_faq_direct
    debug["validation_applied"] = result.validation_applied
    debug["validation_outcome"] = result.validation_outcome
    debug["raw_answer"] = result.raw_answer
    debug["detected_language"] = language_context.detected_language
    debug["confidence"] = language_context.confidence
    debug["is_reliable"] = language_context.is_reliable
    debug["response_language"] = language_context.response_language
    debug["response_language_resolution_reason"] = (
        language_context.response_language_resolution_reason
    )
    debug["escalation_language"] = language_context.escalation_language
    debug["escalation_language_source"] = language_context.escalation_language_source

    final_text = result.final_answer
    total_tokens_used = result.tokens_used
    return (final_text, total_tokens_used, debug)
