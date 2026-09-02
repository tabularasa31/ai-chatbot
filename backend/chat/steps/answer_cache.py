"""Answer-cache steps.

Before handler dispatch (``service.async_process_chat_message``):

* :func:`resolve_scope_for_turn` — the cache scope for the turn, or ``None``
  for turns the cache must stay out of: user context in the prompt, a
  PII-redacted question, any session state (operator hold, escalation
  states, closed chat) and any turn after the visitor's first question.
* :func:`exact_lookup` — level 1. Runs before the pre-dispatch classifiers
  and every guard, so a hit costs no OpenAI call at all.

Inside the RAG pipeline:

* :func:`semantic_lookup` — level 2, once the question embedding exists and
  the FAQ matcher has declined the turn.
* :func:`build_store_candidate` — whether the fresh answer is confident and
  free of visitor-specific content; the RAG handler performs the write once
  the turn is persisted.

Storage and key semantics live in ``backend/chat/answer_cache.py``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.chat import answer_cache
from backend.chat.answer_cache import (
    AnswerCacheCandidate,
    AnswerCacheHit,
    AnswerCacheLevel,
    AnswerCacheScope,
    CachedAnswer,
)
from backend.chat.language import ResolvedLanguageContext
from backend.chat.types import ChatPipelineResult, PipelineRun, PipelineState, RetrievalContext
from backend.models import MessageRole, OperatorState
from backend.models.base import _utcnow
from backend.observability import TraceHandle, record_stage_ms
from backend.search.service import default_retrieval_reliability

if TYPE_CHECKING:
    from backend.chat.handlers.base import HandlerContext

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


async def resolve_scope_for_turn(
    ctx: HandlerContext, db: AsyncSession
) -> AnswerCacheScope | None:
    chat = ctx.chat
    session_bound = (
        chat.operator_state is OperatorState.live
        or chat.ended_at is not None
        or bool(chat.escalation_awaiting_ticket_id)
        or bool(chat.escalation_pre_confirm_pending)
        or bool(chat.escalation_awaiting_request)
        or bool(chat.escalation_followup_pending)
        or any(m.role == MessageRole.user for m in chat.messages or [])
    )
    if (
        not ctx.question_text
        or not answer_cache.is_enabled()
        or ctx.user_context_line is not None
        or ctx.redacted_question != ctx.question
        or session_bound
    ):
        return None
    return await answer_cache.resolve_scope(
        tenant_id=ctx.tenant_id,
        bot_id=ctx.bot_id,
        response_language=ctx.language_context.response_language,
        question=ctx.redacted_question,
        agent_instructions=ctx.bot_agent_instructions,
        disclosure_config=ctx.disclosure_config,
        db=db,
    )


def _end_lookup_span(
    trace: TraceHandle | None,
    *,
    level: AnswerCacheLevel,
    started_at: float,
    hit: AnswerCacheHit | None,
) -> None:
    if trace is None:
        return
    duration_ms = round((perf_counter() - started_at) * 1000, 2)
    span = trace.span(name="answer-cache", input={"level": level})
    span.end(
        output={
            "hit": hit is not None,
            "similarity": hit.similarity if hit is not None else None,
            "saved_ms": hit.saved_ms if hit is not None else None,
        },
        metadata={"duration_ms": duration_ms},
    )
    record_stage_ms(trace, "answer_cache_ms", duration_ms)


def _hit_result(
    hit: AnswerCacheHit,
    language_context: ResolvedLanguageContext,
    state: PipelineState | None = None,
) -> ChatPipelineResult:
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
    telemetry = (
        dict(
            faq_match=state.faq_match,
            query_script=state.query_script or None,
            kb_scripts=list(state.kb_scripts) if state.kb_scripts else None,
            cross_lingual_triggered=state.cross_lingual_triggered,
            cross_lingual_variants_count=state.cross_lingual_variants_added,
            query_kb_language_match=state.query_kb_language_match if state.kb_scripts else None,
        )
        if state is not None
        else {}
    )
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
        language_context=language_context,
        answer_cache=hit,
        **telemetry,
    )


async def exact_lookup(
    scope: AnswerCacheScope,
    *,
    language_context: ResolvedLanguageContext,
    trace: TraceHandle | None,
) -> ChatPipelineResult | None:
    started_at = perf_counter()
    cached = await answer_cache.exact_get(scope)
    hit = AnswerCacheHit(level="exact", similarity=1.0, answer=cached) if cached else None
    _end_lookup_span(trace, level="exact", started_at=started_at, hit=hit)
    if hit is None:
        return None
    return _hit_result(hit, language_context)


async def semantic_lookup(run: PipelineRun) -> ChatPipelineResult | None:
    scope = run.answer_cache_scope
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
    _end_lookup_span(run.trace, level="semantic", started_at=started_at, hit=hit)
    if hit is None:
        return None
    # The relevance guard was launched alongside the embedding; a cached
    # answer already passed it when it was generated, so the verdict is not
    # awaited (same as the faq_direct short-circuit).
    if state.rel_task is not None and not state.rel_task.done():
        state.rel_task.cancel()
        await asyncio.gather(state.rel_task, return_exceptions=True)
    await answer_cache.promote_exact(scope, hit.answer)
    return _hit_result(hit, run.language_context, state)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.casefold()))


def _answer_echoes_question_specifics(
    question: str, answer: str, chunk_texts: list[str]
) -> bool:
    """True when the answer repeats a detail the visitor typed that the
    retrieved documents do not contain — a name, an order or account number.

    The structural PII redactor does not know such tokens; digit-bearing and
    capitalised ones (past the first word) are the language-agnostic proxy.
    """
    grounded = _tokens(" ".join(chunk_texts))
    answer_tokens = _tokens(answer)
    raw_tokens = _TOKEN_RE.findall(question)
    for index, token in enumerate(raw_tokens):
        specific = any(ch.isdigit() for ch in token) or (index > 0 and token[:1].isupper())
        folded = token.casefold()
        if specific and folded not in grounded and folded in answer_tokens:
            return True
    return False


def build_store_candidate(
    run: PipelineRun,
    result: ChatPipelineResult,
    *,
    strong_context: bool,
) -> AnswerCacheCandidate | None:
    """Wrap a fresh answer for storage, or ``None`` when it must not be reused:
    weak retrieval or a reliability cap, any escalation / handoff / clarify
    signal from the model, or an answer that echoes visitor-specific detail.
    """
    state = run.state
    scope = run.answer_cache_scope
    retrieval = result.retrieval
    if (
        scope is None
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
    if not answer or _answer_echoes_question_specifics(run.question, answer, retrieval.chunk_texts):
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
