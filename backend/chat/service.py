"""Business logic for RAG chat pipeline."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from backend.chat.decision import (
    MAX_CLARIFICATIONS_PER_SESSION,
    Decision,  # noqa: F401  (re-export for type hints in callers)
)
from backend.chat.events import (
    _emit_chat_completed_event,  # noqa: F401  (re-export)
    _emit_chat_escalated_event,  # noqa: F401  (re-export — handlers access via _svc.*)
    _emit_chat_feedback_event,  # noqa: F401  (re-export)
    _emit_chat_session_ended_event,  # noqa: F401  (re-export)
    _emit_chat_turn_event,  # noqa: F401  (re-export)
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
    _async_lookup_quick_answers,  # noqa: F401  (re-export for monkeypatch)
    _classify_kb_confidence,
    _emit_quick_answer_lookup_event,
    _lookup_quick_answers,
    _metrics_distinct_id,
    _quick_answer_keys_for_question,
    _quick_answer_quality_score,
    _quick_answers_context,
    _strip_thought_tags,
    _user_context_prompt_line,
    async_run_chat_pipeline,
    build_rag_messages,
    build_rag_prompt,
    generate_answer,
    retrieve_context,
    validate_answer,
)
from backend.chat.history_service import (
    PREVIEW_MAX_LEN,  # noqa: F401  (re-export)
    SessionSummary,  # noqa: F401  (re-export for test imports)
    _tenant_optional_entity_types,
    delete_session_original_content,  # noqa: F401  (re-export for test imports)
    get_chat_history,  # noqa: F401  (re-export for test imports)
    get_session_logs,  # noqa: F401  (re-export for test imports)
    list_chat_sessions,  # noqa: F401  (re-export for test imports)
)
from backend.chat.language import (
    ResolvedLanguageContext,
)
from backend.chat.language_context import (
    _assistant_turn_index,  # noqa: F401  (re-export — handlers access via _svc.*)
    _decrypt_optional,  # noqa: F401  (re-export)
    _is_bootstrap_question,
    _load_recent_user_turn_texts,  # noqa: F401  (re-export)
    _maybe_lock_language,  # noqa: F401  (re-export)
    _resolve_chat_language_context,
    _resolve_fallback_locale,  # noqa: F401  (re-export for test imports via backend.chat.service)
    _set_last_response_language,  # noqa: F401  (re-export — escalation handler accesses via _svc.*)
    _user_message_count,  # noqa: F401  (re-export)
)
from backend.chat.persistence import (
    _create_message,  # noqa: F401  (re-export)
    _finalize_persisted_messages,  # noqa: F401  (re-export)
    _persist_assistant_message,  # noqa: F401  (re-export — greeting handler lazy-imports via service)
    _persist_assistant_message_with_response_language,  # noqa: F401  (re-export)
    _persist_turn,  # noqa: F401  (re-export — escalation handler accesses via _svc.*)
    _persist_turn_with_response_language,
    _source_docs_for_db,  # noqa: F401  (re-export)
)
from backend.chat.pii import redact
from backend.contact_sessions.service import touch_user_session
from backend.core import db as core_db
from backend.core.config import (
    settings,  # noqa: F401  (re-export for monkeypatch via backend.chat.service.settings)
)
from backend.core.db import run_sync

# Symbols below are re-exported so that tests can monkeypatch them through
# ``backend.chat.service.<name>`` and the lazy ``_svc.*`` lookups in handlers
# still see the patched versions.
from backend.core.openai_client import get_async_openai_client, get_openai_client  # noqa: F401
from backend.core.openai_retry import async_call_openai_with_retry  # noqa: F401
from backend.escalation.openai_escalation import (
    EscalationLlmResult,
    complete_escalation_openai_turn,  # noqa: F401
)
from backend.escalation.service import (
    build_chat_messages_for_openai,  # noqa: F401
    create_escalation_ticket,  # noqa: F401
    detect_human_request,
    fact_from_ticket,  # noqa: F401
    should_escalate,  # noqa: F401
)
from backend.faq.faq_matcher import async_match_faq, match_faq  # noqa: F401
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.jobs import enqueue_gap_job_for_tenant_best_effort
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.guards.injection_detector import (
    async_detect_injection,  # noqa: F401
    detect_injection,  # noqa: F401
)
from backend.guards.relevance_checker import (
    async_check_relevance_with_profile,  # noqa: F401
    check_relevance_with_profile,  # noqa: F401
)
from backend.models import (
    Bot,
    Chat,
    Message,
    MessageRole,  # noqa: F401  (re-export)
    Tenant,
    TenantProfile,
)
from backend.observability import TraceHandle, begin_trace
from backend.observability.metrics import capture_event  # noqa: F401  (re-export for monkeypatch)
from backend.search.service import (
    async_detect_tenant_kb_scripts,  # noqa: F401
    async_embed_queries,  # noqa: F401
    async_semantic_query_rewrite,  # noqa: F401
    async_semantic_query_rewrite_for_kb,  # noqa: F401
    embed_queries,  # noqa: F401
    expand_query,  # noqa: F401
    search_similar_chunks_detailed,  # noqa: F401
    semantic_query_rewrite,  # noqa: F401
)

_DISCLOSURE_UNSET: dict | None = object()  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Re-exports above are kept at module top so that tests can monkeypatch them
# through ``backend.chat.service.<name>`` and handlers looking them up via
# the lazy ``_svc = from backend.chat import service`` pattern still see the patches.
__all__ = (
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
    "validate_answer",
)

_HANDLER_ROUTER: HandlerRouter = default_router()


def _trace_event(trace: TraceHandle | None, name: str, metadata: dict[str, Any]) -> None:
    if trace is None:
        return
    trace.span(name=name, metadata=metadata).end(output=metadata)


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
        trace=trace,
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
    import threading

    def _run() -> None:
        try:
            from backend.jobs.analyze_chat_logs import (
                increment_and_check_threshold,
            )
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
    """Sync entry point retained **only** for legacy tests that drive the
    chat pipeline synchronously.

    Production callers (the chat HTTP route, widget streaming) use
    :func:`async_process_chat_message` directly. New tests should do the same
    via the ``async_db_session`` fixture. This wrapper is a thin compat shim
    around the async orchestrator with two non-obvious requirements:

    1. ``db`` (the caller's sync ``Session``) must be bound to the same engine
       as ``core_db.engine`` — i.e. the test conftest must have rebound
       ``core_db.SessionLocal`` and ``core_db.AsyncSessionLocal`` to share the
       same underlying database (the ``tenant`` fixture does this). The wrapper
       deliberately does **not** derive an ``AsyncSession`` from ``db``: SQLite
       prevents multiplexing one connection across sync and asyncio drivers, so
       it opens its own ``AsyncSession`` from ``AsyncSessionLocal``. If those
       two engines point at different databases the call will silently write
       to one and the caller will read from the other; the assertion below
       catches the most common form of that mismatch.
    2. There must be no running event loop. ``asyncio.run`` raises
       ``RuntimeError`` if called from inside one — but pytest sync tests have
       no loop, and async tests should not call this wrapper at all (they can
       ``await async_process_chat_message`` directly).

    Caller's session is mutated:

    - Committed on entry so SQLite releases locks before the async pipeline
      opens its own connection (otherwise concurrent writes deadlock). **Any
      uncommitted state staged on ``db`` before the call is therefore
      persisted** — tests that staged data without committing must be aware.
    - ``expire_all()``-ed on exit so subsequent reads reflect the writes the
      pipeline made on its own connection (any in-memory ORM objects the
      caller held are also invalidated and re-loaded on next access).
    """
    if db is not None:
        # Defensive check: the wrapper relies on the conftest rebinding both
        # SessionLocal and AsyncSessionLocal to the same engine. If callers
        # pass a session bound elsewhere, surface it loudly instead of silently
        # writing to the wrong DB.
        if db.get_bind() is not core_db.engine:
            raise RuntimeError(
                "process_chat_message: caller's Session is bound to an engine "
                "that differs from core_db.engine. The sync wrapper opens its "
                "own AsyncSession against core_db.AsyncSessionLocal, so writes "
                "would go to a different database than the caller's reads. "
                "Use async_process_chat_message directly, or rebind "
                "core_db.AsyncSessionLocal in your fixture (the tenant fixture "
                "in tests/conftest.py shows the pattern)."
            )

        # Flush + release any locks held by the caller's session before the
        # async pipeline opens its own connection. Without this, concurrent
        # SQLite writes raise "database is locked".
        db.commit()

    async def _run() -> ChatTurnOutcome:
        async with core_db.AsyncSessionLocal() as async_db:
            return await async_process_chat_message(
                tenant_id,
                question,
                session_id,
                async_db,
                api_key=api_key,
                user_context=user_context,
                browser_locale=browser_locale,
                disclosure_config=disclosure_config,
                bot_id=bot_id,
                bot_public_id=bot_public_id,
                stream_callback=stream_callback,
            )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no running loop — the expected case
    else:
        raise RuntimeError(
            "process_chat_message must not be called from a running event "
            "loop; call async_process_chat_message directly instead."
        )

    try:
        return asyncio.run(_run())
    finally:
        if db is not None:
            db.expire_all()


async def _ensure_chat_async(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    bot_id: uuid.UUID | None,
    user_context: dict | None,
    browser_locale: str | None,
) -> tuple[Chat, dict | None]:
    """Async counterpart of :func:`_ensure_chat`.

    Loads the Chat row via AsyncSession (``selectinload`` for messages) and
    falls back to creating a new one when none exists.
    """
    result = await db.execute(
        select(Chat)
        .options(selectinload(Chat.messages))
        .where(Chat.session_id == session_id, Chat.tenant_id == tenant_id)
    )
    chat = result.scalar_one_or_none()

    effective_user_ctx: dict | None = None
    if chat and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    elif user_context:
        effective_user_ctx = dict(user_context)

    if not chat:
        uc: dict | None = dict(effective_user_ctx) if effective_user_ctx else None
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
        await db.flush()
        # touch_user_session is sync; call via run_sync for greenlet context.
        await run_sync(
            db,
            lambda s: touch_user_session(
                s,
                tenant_id=tenant_id,
                user_context=chat.user_context,
                started_at=chat.created_at,
            ),
        )
        await db.commit()
        # Re-query with selectinload so chat.messages is eagerly loaded.
        _res = await db.execute(
            select(Chat).options(selectinload(Chat.messages)).where(Chat.id == chat.id)
        )
        chat = _res.scalar_one()
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
            await db.commit()
            # Re-query with selectinload so chat.messages is eagerly loaded.
            _res = await db.execute(
                select(Chat).options(selectinload(Chat.messages)).where(Chat.id == chat.id)
            )
            chat = _res.scalar_one()

    if effective_user_ctx is None and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    return chat, effective_user_ctx


async def _build_handler_context_async(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    tenant_row: Tenant | None,
    tenant_profile: TenantProfile | None,
    chat: Chat,
    question: str,
    redacted_question: str,
    question_text: str,
    language_context: ResolvedLanguageContext,
    api_key: str,
    optional_entity_types: set[str] | None,
    is_new_session: bool,
    trace: TraceHandle,
    session_id: uuid.UUID,
    user_context: dict | None,
    effective_user_ctx: dict | None,
    bot_public_id: str | None,
    bot_id: uuid.UUID | None,
    disclosure_config: dict | None,
    allow_clarification: bool,
    stream_callback: Callable[[str], None] | None,
    explicit_human_request: bool,
    turn_started_at: float,
) -> HandlerContext:
    """Async counterpart of :func:`_build_handler_context`.

    Queries the Bot table via AsyncSession. ``HandlerContext.async_db`` is
    populated by ``_async_dispatch`` right before handlers run; ``ctx.db``
    (sync) is populated by each handler's internal ``run_sync`` block.
    """
    resolved_bot: Bot | None = None
    if bot_id is not None:
        result = await db.execute(
            select(Bot).where(Bot.id == bot_id, Bot.tenant_id == tenant_id)
        )
        resolved_bot = result.scalar_one_or_none()
    if resolved_bot is None:
        result = await db.execute(
            select(Bot)
            .where(Bot.tenant_id == tenant_id, Bot.is_active.is_(True))
            .order_by(Bot.created_at.asc())
        )
        resolved_bot = result.scalars().first()

    if disclosure_config is _DISCLOSURE_UNSET:
        disclosure_config = (
            resolved_bot.disclosure_config
            if resolved_bot and isinstance(resolved_bot.disclosure_config, dict)
            else None
        )
    disclosure_cfg: dict[str, Any] | None = (
        disclosure_config if isinstance(disclosure_config, dict) else None
    )

    # ``db`` is intentionally left at the dataclass default (``None``); the
    # actual sync ``Session`` is set by ``_async_dispatch._run_handler`` right
    # before each handler runs (inside ``AsyncSession.run_sync``).
    return HandlerContext(
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
        session_id=session_id,
        user_context=user_context,
        effective_user_ctx=effective_user_ctx,
        bot_public_id=bot_public_id,
        bot_id=bot_id,
        bot=resolved_bot,
        bot_agent_instructions=resolved_bot.agent_instructions if resolved_bot else None,
        disclosure_config=disclosure_cfg,
        allow_clarification=allow_clarification,
        user_context_line=_user_context_prompt_line(effective_user_ctx),
        stream_callback=stream_callback,
        explicit_human_request=explicit_human_request,
        turn_started_at=turn_started_at,
    )


async def _async_dispatch(ctx: HandlerContext, db: AsyncSession) -> ChatTurnOutcome | None:
    """Async handler dispatch.

    For RagHandler: async pipeline runs first (concurrent guards/embed),
    result stashed in ``ctx.extras['_pipeline_result']`` so the handler
    body picks it up. Every handler is invoked via ``await handler.handle()``;
    each handler is responsible for its own sync/async bridging (currently
    via an internal ``run_sync`` wrapper around its persistence body).
    """
    from backend.chat.handlers.rag import RagHandler

    ctx.async_db = db
    for handler in _HANDLER_ROUTER.handlers:
        if not handler.can_handle(ctx):
            continue
        if isinstance(handler, RagHandler):
            pipeline_result = await async_run_chat_pipeline(
                ctx.tenant_id,
                ctx.question,
                db,
                api_key=ctx.api_key,
                language_context=ctx.language_context,
                user_context_line=ctx.user_context_line,
                disclosure_config=ctx.disclosure_config,
                trace=ctx.trace,
                tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
                bot_public_id=ctx.bot_public_id,
                retry_bot_id=str(ctx.bot_id) if ctx.bot_id else None,
                chat_id=str(ctx.chat.id),
                chat=ctx.chat,
                stream_callback=ctx.stream_callback,
                agent_instructions=ctx.bot_agent_instructions,
                allow_clarification=ctx.allow_clarification,
                guard_profile=ctx.tenant_profile,
            )
            ctx.extras["_pipeline_result"] = pipeline_result

        outcome = await handler.handle(ctx)
        if outcome is not None:
            return outcome
    return None


async def async_process_chat_message(
    tenant_id: uuid.UUID,
    question: str,
    session_id: uuid.UUID,
    db: AsyncSession,
    *,
    api_key: str,
    user_context: dict | None = None,
    browser_locale: str | None = None,
    disclosure_config: dict | None = _DISCLOSURE_UNSET,  # type: ignore[assignment]
    bot_id: uuid.UUID | None = None,
    bot_public_id: str | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> ChatTurnOutcome:
    """Async counterpart of :func:`process_chat_message`.

    Replaces the ``_GUARD_POOL`` (ThreadPoolExecutor) in the RAG path with
    ``asyncio.create_task`` so guard checks, embedding, and retrieval run
    concurrently on the event loop without blocking OS threads.

    Non-RAG handlers (Greeting, SmallTalk, Escalation) are dispatched via
    ``asyncio.to_thread`` using the sync session (``db.sync_session``) so
    their DB operations do not need to be migrated in this PR.
    """
    _turn_started_at = perf_counter()

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant_row = tenant_result.scalar_one_or_none()
    optional_entity_types = _tenant_optional_entity_types(tenant_row)
    redacted_question = redact(question, optional_entity_types=optional_entity_types).redacted_text

    explicit_human_request = detect_human_request(redacted_question, api_key)

    trace = begin_trace(
        name="rag-query",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        input=redacted_question or None,
        metadata={"tenant_id": str(tenant_id), "session_id": str(session_id)},
        tags=[f"tenant:{tenant_id}"],
        force_trace=explicit_human_request,
    )

    _setup_start = perf_counter()
    _setup_span = trace.span(
        name="chat_setup",
        input={"session_id": str(session_id), "has_bot_id": bot_id is not None},
    )
    chat, effective_user_ctx = await _ensure_chat_async(
        db, tenant_id, session_id, bot_id, user_context, browser_locale
    )
    tenant_profile = (
        await db.get(TenantProfile, tenant_id) if tenant_row is not None else None
    )
    _setup_span.end(
        output={"is_new_session": not chat.messages, "chat_id": str(chat.id)},
        metadata={"duration_ms": round((perf_counter() - _setup_start) * 1000, 2)},
    )

    question_text = question.strip()
    is_new_session = not chat.messages

    _lang_start = perf_counter()
    _lang_span = trace.span(
        name="language_detect",
        input={"question_preview": question_text[:80]},
    )
    language_context = await run_sync(
        db,
        lambda s: _resolve_chat_language_context(
            current_turn_text=question_text,
            tenant_row=tenant_row,
            tenant_profile=tenant_profile,
            is_bootstrap_turn=_is_bootstrap_question(question_text) and is_new_session,
            bootstrap_user_locale=(effective_user_ctx or {}).get("locale"),
            browser_locale=(effective_user_ctx or {}).get("browser_locale") or browser_locale,
            chat=chat,
            db=s,
        ),
    )
    _lang_span.end(
        output={
            "detected_language": language_context.detected_language,
            "response_language": language_context.response_language,
            "confidence": language_context.confidence,
            "is_reliable": language_context.is_reliable,
        },
        metadata={"duration_ms": round((perf_counter() - _lang_start) * 1000, 2)},
    )

    trace.update(
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
        user_id=str((effective_user_ctx or {}).get("user_id")) if effective_user_ctx else None,
    )

    if not question_text and not is_new_session:
        raise ValueError("Question is required")

    handler_ctx = await _build_handler_context_async(
        db=db,
        tenant_id=tenant_id,
        tenant_row=tenant_row,
        tenant_profile=tenant_profile,
        chat=chat,
        question=question,
        redacted_question=redacted_question,
        question_text=question_text,
        language_context=language_context,
        api_key=api_key,
        optional_entity_types=optional_entity_types,
        is_new_session=is_new_session,
        trace=trace,
        session_id=session_id,
        user_context=user_context,
        effective_user_ctx=effective_user_ctx,
        bot_public_id=bot_public_id,
        bot_id=bot_id,
        disclosure_config=disclosure_config,
        allow_clarification=chat.clarification_count < MAX_CLARIFICATIONS_PER_SESSION,
        stream_callback=stream_callback,
        explicit_human_request=explicit_human_request,
        turn_started_at=_turn_started_at,
    )

    outcome = await _async_dispatch(handler_ctx, db)
    if outcome is None:
        raise RuntimeError("Pipeline router produced no outcome for chat turn")
    return outcome
