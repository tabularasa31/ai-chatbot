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
from sqlalchemy.orm import selectinload

from backend.chat.decision import (
    MAX_CLARIFICATIONS_PER_SESSION,
)
from backend.chat.handlers import (
    ChatTurnOutcome,
    HandlerContext,
    HandlerRouter,
    default_router,
)
from backend.chat.handlers.rag import RagHandler
from backend.chat.language import (
    ResolvedLanguageContext,
)
from backend.chat.language_context import (
    _is_bootstrap_question,
    _resolve_chat_language_context,
)
from backend.chat.pii import redact
from backend.chat.pipeline import (
    async_run_chat_pipeline,
)
from backend.chat.presets import effective_agent_instructions
from backend.chat.prompts import (
    _user_context_prompt_line,
)
from backend.chat.rotation import latest_chat_query, should_rotate
from backend.chat.steps import answer_cache as answer_cache_steps
from backend.chat.types import (
    QuestionIntentResult,
)
from backend.contact_sessions.service import touch_user_session
from backend.core.db import async_commit_or_rollback, run_sync
from backend.documents.service import async_knowledge_base_updated_at
from backend.escalation.service import (
    classify_question_intent,
    detect_human_request,
    visitor_identity_context,
)
from backend.models import (
    Bot,
    Chat,
    Tenant,
    TenantProfile,
)
from backend.observability import TraceHandle, begin_trace, record_stage_ms
from backend.tenants.cache import (
    get_cached_tenant,
    get_cached_tenant_profile,
    set_cached_tenant,
    set_cached_tenant_profile,
)

_DISCLOSURE_UNSET: dict | None = object()  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_HANDLER_ROUTER: HandlerRouter = default_router()


def _discard_task_result(task: asyncio.Future) -> None:
    """Retrieve an abandoned future's outcome so asyncio does not log it."""
    if not task.cancelled():
        task.exception()


async def _ensure_chat_async(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    bot_id: uuid.UUID | None,
    user_context: dict | None,
    browser_locale: str | None,
) -> tuple[Chat, dict | None, str | None]:
    """Load the session's current conversation, rotating it when stale.

    Loads the latest Chat row for the session (``selectinload`` for messages)
    and creates a new one when none exists — or when the latest one is idle
    past the conversation threshold (see :mod:`backend.chat.rotation`), which
    starts a fresh conversation under the same ``session_id``.
    """
    result = await db.execute(
        latest_chat_query(tenant_id, session_id).options(selectinload(Chat.messages))
    )
    chat = result.scalars().first()

    rotated_from: Chat | None = None
    prior_session_language: str | None = None
    if chat is not None and should_rotate(chat):
        # Serialize concurrent rotation: lock the stale row, then re-read the
        # latest chat. If a parallel request rotated while we waited on the
        # lock, the re-read (READ COMMITTED) sees its freshly committed Chat
        # and we continue there instead of rotating a second time.
        await db.execute(select(Chat.id).where(Chat.id == chat.id).with_for_update())
        _res = await db.execute(
            latest_chat_query(tenant_id, session_id).options(
                selectinload(Chat.messages)
            )
        )
        latest = _res.scalars().first()
        if latest is not None and latest.id == chat.id and should_rotate(latest):
            rotated_from = latest
            chat = None
        else:
            chat = latest

    effective_user_ctx: dict | None = None
    if chat and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    elif rotated_from is not None and rotated_from.user_context:
        # The visitor identity survives rotation even though the conversation
        # state does not.
        effective_user_ctx = visitor_identity_context(rotated_from.user_context)
    elif user_context:
        effective_user_ctx = dict(user_context)
    if rotated_from is not None:
        # Carry the language the visitor last spoke in across the rotation
        # boundary. The fresh Chat row has no last_response_language of its own,
        # so this is the only bridge that lets a bootstrap re-greeting answer in
        # the returning visitor's established language instead of English. Kept
        # as a standalone statement so it does not sever the user_context
        # if/elif chain above.
        prior_session_language = rotated_from.last_response_language

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
        await async_commit_or_rollback(db)
        if rotated_from is not None:
            # No chat_session_ended emission here: the old chat is past the
            # same idle threshold the sweeper uses, so the sweeper reports it
            # at-most-once via session_ended_event_at within one pass.
            logger.info(
                "conversation_rotated",
                extra={
                    "session_id": str(session_id),
                    "old_chat_id": str(rotated_from.id),
                    "new_chat_id": str(chat.id),
                },
            )
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
            await async_commit_or_rollback(db)
            # Re-query with selectinload so chat.messages is eagerly loaded.
            _res = await db.execute(
                select(Chat).options(selectinload(Chat.messages)).where(Chat.id == chat.id)
            )
            chat = _res.scalar_one()

    if effective_user_ctx is None and chat.user_context:
        effective_user_ctx = dict(chat.user_context)
    return chat, effective_user_ctx, prior_session_language


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
    status_callback: Callable[[str], None] | None = None,
    explicit_human_request: bool,
    human_request_explicit: bool = True,
    question_intent: QuestionIntentResult | None = None,
    message_has_request_content: bool = False,
    turn_started_at: float,
) -> HandlerContext:
    """Assemble the per-turn ``HandlerContext`` for handler dispatch.

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
        is_new_session=is_new_session,
        trace=trace,
        session_id=session_id,
        user_context=user_context,
        effective_user_ctx=effective_user_ctx,
        bot_public_id=bot_public_id,
        bot_id=bot_id,
        bot=resolved_bot,
        bot_agent_instructions=(
            effective_agent_instructions(
                custom_instructions=resolved_bot.custom_instructions,
                preset=resolved_bot.preset,
            )[0]
            if resolved_bot
            else None
        ),
        disclosure_config=disclosure_cfg,
        allow_clarification=allow_clarification,
        user_context_line=_user_context_prompt_line(effective_user_ctx),
        stream_callback=stream_callback,
        status_callback=status_callback,
        explicit_human_request=explicit_human_request,
        human_request_explicit=human_request_explicit,
        question_intent=question_intent or QuestionIntentResult(),
        message_has_request_content=message_has_request_content,
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
    ctx.async_db = db
    for handler in _HANDLER_ROUTER.handlers:
        if not handler.can_handle(ctx):
            continue
        if isinstance(handler, RagHandler) and "_pipeline_result" not in ctx.extras:
            # Egress boundary: the pipeline embeds this text, rewrites it,
            # and puts it in the generation prompt — every one of those is an
            # OpenAI call, so it gets the redacted question, never the raw
            # one. Storage keeps the original via ``ctx.question``.
            pipeline_result = await async_run_chat_pipeline(
                ctx.tenant_id,
                ctx.redacted_question,
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
                status_callback=ctx.status_callback,
                agent_instructions=ctx.bot_agent_instructions,
                allow_clarification=ctx.allow_clarification,
                guard_profile=ctx.tenant_profile,
                question_intent=ctx.question_intent,
                answer_cache_scope=ctx.extras.get("_answer_cache_scope"),
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
    status_callback: Callable[[str], None] | None = None,
) -> ChatTurnOutcome:
    """Run one chat turn through the async pipeline.

    Replaces the ``_GUARD_POOL`` (ThreadPoolExecutor) in the RAG path with
    ``asyncio.create_task`` so guard checks, embedding, and retrieval run
    concurrently on the event loop without blocking OS threads.

    Non-RAG handlers (Greeting, Escalation) run their DB work on the
    ``AsyncSession`` sync facade via ``run_sync`` (greenlet); their LLM calls
    are async and awaited on the event loop through ``await_only``.
    """
    _turn_started_at = perf_counter()

    # Tenant is near-static; a per-process TTL cache collapses this DB hop into
    # a memory read on the hot path (item 1 of the chat-latency plan). A miss
    # loads from DB and populates the cache; tenant-update routes invalidate it.
    tenant_row = get_cached_tenant(tenant_id)
    if tenant_row is None:
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant_row = tenant_result.scalar_one_or_none()
        if tenant_row is not None:
            set_cached_tenant(tenant_row)
    # Release the pooled connection before the classifier LLM calls; the chat
    # setup below re-acquires one.
    await db.close()

    redacted_question = redact(question).redacted_text

    # The human-request and intent classifiers overlap the chat setup and the
    # answer-cache lookup below; an exact cache hit abandons them. A bootstrap
    # turn (widget open) carries an empty question and nothing to classify.
    _hrc_start = perf_counter()
    classifier_task: asyncio.Future[tuple[Any, Any]] | None = None
    if redacted_question.strip():
        classifier_task = asyncio.gather(
            detect_human_request(redacted_question, api_key, tenant_id),
            classify_question_intent(redacted_question, api_key, tenant_id),
        )
        classifier_task.add_done_callback(_discard_task_result)

    trace = begin_trace(
        name="rag-query",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
        input=redacted_question or None,
        metadata={"tenant_id": str(tenant_id), "session_id": str(session_id)},
        tags=[f"tenant:{tenant_id}"],
    )

    _setup_start = perf_counter()
    _setup_span = trace.span(
        name="chat_setup",
        input={"session_id": str(session_id), "has_bot_id": bot_id is not None},
    )
    chat, effective_user_ctx, prior_session_language = await _ensure_chat_async(
        db, tenant_id, session_id, bot_id, user_context, browser_locale
    )
    # Bind tenant_row to the now-active session. It is detached either by the
    # db.close() above (fresh DB load) or because it is a session-less cache
    # clone; merge(load=False) yields a session-bound copy without SQL in both
    # cases. Today only loaded scalar columns are read downstream, but merging
    # protects future lazy-loaded relationships from DetachedInstanceError.
    if tenant_row is not None:
        tenant_row = await db.merge(tenant_row, load=False)
    tenant_profile: TenantProfile | None = None
    if tenant_row is not None:
        # Same near-static TTL cache as Tenant (item 1). Absence of a profile
        # row is not cached, so a profile created later is still picked up.
        cached_profile = get_cached_tenant_profile(tenant_id)
        if cached_profile is not None:
            tenant_profile = await db.merge(cached_profile, load=False)
        else:
            tenant_profile = await db.get(TenantProfile, tenant_id)
            if tenant_profile is not None:
                set_cached_tenant_profile(tenant_profile)
    knowledge_base_updated_at = await async_knowledge_base_updated_at(tenant_id, db)
    _setup_ms = round((perf_counter() - _setup_start) * 1000, 2)
    _setup_span.end(
        output={"is_new_session": not chat.messages, "chat_id": str(chat.id)},
        metadata={"duration_ms": _setup_ms},
    )
    record_stage_ms(trace, "chat_setup_ms", _setup_ms)

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
            prior_session_language=prior_session_language,
            chat=chat,
            db=s,
        ),
    )
    _lang_ms = round((perf_counter() - _lang_start) * 1000, 2)
    _lang_span.end(
        output={
            "detected_language": language_context.detected_language,
            "detected_language_resolution_reason": language_context.detected_language_resolution_reason,
            "response_language": language_context.response_language,
            "confidence": language_context.confidence,
            "is_reliable": language_context.is_reliable,
        },
        metadata={"duration_ms": _lang_ms},
    )
    record_stage_ms(trace, "language_detect_ms", _lang_ms)

    trace.update(
        metadata={
            "tenant_id": str(tenant_id),
            "session_id": str(session_id),
            "chat_id": str(chat.id),
            "browser_locale": browser_locale,
            "question": redacted_question,
            "has_user_context": bool(effective_user_ctx),
            "knowledge_base_updated_at": (
                knowledge_base_updated_at.isoformat() + "Z"
                if knowledge_base_updated_at is not None
                else None
            ),
            "detected_language": language_context.detected_language,
            "detected_language_resolution_reason": language_context.detected_language_resolution_reason,
            # Language-detection confidence, renamed from the bare "confidence"
            # key: at trace level it collided with retrieval "best_confidence_score"
            # (written by the RAG handler) and made per-trace confidence analytics
            # ambiguous. Omitted entirely — not written as 0.0 — when detection did
            # not run this turn (locked follow-ups, bootstrap), so those turns stop
            # registering as false-negative zero-confidence detections.
            **(
                {
                    "language_confidence": language_context.confidence,
                    "language_is_reliable": language_context.is_reliable,
                }
                if language_context.detection_confidence_measured
                else {}
            ),
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
        status_callback=status_callback,
        explicit_human_request=False,
        turn_started_at=_turn_started_at,
    )

    # Answer cache, exact level: an identical question already answered for
    # this bot skips the classifiers, the guards and the whole pipeline. A
    # cached question was a real request, so the greeting handler must not
    # claim the turn.
    answer_cache_scope = await answer_cache_steps.resolve_scope_for_turn(handler_ctx, db)
    handler_ctx.extras["_answer_cache_scope"] = answer_cache_scope
    cached_result = (
        await answer_cache_steps.exact_lookup(
            answer_cache_scope, language_context=language_context, trace=trace
        )
        if answer_cache_scope is not None
        else None
    )
    if cached_result is not None:
        handler_ctx.extras["_pipeline_result"] = cached_result
        handler_ctx.message_has_request_content = True
        if classifier_task is not None:
            classifier_task.cancel()
    elif classifier_task is not None:
        human_request_result, question_intent = await classifier_task
        handler_ctx.explicit_human_request = human_request_result.human_request
        handler_ctx.human_request_explicit = human_request_result.human_request_explicit
        handler_ctx.question_intent = question_intent
        handler_ctx.message_has_request_content = human_request_result.message_has_request_content
        if human_request_result.human_request:
            trace.promote(
                metadata={"sampling_promoted": True, "promotion_reason": "explicit_human_request"}
            )
    record_stage_ms(
        trace, "human_request_classifier_ms", round((perf_counter() - _hrc_start) * 1000, 2)
    )
    # Sticky marker: once any turn carries a concrete problem/question, later
    # bare handoff requests can escalate with that context instead of being
    # re-asked. A bare greeting (message_has_request_content=False) never sets
    # it. Persisted with the turn via the chat row already in this session.
    if handler_ctx.message_has_request_content and not chat.has_substantive_content:
        chat.has_substantive_content = True

    try:
        outcome = await _async_dispatch(handler_ctx, db)
        if outcome is None:
            raise RuntimeError("Pipeline router produced no outcome for chat turn")
        return outcome
    finally:
        # Flush the per-stage wall-clock dict onto the trace so a single
        # Langfuse view surfaces every stage's duration without span-walking.
        # See TraceHandle.record_stage_ms for the aggregation contract. The
        # ``getattr`` guard tolerates ad-hoc test traces that don't subclass
        # TraceHandle (matching the helper used at the recording call sites).
        stage_durations = getattr(trace, "stage_durations_ms", None) or {}
        if stage_durations:
            trace.update(metadata={"stage_durations_ms": stage_durations})
