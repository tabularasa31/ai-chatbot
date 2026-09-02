"""Answer-cache pipeline steps.

* :func:`resolve_scope` — cache scope for the turn (bot, language, KB
  fingerprint); ``None`` leaves the cache out of the turn entirely.
* :func:`exact_lookup` — level 1, runs before the guards: an identical
  question already answered for this scope is served without any OpenAI call.
* :func:`semantic_lookup` — level 2, runs once the question embedding exists
  and the FAQ matcher has declined the turn: the nearest cached question of
  the scope above the similarity threshold is served without retrieval or
  generation.
* :func:`build_store_candidate` — decides after generation whether the fresh
  answer is self-contained and confident enough to be stored; the RAG handler
  performs the write once the turn is persisted.

Storage and key semantics live in ``backend/chat/answer_cache.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from time import perf_counter

from backend.chat import answer_cache
from backend.chat.answer_cache import (
    AnswerCacheCandidate,
    AnswerCacheHit,
    AnswerCacheLevel,
    CachedAnswer,
)
from backend.chat.types import ChatPipelineResult, PipelineRun, RetrievalContext
from backend.models.base import _utcnow
from backend.observability import record_stage_ms
from backend.search.service import default_retrieval_reliability

logger = logging.getLogger(__name__)


async def resolve_scope(run: PipelineRun) -> None:
    if not run.answer_cache_eligible or not answer_cache.is_enabled():
        return
    bot_id: uuid.UUID | None = None
    if run.retry_bot_id:
        try:
            bot_id = uuid.UUID(run.retry_bot_id)
        except ValueError:
            bot_id = None
    run.state.answer_cache_scope = await answer_cache.resolve_scope(
        tenant_id=run.tenant_id,
        bot_id=bot_id,
        response_language=run.language_context.response_language,
        question=run.question,
        agent_instructions=run.agent_instructions,
        disclosure_config=run.disclosure_config,
        db=run.db,
    )


def _end_lookup_span(
    run: PipelineRun,
    *,
    level: AnswerCacheLevel,
    started_at: float,
    hit: AnswerCacheHit | None,
) -> None:
    duration_ms = round((perf_counter() - started_at) * 1000, 2)
    if run.trace is None:
        return
    span = run.trace.span(name="answer-cache", input={"level": level})
    span.end(
        output={
            "hit": hit is not None,
            "similarity": hit.similarity if hit is not None else None,
            "saved_ms": hit.saved_ms if hit is not None else None,
        },
        metadata={"duration_ms": duration_ms},
    )
    record_stage_ms(run.trace, "answer_cache_ms", duration_ms)


def _hit_result(run: PipelineRun, hit: AnswerCacheHit) -> ChatPipelineResult:
    cached = hit.answer
    reliability = replace(
        default_retrieval_reliability(),
        base_score=cached.reliability_score,
        score=cached.reliability_score,
    )
    retrieval = RetrievalContext(
        chunk_texts=list(cached.chunk_texts),
        document_ids=[uuid.UUID(d) for d in cached.document_ids],
        scores=list(cached.scores),
        mode=cached.retrieval_mode,
        best_rank_score=cached.best_rank_score,
        best_confidence_score=cached.best_confidence_score,
        confidence_source=cached.confidence_source,
        reliability=reliability,
    )
    state = run.state
    return ChatPipelineResult(
        raw_answer=cached.answer,
        final_answer=cached.answer,
        tokens_used=0,
        strategy=cached.strategy,
        reject_reason=None,
        is_reject=False,
        is_faq_direct=False,
        retrieval=retrieval,
        escalation_recommended=False,
        escalation_trigger=None,
        faq_match=state.faq_match,
        language_context=run.language_context,
        query_script=state.query_script or None,
        kb_scripts=list(state.kb_scripts) if state.kb_scripts else None,
        cross_lingual_triggered=state.cross_lingual_triggered,
        cross_lingual_variants_count=state.cross_lingual_variants_added,
        query_kb_language_match=state.query_kb_language_match if state.kb_scripts else None,
        answer_cache=hit,
    )


async def exact_lookup(run: PipelineRun) -> ChatPipelineResult | None:
    scope = run.state.answer_cache_scope
    if scope is None:
        return None
    started_at = perf_counter()
    cached = await answer_cache.exact_get(scope)
    hit = AnswerCacheHit(level="exact", similarity=1.0, answer=cached) if cached else None
    _end_lookup_span(run, level="exact", started_at=started_at, hit=hit)
    if hit is None:
        return None
    return _hit_result(run, hit)


async def semantic_lookup(run: PipelineRun) -> ChatPipelineResult | None:
    scope = run.state.answer_cache_scope
    state = run.state
    if scope is None or not state.variant_vectors:
        return None
    started_at = perf_counter()
    match = await answer_cache.semantic_get(scope, state.variant_vectors[0], run.db)
    hit = (
        AnswerCacheHit(level="semantic", similarity=match[1], answer=match[0])
        if match is not None
        else None
    )
    _end_lookup_span(run, level="semantic", started_at=started_at, hit=hit)
    if hit is None:
        return None
    # The relevance guard was launched alongside the embedding; a cached
    # answer already passed it when it was generated, so the verdict is not
    # awaited (same as the faq_direct short-circuit).
    if state.rel_task is not None and not state.rel_task.done():
        state.rel_task.cancel()
        await asyncio.gather(state.rel_task, return_exceptions=True)
    await answer_cache.promote_exact(scope, hit.answer)
    return _hit_result(run, hit)


def build_store_candidate(
    run: PipelineRun,
    result: ChatPipelineResult,
    *,
    strong_context: bool,
) -> AnswerCacheCandidate | None:
    """Wrap a fresh answer for storage, or ``None`` when it must not be reused.

    Stored answers are served to other visitors verbatim, so the turn has to
    be self-contained (first turn: no dialog history shaped the prompt) and
    the answer confident: strong retrieval, no reliability cap, and no
    escalation, handoff or clarification signal from the model.
    """
    state = run.state
    scope = state.answer_cache_scope
    retrieval = result.retrieval
    if (
        scope is None
        or state.guard_dialog_context is not None
        or not state.variant_vectors
        or not strong_context
        or retrieval is None
        or not retrieval.chunk_texts
        or retrieval.reliability.cap is not None
        or result.escalation_recommended
        or result.llm_offered_ticket
        or result.llm_needs_human
        or result.llm_clarifying
        or result.clarify_required_reason is not None
    ):
        return None
    answer = result.final_answer.strip()
    if not answer:
        return None
    return AnswerCacheCandidate(
        scope=scope,
        question_embedding=list(state.variant_vectors[0]),
        answer=CachedAnswer(
            answer=answer,
            strategy=result.strategy,
            document_ids=tuple(str(d) for d in retrieval.document_ids),
            scores=tuple(float(s) for s in retrieval.scores),
            chunk_texts=tuple(retrieval.chunk_texts),
            retrieval_mode=retrieval.mode,
            best_rank_score=retrieval.best_rank_score,
            best_confidence_score=retrieval.best_confidence_score,
            confidence_source=retrieval.confidence_source,
            reliability_score=retrieval.reliability.score,
            retrieval_ms=int(result.retrieval_ms),
            llm_ms=int(result.llm_ms) + int(result.llm_lang_retry_ms),
            created_at=_utcnow().isoformat(),
        ),
    )
