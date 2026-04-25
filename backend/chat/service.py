"""Business logic for RAG chat pipeline."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session, joinedload

from backend.chat.decision import (
    MAX_CLARIFICATIONS_PER_SESSION,
    Decision,
)
from backend.chat.handlers import (
    ChatTurnOutcome,
    HandlerContext,
    HandlerRouter,
    default_router,
)
from backend.chat.handlers.rag import (
    ChatPipelineResult,
    RetrievalContext,
    _classify_kb_confidence,
    _emit_quick_answer_lookup_event,
    _lookup_quick_answers,
    _metrics_distinct_id,
    _quick_answer_keys_for_question,
    _quick_answer_quality_score,
    _quick_answers_context,
    _strip_thought_tags,
    _user_context_prompt_line,
    build_rag_messages,
    build_rag_prompt,
    generate_answer,
    retrieve_context,
    run_chat_pipeline,
    validate_answer,
)
from backend.chat.language import (
    STICKY_WINDOW,
    LanguageDetectionResult,
    ResolvedLanguageContext,
    _decide_language_lock,
    resolve_language_context,
)
from backend.chat.pii import redact
from backend.contact_sessions.service import record_user_session_turn, touch_user_session
from backend.core import db as core_db
from backend.core.config import (
    settings,  # noqa: F401  (re-export for monkeypatch via backend.chat.service.settings)
)
from backend.core.crypto import decrypt_value, encrypt_value

# Symbols below marked ``noqa: F401`` are re-exported intentionally: the actual
# call sites moved to ``backend.chat.handlers.rag`` but tests still monkeypatch
# them through ``backend.chat.service.<name>``. handlers/rag.py looks them back
# up via this module so the patches keep taking effect.
from backend.core.openai_client import get_openai_client  # noqa: F401
from backend.escalation.openai_escalation import (
    EscalationLlmResult,
    complete_escalation_openai_turn,  # noqa: F401  (re-export for monkeypatch via backend.chat.service)
)
from backend.escalation.service import (
    build_chat_messages_for_openai,  # noqa: F401  (re-export for monkeypatch)
    create_escalation_ticket,  # noqa: F401  (re-export for monkeypatch)
    detect_human_request,
    fact_from_ticket,  # noqa: F401  (re-export for monkeypatch)
    should_escalate,  # noqa: F401  (re-export for monkeypatch via backend.chat.service)
)
from backend.faq.faq_matcher import match_faq  # noqa: F401  (re-export for monkeypatch)
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.jobs import enqueue_gap_job_for_tenant_best_effort
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.guards.injection_detector import detect_injection  # noqa: F401  (re-export)
from backend.guards.relevance_checker import check_relevance_with_profile  # noqa: F401  (re-export)
from backend.models import (
    Bot,
    Chat,
    Message,
    MessageFeedback,
    MessageRole,
    PiiEvent,
    PiiEventDirection,
    Tenant,
    TenantProfile,
)
from backend.observability import TraceHandle, begin_trace
from backend.observability.metrics import capture_event
from backend.privacy_config import public_redaction_config_dict
from backend.search.service import (
    build_reliability_projection,
    embed_queries,  # noqa: F401  (re-export for monkeypatch via backend.chat.service)
    expand_query,  # noqa: F401  (re-export)
    search_similar_chunks_detailed,  # noqa: F401  (re-export)
    semantic_query_rewrite,  # noqa: F401  (re-export)
)
from backend.support_config import public_support_config_dict

_DISCLOSURE_UNSET: dict | None = object()  # type: ignore[assignment]

PREVIEW_MAX_LEN = 120

logger = logging.getLogger(__name__)

# Re-exports above (RetrievalContext, ChatPipelineResult, run_chat_pipeline,
# retrieve_context, generate_answer, validate_answer, build_rag_prompt,
# build_rag_messages, _classify_kb_confidence, _quick_answer_*,
# _emit_quick_answer_lookup_event, _strip_thought_tags, _user_context_prompt_line,
# _metrics_distinct_id, _lookup_quick_answers, _quick_answers_context) are kept
# at module top because tests rely on them being importable from
# backend.chat.service AND on monkeypatch.setattr("backend.chat.service.X", ...)
# affecting in-module call sites. The handlers/rag.py implementations look these
# back up via this module so the patches still take effect after the move.
__all__ = (  # documentation hint, not enforced
    "ChatPipelineResult",
    "RetrievalContext",
    "_classify_kb_confidence",
    "_emit_quick_answer_lookup_event",
    "_lookup_quick_answers",
    "_metrics_distinct_id",
    "_quick_answer_keys_for_question",
    "_quick_answer_quality_score",
    "_quick_answers_context",
    "_strip_thought_tags",
    "_user_context_prompt_line",
    "build_rag_messages",
    "build_rag_prompt",
    "generate_answer",
    "retrieve_context",
    "run_chat_pipeline",
    "validate_answer",
)

# Pipeline handler chain. PR 1/4 wires only GreetingHandler; subsequent PRs add
# SmallTalk / RAG / Escalation handlers and shrink process_chat_message accordingly.
_HANDLER_ROUTER: HandlerRouter = default_router()


def _emit_chat_turn_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    chat_id: str | None,
    strategy: str,
    reject_reason: str | None,
    is_reject: bool,
    escalated: bool,
    identified: bool = False,
    latency_ms: int | None = None,
    retrieval_ms: int = 0,
    llm_ms: int = 0,
    reliability_score: str | None = None,
    best_confidence_score: float | None = None,
    decision: Decision | None = None,
    escalation_trigger: str | None = None,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        props: dict = {
            "chat_id": chat_id,
            "strategy": strategy,
            "reject_reason": reject_reason,
            "is_reject": is_reject,
            "escalated": escalated,
            "identified": identified,
            "latency_ms": latency_ms,
            "retrieval_ms": retrieval_ms,
            "llm_ms": llm_ms,
            "reliability_score": reliability_score,
            "best_confidence_score": best_confidence_score,
            "escalation_trigger": escalation_trigger,
        }
        if decision is not None:
            props["decision"] = decision.kind.value
            props["decision_reason"] = decision.clarify_reason or decision.escalate_reason or "n/a"
            props["clarify_type"] = decision.clarify_type
            props["clarify_reason"] = decision.clarify_reason
            props["budget_blocked"] = decision.budget_blocked
            props["escalation_reason"] = decision.escalate_reason
        capture_event(
            "chat.turn",
            distinct_id=_metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties=props,
        )
    except Exception:
        logger.warning("Failed to emit chat.turn event", exc_info=True)


def _emit_chat_escalated_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    chat_id: str | None,
    escalation_reason: str,
    escalation_trigger: str | None = None,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        capture_event(
            "chat_escalated",
            distinct_id=_metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "chat_id": chat_id,
                "escalation_reason": escalation_reason,
                "escalation_trigger": escalation_trigger,
            },
        )
    except Exception:
        logger.warning("Failed to emit chat_escalated event", exc_info=True)


def _emit_chat_session_ended_event(
    *,
    tenant_public_id: str | None,
    bot_public_id: str | None,
    chat_id: str | None,
    outcome: str,
) -> None:
    if tenant_public_id is None and bot_public_id is None:
        return
    try:
        capture_event(
            "chat_session_ended",
            distinct_id=_metrics_distinct_id(bot_public_id, tenant_public_id),
            tenant_id=tenant_public_id,
            bot_id=bot_public_id,
            properties={
                "chat_id": chat_id,
                "outcome": outcome,
            },
        )
    except Exception:
        logger.warning("Failed to emit chat_session_ended event", exc_info=True)


def _trace_event(trace: TraceHandle | None, name: str, metadata: dict[str, Any]) -> None:
    if trace is None:
        return
    trace.span(name=name, metadata=metadata).end(output=metadata)


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


def _is_bootstrap_question(text: str) -> bool:
    """Return True when *text* is empty/whitespace-only — the canonical test for a bootstrap turn.

    Centralised here so that ``run_chat_pipeline``'s fallback resolver and any
    other standalone caller share the same definition instead of inlining
    ``not text.strip()`` in multiple places.
    """
    return not text.strip()


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
    # When this turn was driven by a real detection (language_context provided
    # and not from the locked fast path), decide whether to lock the chat's
    # language now. See _decide_language_lock for the rules.
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


def _user_message_count(chat: Chat) -> int:
    return sum(
        1 for message in (chat.messages or []) if message.role == MessageRole.user
    )


def _maybe_lock_language(
    *,
    db: Session,
    chat: Chat,
    language_context: ResolvedLanguageContext,
    previous_response_language: str | None,
) -> None:
    """Set chat.language_locked = True when this turn's detection meets the
    lock rules. Idempotent — caller is expected to skip already-locked chats.
    """
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


def _escalation_turn_response(
    *,
    db: Session,
    chat: Chat,
    tenant_id: uuid.UUID,
    language_context: ResolvedLanguageContext,
    question: str,
    out: EscalationLlmResult,
    optional_entity_types: set[str] | None,
    trace: TraceHandle,
    trace_source: str,
    chat_ended: bool,
    escalated: bool,
    ticket_number: str | None = None,
) -> ChatTurnOutcome:
    """Persist an escalation turn and return the outcome. Single commit for all mutations.

    The user-facing message is always written in ``response_language`` (the
    user's language), not in ``escalation_language``. ``escalation_language``
    is the tenant-side artifact language (ticket text / support team) and
    must not leak into the chat reply.
    """
    _persist_turn_with_response_language(
        db=db,
        chat=chat,
        tenant_id=tenant_id,
        response_language=language_context.response_language,
        resolution_reason=language_context.response_language_resolution_reason,
        user_content=question,
        assistant_content=out.message_to_user,
        document_ids=[],
        extra_tokens=out.tokens_used,
        optional_entity_types=optional_entity_types,
        language_context=language_context,
    )
    trace.update(
        output={"answer": out.message_to_user, "source": trace_source},
        metadata={
            "chat_ended": chat_ended,
            "escalated": escalated,
            "response_language": language_context.response_language,
            "escalation_language": language_context.escalation_language,
        },
    )
    return ChatTurnOutcome(
        text=out.message_to_user,
        document_ids=[],
        tokens_used=out.tokens_used,
        chat_ended=chat_ended,
        ticket_number=ticket_number,
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



def _source_docs_for_db(db: Session, document_ids: list[uuid.UUID]) -> list[uuid.UUID] | None:
    return document_ids if "postgresql" in str(db.bind.url) else None


def _tenant_optional_entity_types(tenant: Tenant | None) -> set[str] | None:
    if not tenant:
        return None
    raw = tenant.settings if isinstance(tenant.settings, dict) else None
    cfg = public_redaction_config_dict(raw)
    return set(cfg["optional_entity_types"])


def _decrypt_optional(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_value(value)
    except RuntimeError:
        logger.warning("Failed to decrypt stored original content")
        return None


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


def _try_ingest_gap_signal(
    *,
    chat: Chat,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    user_message: Message,
    assistant_message: Message,
    question_text: str,
    answer_confidence: float | None,
    was_rejected: bool,
    had_fallback: bool,
    was_escalated: bool,
    language: str | None = None,
) -> None:
    ingestion_db = core_db.SessionLocal()
    try:
        orchestrator = GapAnalyzerOrchestrator(
            repository=SqlAlchemyGapAnalyzerRepository(ingestion_db)
        )
        orchestrator.ingest_signal(
            GapSignal(
                tenant_id=tenant_id,
                chat_id=chat.id,
                session_id=session_id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_message.id,
                question_text=question_text,
                answer_confidence=answer_confidence,
                was_rejected=was_rejected,
                had_fallback=had_fallback,
                was_escalated=was_escalated,
                user_thumbed_down=False,
                language=language,
            )
        )
        ingestion_db.commit()
        _start_mode_b_followup(tenant_id)
    except ValueError:
        ingestion_db.rollback()
        logger.warning(
            "gap_analyzer_signal_ingestion_contract_failed: tenant_id=%s session_id=%s assistant_message_id=%s",
            tenant_id,
            session_id,
            assistant_message.id,
            exc_info=True,
        )
    except Exception:
        ingestion_db.rollback()
        logger.exception(
            "gap_analyzer_signal_ingestion_failed: tenant_id=%s session_id=%s assistant_message_id=%s",
            tenant_id,
            session_id,
            assistant_message.id,
        )
    finally:
        ingestion_db.close()


def _start_mode_b_followup(tenant_id: uuid.UUID) -> None:
    enqueue_gap_job_for_tenant_best_effort(
        tenant_id,
        job_kind=GapJobKind.mode_b,
        trigger="chat_signal",
    )


def record_gap_feedback_for_message(
    *,
    db: Session,
    tenant_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
    feedback_value: str,
) -> bool:
    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db))
    return orchestrator.record_assistant_feedback(
        tenant_id=tenant_id,
        assistant_message_id=assistant_message_id,
        feedback_value=feedback_value,
    )


def _trigger_log_analysis_threshold(
    tenant_id: uuid.UUID,
    api_key: str,
) -> None:
    """Increment message counter and enqueue analysis job if threshold is reached.

    Opens its own DB session inside the daemon thread — never touches the
    request-scoped session, which is not thread-safe.
    """
    import threading

    def _run() -> None:
        try:
            from backend.jobs.analyze_chat_logs import increment_and_check_threshold

            increment_and_check_threshold(tenant_id=tenant_id, api_key=api_key)
        except Exception:
            logger.debug("Log analysis threshold check failed", exc_info=True)

    threading.Thread(target=_run, daemon=True).start()


def process_chat_message(
    tenant_id: uuid.UUID,
    question: str,
    session_id: uuid.UUID,
    db: Session,
    *,
    api_key: str,
    user_context: dict | None = None,
    browser_locale: str | None = None,
    disclosure_config: dict | None = _DISCLOSURE_UNSET,  # type: ignore[assignment]
    bot_id: uuid.UUID | None = None,
    bot_public_id: str | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> ChatTurnOutcome:
    """
    RAG pipeline with FI-ESC escalation state machine.

    Returns:
        Typed turn outcome. The object is also iterable for legacy tuple-style callers.
    """
    _turn_started_at = perf_counter()
    tenant_row = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    optional_entity_types = _tenant_optional_entity_types(tenant_row)
    redaction = redact(question, optional_entity_types=optional_entity_types)
    redacted_question = redaction.redacted_text

    chat = (
        db.query(Chat)
        .options(joinedload(Chat.messages))
        .filter(Chat.session_id == session_id, Chat.tenant_id == tenant_id)
        .first()
    )

    effective_user_ctx: dict | None = None
    if chat and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    elif user_context:
        effective_user_ctx = dict(user_context)

    if not chat:
        uc: dict | None = None
        if effective_user_ctx:
            uc = dict(effective_user_ctx)
        if browser_locale:
            uc = dict(uc or {})
            uc.setdefault("browser_locale", browser_locale)
        chat = Chat(
            tenant_id=tenant_id,
            bot_id=bot_id,
            session_id=session_id,
            user_context=uc,
        )
        db.add(chat)
        db.flush()
        touch_user_session(
            db,
            tenant_id=tenant_id,
            user_context=chat.user_context,
            started_at=chat.created_at,
        )
        db.commit()
        db.refresh(chat)
    else:
        chat_updated = False
        if bot_id is not None:
            if chat.bot_id is None:
                chat.bot_id = bot_id
                chat_updated = True
            elif chat.bot_id != bot_id:
                raise ValueError("Session belongs to another bot")
        if browser_locale and not (chat.user_context or {}).get("browser_locale"):
            ctx = dict(chat.user_context or {})
            ctx["browser_locale"] = browser_locale
            chat.user_context = ctx
            chat_updated = True
        if chat_updated:
            db.add(chat)
            db.commit()
            db.refresh(chat)

    if effective_user_ctx is None and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    tenant_profile = (
        db.query(TenantProfile).filter(TenantProfile.tenant_id == tenant_id).first()
        if tenant_row is not None
        else None
    )
    question_text = question.strip()
    # A session is "new" when it has no messages at all (neither user nor assistant).
    # Using bool(chat.messages) rather than checking for a prior user message avoids
    # creating a phantom empty user-message row during bootstrap persistence — the
    # assistant greeting alone is enough evidence that bootstrap already occurred.
    is_new_session = not chat.messages
    language_context = _resolve_chat_language_context(
        current_turn_text=question_text,
        tenant_row=tenant_row,
        tenant_profile=tenant_profile,
        is_bootstrap_turn=_is_bootstrap_question(question_text) and is_new_session,
        bootstrap_user_locale=(effective_user_ctx or {}).get("locale"),
        browser_locale=(effective_user_ctx or {}).get("browser_locale") or browser_locale,
        chat=chat,
        db=db,
    )

    explicit_human_request_raw = detect_human_request(redacted_question, api_key)

    trace = begin_trace(
        name="rag-query",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        user_id=str((effective_user_ctx or {}).get("user_id")) if effective_user_ctx else None,
        input=redacted_question or None,
        metadata={
            "tenant_id": str(tenant_id),
            "session_id": str(session_id),
            "chat_id": str(chat.id),
            "browser_locale": browser_locale,
            "question": redacted_question,
            "has_user_context": bool(effective_user_ctx),
            "detected_language": language_context.detected_language,
            "confidence": language_context.confidence,
            "is_reliable": language_context.is_reliable,
            "response_language": language_context.response_language,
            "response_language_resolution_reason": language_context.response_language_resolution_reason,
            "escalation_language": language_context.escalation_language,
            "escalation_language_source": language_context.escalation_language_source,
        },
        tags=[f"tenant:{tenant_id}"],
        force_trace=explicit_human_request_raw,
    )

    # Empty input on an established session is invalid — reject before dispatch
    # so handlers don't have to validate this themselves.
    if not question_text and not is_new_session:
        raise ValueError("Question is required")

    explicit_human_request = detect_human_request(redacted_question, api_key)

    # Resolve the bot row once: handlers need disclosure config and agent
    # instructions, both of which are tied to the active bot.
    _resolved_bot: Bot | None = None
    if bot_id is not None:
        _resolved_bot = db.query(Bot).filter(Bot.id == bot_id, Bot.tenant_id == tenant_id).first()
    if _resolved_bot is None:
        _resolved_bot = (
            db.query(Bot)
            .filter(Bot.tenant_id == tenant_id, Bot.is_active.is_(True))
            .order_by(Bot.created_at.asc())
            .first()
        )
    if disclosure_config is _DISCLOSURE_UNSET:
        disclosure_config = (
            _resolved_bot.disclosure_config
            if _resolved_bot and isinstance(_resolved_bot.disclosure_config, dict)
            else None
        )
    disclosure_cfg: dict[str, Any] | None = disclosure_config if isinstance(disclosure_config, dict) else None

    handler_ctx = HandlerContext(
        tenant_id=tenant_id,
        chat=chat,
        tenant_row=tenant_row,
        tenant_profile=tenant_profile,
        question=question,
        redacted_question=redacted_question,
        question_text=question_text,
        language_context=language_context,
        api_key=api_key,
        optional_entity_types=optional_entity_types,
        is_new_session=is_new_session,
        trace=trace,
        db=db,
        session_id=session_id,
        user_context=user_context,
        effective_user_ctx=effective_user_ctx,
        bot_public_id=bot_public_id,
        bot_id=bot_id,
        bot=_resolved_bot,
        bot_agent_instructions=_resolved_bot.agent_instructions if _resolved_bot else None,
        disclosure_config=disclosure_cfg,
        allow_clarification=chat.clarification_count < MAX_CLARIFICATIONS_PER_SESSION,
        user_context_line=_user_context_prompt_line(effective_user_ctx),
        stream_callback=stream_callback,
        explicit_human_request=explicit_human_request,
        turn_started_at=_turn_started_at,
    )

    outcome = _HANDLER_ROUTER.dispatch(handler_ctx)
    if outcome is None:
        # Unreachable in normal operation: RagHandler is the catch-all and
        # always produces an outcome for non-empty input. If we reach here
        # the handler chain has been misconfigured.
        raise RuntimeError("Pipeline router produced no outcome for chat turn")
    return outcome


def run_debug(
    tenant_id: uuid.UUID,
    question: str,
    db: Session,
    *,
    api_key: str,
) -> tuple[str, int, dict]:
    """
    Run full RAG pipeline for debug purposes — no DB persistence, no escalation,
    no observability side effects.

    Mirrors the public chat pipeline (injection guard → FAQ → relevance →
    retrieve → generate → validate) via run_chat_pipeline, so debug responses
    match production decisions for guard/FAQ/RAG scenarios.

    Structured clarification is currently disabled. The model may still ask
    a clarifying question in plain text, and debug should reflect that as a
    normal answer.

    Returns:
        Tuple of (final_answer, tokens_used, debug_dict).
        debug_dict includes strategy, reject_reason, validation_outcome,
        raw_answer vs final_answer, retrieval details, and validation payload.
    """
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

    result = run_chat_pipeline(
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


def get_chat_history(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> list[Message]:
    """
    Get all messages for a chat session (ownership enforced).

    Args:
        session_id: Chat session ID.
        tenant_id: Tenant ID for ownership check.
        db: Database session.

    Returns:
        List of Message objects, or empty list if not found/not owner.
    """
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


@dataclass
class SessionSummary:
    """Summary of a chat session for inbox list."""

    session_id: uuid.UUID
    message_count: int
    last_question: str | None
    last_answer_preview: str | None
    last_activity: datetime


def list_chat_sessions(tenant_id: uuid.UUID, db: Session) -> list[SessionSummary]:
    """
    List all chat sessions for a tenant, sorted by last_activity DESC.

    Args:
        tenant_id: Tenant ID for tenant isolation.
        db: Database session.

    Returns:
        List of SessionSummary, sorted by last_activity descending.
    """
    # N+1 fix: joinedload eager-loads messages in one query instead of N queries per chat
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
        last_activity = datetime.min
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

        if msg_count > 0:
            result.append(
                SessionSummary(
                    session_id=chat.session_id,
                    message_count=msg_count,
                    last_question=last_question,
                    last_answer_preview=last_answer_preview,
                    last_activity=last_activity,
                )
            )
        else:
            result.append(
                SessionSummary(
                    session_id=chat.session_id,
                    message_count=0,
                    last_question=None,
                    last_answer_preview=None,
                    last_activity=chat.created_at or datetime.min,
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
    """
    Get all messages for a session (ownership enforced).

    Args:
        session_id: Chat session ID.
        tenant_id: Tenant ID for ownership check.
        db: Database session.

    Returns:
        List of tuples with safe content, optional original content, availability,
        feedback, ideal_answer, created_at or None if not found.
    """
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
