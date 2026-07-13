"""Retrieval pipeline steps.

* :func:`async_retrieve_context` — embed the query and run hybrid search
  (pgvector + BM25 + RRF via ``backend.search.service``).
* :func:`run_retrieval` — consume the speculative retrieval task started in
  the pre-retrieval step (or run retrieval fresh) under a ``retrieval``
  Langfuse span.
* :func:`zero_hits_fast_path` — strict zero-hits routing (soft reply /
  escalation / off-topic / social) before the expensive answer LLM.
* :func:`low_retrieval_guard` — reject when every vector similarity is below
  the relevance threshold and the reranker didn't rescue the turn.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from time import perf_counter
from typing import Any, Literal

from openai import APIConnectionError, APITimeoutError, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat.events import (
    _emit_no_rag_hits_event,
    _emit_speculative_retrieval_event,
)
from backend.chat.steps.refusal import (
    build_pre_confirm_escalation_result,
    build_reject_result,
)
from backend.chat.types import (
    ChatPipelineResult,
    PipelineRun,
    RetrievalContext,
    _empty_retrieval_context,
)
from backend.core.config import settings
from backend.guards.relevance_checker import (
    CATEGORY_SOCIAL,
    CATEGORY_SOCIAL_QUESTION,
    CATEGORY_SUPPORT_COMPLAINT,
)
from backend.observability import TraceHandle, record_stage_ms

logger = logging.getLogger(__name__)


async def async_retrieve_context(
    tenant_id: uuid.UUID,
    question: str,
    db: AsyncSession,
    api_key: str,
    top_k: int = 5,
    trace: TraceHandle | None = None,
    precomputed_query_variants: list[str] | None = None,
    precomputed_variant_vectors: list[list[float]] | None = None,
    precomputed_embedding_api_request_count: int | None = None,
    rewritten_variant: str | None = None,
) -> RetrievalContext:
    """Async retrieval: embed the query and run hybrid search.

    Uses ``search_similar_chunks_detailed_async`` so the event loop is not
    blocked during embedding and pgvector queries.
    """
    from backend.search.service import search_similar_chunks_detailed_async

    _retrieval_start = perf_counter()
    try:
        bundle = await search_similar_chunks_detailed_async(
            tenant_id=tenant_id,
            query=question,
            top_k=top_k,
            db=db,
            api_key=api_key,
            trace=trace,
            precomputed_query_variants=precomputed_query_variants,
            precomputed_variant_vectors=precomputed_variant_vectors,
            precomputed_embedding_api_request_count=precomputed_embedding_api_request_count,
            precomputed_rewritten_variant=rewritten_variant,
        )
    except (APITimeoutError, APIConnectionError, RateLimitError):
        retrieval_duration_ms = round((perf_counter() - _retrieval_start) * 1000, 2)
        logger.warning("async_retrieve_context_embedding_failed", exc_info=True)
        return RetrievalContext(
            chunk_texts=[],
            document_ids=[],
            scores=[],
            mode="none",
            best_rank_score=None,
            best_confidence_score=None,
            confidence_source="none",
            retrieval_duration_ms=retrieval_duration_ms,
        )
    results = bundle.results

    if not results:
        return RetrievalContext(
            chunk_texts=[],
            document_ids=[],
            scores=[],
            mode="none",
            best_rank_score=None,
            best_confidence_score=None,
            confidence_source="none",
            reliability=bundle.reliability,
            variant_mode=bundle.variant_mode,
            query_variant_count=bundle.query_variant_count,
            extra_embedded_queries=bundle.extra_embedded_queries,
            extra_embedding_api_requests=bundle.extra_embedding_api_requests,
            extra_vector_search_calls=bundle.extra_vector_search_calls,
            bm25_expansion_mode=bundle.bm25_expansion_mode,
            bm25_query_variant_count=bundle.bm25_query_variant_count,
            bm25_variant_eval_count=bundle.bm25_variant_eval_count,
            extra_bm25_variant_evals=bundle.extra_bm25_variant_evals,
            bm25_merged_hit_count_before_cap=bundle.bm25_merged_hit_count_before_cap,
            bm25_merged_hit_count_after_cap=bundle.bm25_merged_hit_count_after_cap,
            retrieval_duration_ms=bundle.retrieval_duration_ms,
            vector_similarities=None,
        )

    best_rank_score = results[0][1]
    mode: Literal["vector", "hybrid", "none"] = "hybrid" if bundle.has_lexical_signal else "vector"
    best_confidence_score = bundle.best_vector_similarity
    confidence_source: Literal["vector_similarity", "none"] = "vector_similarity"
    chunk_texts = [r[0].chunk_text or "" for r in results]
    document_ids = [r[0].document_id for r in results]
    scores = [r[1] for r in results]

    return RetrievalContext(
        chunk_texts=chunk_texts,
        document_ids=document_ids,
        scores=scores,
        mode=mode,
        best_rank_score=best_rank_score,
        best_confidence_score=best_confidence_score,
        confidence_source=confidence_source,
        reliability=bundle.reliability,
        variant_mode=bundle.variant_mode,
        query_variant_count=bundle.query_variant_count,
        extra_embedded_queries=bundle.extra_embedded_queries,
        extra_embedding_api_requests=bundle.extra_embedding_api_requests,
        extra_vector_search_calls=bundle.extra_vector_search_calls,
        bm25_expansion_mode=bundle.bm25_expansion_mode,
        bm25_query_variant_count=bundle.bm25_query_variant_count,
        bm25_variant_eval_count=bundle.bm25_variant_eval_count,
        extra_bm25_variant_evals=bundle.extra_bm25_variant_evals,
        bm25_merged_hit_count_before_cap=bundle.bm25_merged_hit_count_before_cap,
        bm25_merged_hit_count_after_cap=bundle.bm25_merged_hit_count_after_cap,
        retrieval_duration_ms=bundle.retrieval_duration_ms,
        vector_similarities=bundle.vector_similarities,
    )


async def _execute_retrieval(run: PipelineRun, session: AsyncSession) -> RetrievalContext:
    # Looked up via the service module so test monkeypatches on
    # ``backend.chat.service.async_retrieve_context`` intercept the call.
    from backend.chat import service as _svc

    return await _svc.async_retrieve_context(
        run.tenant_id,
        run.question,
        session,
        run.api_key,
        top_k=5,
        trace=run.trace,
        **run.state.retrieve_kwargs,
    )


async def _speculative_retrieval(run: PipelineRun) -> RetrievalContext:
    """Run retrieval on a dedicated session for the speculative path.

    A separate session (never the pipeline's ``db``, which is closed while
    the relevance guard runs) avoids concurrent-use corruption, and the
    ``async with`` releases the connection on cancellation — so a discarded
    speculative turn leaves no dangling connection. Retrieval issues only
    SELECTs and never commits, so nothing is persisted either.
    """
    import backend.core.db as core_db

    async with core_db.AsyncSessionLocal() as spec_db:
        return await _execute_retrieval(run, spec_db)


async def cancel_speculative_retrieval(run: PipelineRun) -> None:
    """Cancel and drain the speculative task; release its session."""
    state = run.state
    task = state.spec_retrieval_task
    if task is None:
        return
    state.spec_retrieval_task = None
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    _emit_speculative_retrieval_event(
        outcome="wasted_reject",
        duration_ms=None,
        tenant_public_id=run.tenant_public_id,
        bot_public_id=run.bot_public_id,
        chat_id=run.chat_id,
        cross_lingual=state.cross_lingual_triggered,
        variant_mode="multi" if state.variant_vectors and len(state.variant_vectors) > 1 else "single",
    )


async def run_retrieval(run: PipelineRun) -> None:
    """Consume the speculative retrieval task, or run retrieval fresh.

    Retrieval was started speculatively in the pre-retrieval step,
    concurrently with the relevance guard. Here we await that result; if the
    task is missing or failed we fall back to a fresh retrieval on ``run.db``
    so a speculative glitch never degrades the answer. Sets
    ``run.state.retrieval`` on every path.
    """
    state = run.state
    span = None
    if run.trace is not None:
        span = run.trace.span(
            name="retrieval",
            input={
                "query_variant_count": len(state.query_variants),
                "speculative": state.spec_retrieval_task is not None,
            },
        )
    outcome = "fresh"
    try:
        if not state.variant_vectors:
            outcome = "skipped_no_vectors"
            state.retrieval = _empty_retrieval_context()
        elif state.spec_retrieval_task is not None:
            task = state.spec_retrieval_task
            state.spec_retrieval_task = None
            try:
                state.retrieval = await task
                outcome = "speculative"
                _emit_speculative_retrieval_event(
                    outcome="used",
                    duration_ms=state.retrieval.retrieval_duration_ms,
                    tenant_public_id=run.tenant_public_id,
                    bot_public_id=run.bot_public_id,
                    chat_id=run.chat_id,
                    cross_lingual=state.cross_lingual_triggered,
                    variant_mode=state.retrieval.variant_mode,
                    retrieval_mode=state.retrieval.mode,
                )
            except asyncio.CancelledError:
                # Request cancelled (e.g. client disconnect) — propagate, never
                # treat it as a speculative failure that warrants a fallback.
                raise
            except Exception:
                logger.warning("speculative_retrieval_failed_fallback", exc_info=True)
                state.retrieval = await _execute_retrieval(run, run.db)
                outcome = "fallback"
                _emit_speculative_retrieval_event(
                    outcome="fallback",
                    duration_ms=state.retrieval.retrieval_duration_ms,
                    tenant_public_id=run.tenant_public_id,
                    bot_public_id=run.bot_public_id,
                    chat_id=run.chat_id,
                    cross_lingual=state.cross_lingual_triggered,
                    variant_mode=state.retrieval.variant_mode,
                    retrieval_mode=state.retrieval.mode,
                )
        else:
            state.retrieval = await _execute_retrieval(run, run.db)
    except BaseException as exc:  # incl. CancelledError — never leave the span dangling
        if span is not None:
            span.end(level="ERROR", status_message=str(exc) or type(exc).__name__)
        raise

    retrieval = state.retrieval
    if span is not None:
        span.end(
            output={
                "outcome": outcome,
                "mode": retrieval.mode,
                "chunk_count": len(retrieval.chunk_texts),
                "best_confidence_score": retrieval.best_confidence_score,
                "best_rank_score": retrieval.best_rank_score,
                "duration_ms": retrieval.retrieval_duration_ms,
            }
        )
    record_stage_ms(
        run.trace,
        "retrieval_ms",
        retrieval.retrieval_duration_ms or 0.0,
    )
    if run.tenant_public_id is not None or run.bot_public_id is not None:
        from backend.chat.events import _emit_ai_span_event
        _retrieval_trace_id = (
            getattr(run.trace, "posthog_trace_id", None) if run.trace is not None else None
        )
        _emit_ai_span_event(
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            span_name="retrieval",
            latency_s=(retrieval.retrieval_duration_ms or 0.0) / 1000.0,
            trace_id=_retrieval_trace_id,
            span_id=uuid.uuid4().hex if _retrieval_trace_id else None,
            parent_id=_retrieval_trace_id,
            extra_properties={
                "chunk_count": len(retrieval.chunk_texts),
                "mode": retrieval.mode,
                "best_confidence_score": retrieval.best_confidence_score,
            },
        )


def _fast_path_extras(run: PipelineRun, retrieval_ms: int) -> dict[str, Any]:
    """Telemetry fields mirrored from the normal-success branch so PostHog
    ``chat.turn`` events on the zero-hits fast path retain the same
    cross-script / FAQ signal as the slow path."""
    state = run.state
    return {
        "query_script": state.query_script or None,
        "kb_scripts": list(state.kb_scripts) if state.kb_scripts else None,
        "cross_lingual_triggered": state.cross_lingual_triggered,
        "cross_lingual_variants_count": state.cross_lingual_variants_added,
        "query_kb_language_match": state.query_kb_language_match,
        # ``retrieval_used_cross_lingual_variant`` requires the variant
        # to have produced non-empty chunks; the fast-path fires
        # precisely when chunk_texts is empty, so this is always False.
        "retrieval_used_cross_lingual_variant": False,
        "retrieval_ms": retrieval_ms,
    }


async def zero_hits_fast_path(run: PipelineRun) -> ChatPipelineResult | None:
    """Strict zero-hits routing before the expensive answer LLM.

    The bot is language-agnostic, so the soft-reply / off-topic / escalation
    decision uses canonical English templates routed through the existing
    localization layer — no hardcoded per-language strings.

    On the first zero-RAG-hits turn in a session we short-circuit before
    the expensive answer LLM and return a "couldn't find an answer in the
    knowledge base, please rephrase" prompt. On a *second consecutive*
    zero-hits turn we ask the LLM relevance model (force_llm_check=True
    so short queries still get a real verdict). If the model says the
    question is in-domain we escalate via the existing pre-confirm gate;
    otherwise we fall back to the standard NOT_RELEVANT reject.

    The flag ``chat.last_reply_was_rephrase_prompt`` is authoritatively
    set/cleared by the persistence layer (``set_rephrase_flag`` param on
    ``_persist_turn_with_response_language``), so handlers that bypass
    the RAG path (Greeting, Escalation) also reset it.

    Fast path only applies when there is truly nothing for the LLM to
    answer from: empty retrieval AND no FAQ context items AND no Quick
    Answer items. If any auxiliary knowledge source matched, fall through
    so the answer LLM can still produce a real reply.
    """
    from backend.chat import service as _svc
    from backend.models import EscalationTrigger

    state = run.state
    retrieval = state.retrieval
    assert retrieval is not None  # set by run_retrieval on every branch
    if retrieval.chunk_texts or state.faq_context_items or state.quick_answer_items:
        return None

    _retrieval_ms = int(retrieval.retrieval_duration_ms)
    extras = _fast_path_extras(run, _retrieval_ms)

    # Session-window guard: the flag is a persistent DB column, so we
    # treat it as stale once the inactivity sweeper has reported the
    # session ended (``session_ended_event_at`` set). Without this
    # check a user resuming the chat days later — whose previous turn
    # happened to be the rephrase prompt — would skip straight to
    # escalation on what is effectively their first question of a new
    # session.
    is_consecutive = bool(
        run.chat is not None
        and run.chat.last_reply_was_rephrase_prompt
        and run.chat.session_ended_event_at is None
    )

    span = None
    if run.trace is not None:
        span = run.trace.span(
            name="zero-hits-check",
            input={
                "is_consecutive": is_consecutive,
                "guard_bypassed_short_query": state.guard_bypassed_short_query,
            },
        )

    def _end_span(outcome: str, relevance_reason: str | None = None) -> None:
        if span is not None:
            span.end(output={"outcome": outcome, "relevance_reason": relevance_reason})

    async def _social_no_hits_result(category: str) -> ChatPipelineResult:
        """Build the polite social reply for a zero-hits social turn.

        Shared by the first-turn short-query path and the consecutive
        force-check path so both emit the identical reply shape and
        ``guard_reject`` telemetry.
        """
        is_social_question = category == CATEGORY_SOCIAL_QUESTION
        # Reply is rendered BEFORE the telemetry event so a failed render
        # never records a no_rag_hits outcome for a reply that was never sent.
        result = await build_reject_result(
            run,
            reject_reason="social_question" if is_social_question else "social",
            retrieval=retrieval,
            tokens_as_output=True,
            extras=extras,
        )
        _emit_no_rag_hits_event(
            outcome="social_reply",
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            chat_id=run.chat_id,
            relevance_reason=category,
        )
        _end_span("social_reply", category)
        return result

    async def _route() -> ChatPipelineResult:
        if not is_consecutive:
            # A short query bypassed the pre-retrieval guard (≤4 words), so
            # it never received a category verdict. Now that retrieval is
            # also empty, classify it once: a social turn or a bot-meta
            # question ("do you speak English?") gets its friendly reply on
            # the first turn instead of the generic rephrase prompt. Other
            # verdicts (in-domain / off-topic / complaint) keep the existing
            # turn-1 rephrase and only escalate on the next consecutive miss.
            if state.guard_bypassed_short_query:
                _short_verdict = await _svc.async_check_relevance_with_profile(
                    tenant_id=run.tenant_id,
                    user_question=run.question,
                    profile=state.profile,
                    api_key=run.api_key,
                    trace=run.trace,
                    force_llm_check=True,
                    dialog_context=state.guard_dialog_context,
                    chat_id=str(run.chat_id) if run.chat_id is not None else None,
                )
                _short_reason = _short_verdict.reason.value
                if _short_reason in (CATEGORY_SOCIAL, CATEGORY_SOCIAL_QUESTION):
                    return await _social_no_hits_result(_short_reason)

            result = await build_reject_result(
                run,
                reject_reason="rephrase",
                retrieval=retrieval,
                include_question=True,
                tokens_as_output=True,
                extras=extras,
            )
            _emit_no_rag_hits_event(
                outcome="soft_reply",
                tenant_public_id=run.tenant_public_id,
                bot_public_id=run.bot_public_id,
                chat_id=run.chat_id,
            )
            _end_span("soft_reply")
            return result

        _rel_verdict = await _svc.async_check_relevance_with_profile(
            tenant_id=run.tenant_id,
            user_question=run.question,
            profile=state.profile,
            api_key=run.api_key,
            trace=run.trace,
            force_llm_check=True,
            dialog_context=state.guard_dialog_context,
            chat_id=str(run.chat_id) if run.chat_id is not None else None,
        )
        relevant = not _rel_verdict.blocked
        relevance_reason = _rel_verdict.reason.value

        # Category routing mirrors the main guard site: a support
        # complaint gets the escalation offer, a social turn a polite
        # acknowledgement — neither should fall into the off-topic reject.
        if relevance_reason == CATEGORY_SUPPORT_COMPLAINT:
            from backend.escalation.openai_escalation import pre_confirm_fallback_result

            complaint_fallback = pre_confirm_fallback_result("support_complaint")
            _emit_no_rag_hits_event(
                outcome="escalation",
                tenant_public_id=run.tenant_public_id,
                bot_public_id=run.bot_public_id,
                chat_id=run.chat_id,
                relevance_reason=relevance_reason,
            )
            _end_span("escalation", relevance_reason)
            return build_pre_confirm_escalation_result(
                run,
                message_to_user=complaint_fallback.message_to_user,
                tokens_used=complaint_fallback.tokens_used,
                trigger=EscalationTrigger.user_complaint,
                retrieval=retrieval,
                extras=extras,
            )

        if relevance_reason in (CATEGORY_SOCIAL, CATEGORY_SOCIAL_QUESTION):
            return await _social_no_hits_result(relevance_reason)
        # Only escalate when the relevance model actually rendered a
        # positive verdict. Fail-open reasons (``no_profile`` for tenants
        # without an onboarded profile; ``timeout`` / ``error`` during
        # OpenAI degradation) all surface ``relevant=True`` without any
        # real judgment — treating them as "in-domain" would arm a
        # support handoff on what may well be an off-topic question, with
        # no support pipeline configured. Route those to the off-topic
        # reply path instead.
        _is_trusted_relevant_verdict = relevant and relevance_reason not in (
            "no_profile",
            "timeout",
            "error",
        )
        if _is_trusted_relevant_verdict:
            # Localized fallback that keeps the reply non-empty if the
            # handler's ``render_pre_confirm_text`` call fails (e.g.
            # OpenAI timeout): the catch in _handle_sync flips
            # ``escalation_pre_confirm_pending`` back to False and reuses
            # ``result.final_answer`` as the user-facing reply. Using the
            # rephrase soft-reply as fallback keeps the bot polite and
            # in-language instead of returning an empty string. The
            # handler also re-arms the rephrase tracker in that catch so
            # the next turn doesn't loop on the same prompt.
            from backend.guards.reject_response import (
                RejectReason,
                build_reject_response_result,
            )

            fallback = await build_reject_response_result(
                reason=RejectReason.REPHRASE_REQUEST,
                profile=state.profile,
                response_language=run.language_context.response_language,
                api_key=run.api_key,
                question=run.question,
            )
            _emit_no_rag_hits_event(
                outcome="escalation",
                tenant_public_id=run.tenant_public_id,
                bot_public_id=run.bot_public_id,
                chat_id=run.chat_id,
                relevance_reason=relevance_reason,
            )
            _end_span("escalation", relevance_reason)
            return build_pre_confirm_escalation_result(
                run,
                message_to_user=fallback.text,
                tokens_used=fallback.tokens_used,
                trigger=EscalationTrigger.no_documents,
                retrieval=retrieval,
                tokens_as_output=True,
                extras=extras,
            )

        result = await build_reject_result(
            run,
            reject_reason="not_relevant",
            retrieval=retrieval,
            include_question=True,
            tokens_as_output=True,
            extras=extras,
        )
        _emit_no_rag_hits_event(
            outcome="offtopic_reply",
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            chat_id=run.chat_id,
            relevance_reason=relevance_reason,
        )
        _end_span("offtopic_reply", relevance_reason)
        return result

    try:
        return await _route()
    except BaseException as exc:  # incl. CancelledError — never leave the span dangling
        if span is not None:
            span.end(level="ERROR", status_message=str(exc) or type(exc).__name__)
        raise


async def low_retrieval_guard(run: PipelineRun) -> ChatPipelineResult | None:
    """Reject when every vector similarity is below the relevance threshold.

    ``reranker_rescued`` (computed here, consumed by the generation step's
    low-context prompt flag) bypasses the reject when the rank score is high
    enough that the reranker vouches for the top hit despite weak raw
    similarity.
    """
    state = run.state
    retrieval = state.retrieval
    assert retrieval is not None  # set by run_retrieval on every branch
    threshold = settings.relevance_retrieval_threshold

    state.reranker_rescued = (
        retrieval.best_rank_score is not None
        and retrieval.best_rank_score >= settings.reranker_bypass_threshold
    )

    if (
        not state.reranker_rescued
        and retrieval.vector_similarities is not None
        and retrieval.vector_similarities
        and all(sim is not None for sim in retrieval.vector_similarities)
        and all(float(sim) < threshold for sim in retrieval.vector_similarities if sim is not None)
    ):
        return await build_reject_result(
            run,
            reject_reason="low_retrieval",
            retrieval=retrieval,
            include_question=True,
        )
    return None
