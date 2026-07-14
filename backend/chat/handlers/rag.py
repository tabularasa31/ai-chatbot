"""RAG handler module — and the chat pipeline's public / test-seam surface.

The pipeline itself lives in ``backend/chat/pipeline.py`` (orchestrator) and
``backend/chat/steps/`` (one module per step); shared dataclasses are in
``backend/chat/types.py``, prompt assembly in ``backend/chat/prompts.py`` and
stream filters in ``backend/chat/streaming.py``. This module keeps:

* :class:`RagHandler` — runs after the async pipeline has already produced a
  ``ChatPipelineResult``: ``service._async_dispatch`` precomputes that result
  and stashes it in ``ctx.extras['_pipeline_result']`` before invoking the
  handler. The handler is then responsible for persistence, analytics, and
  escalation side effects only.
* The decision-side helpers the handler feeds into ``decide()``
  (:func:`_classify_kb_confidence`, :func:`_compute_loop_signal`, …).
* Re-exports of every pipeline symbol that used to be defined here, so
  existing imports keep working.

Test seams: this module is the documented monkeypatch surface for the
generation hop. The pipeline resolves ``async_generate_answer``,
``detect_language`` and ``translate_text_result`` through THIS module's
globals at call time, so
``monkeypatch.setattr("backend.chat.handlers.rag.<name>", ...)`` intercepts
the call sites that live in ``backend/chat/steps/generate.py`` and
``backend/chat/streaming.py``. Helpers that tests monkeypatch on
``backend.chat.service`` (e.g. ``async_detect_injection``, ``match_faq``,
``capture_event``, ``async_retrieve_context``) are likewise looked up
dynamically via ``backend.chat.service`` inside the steps.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.util import await_only

from backend.chat.decision import KbConfidence

# --- Pipeline surface (moved out of this module; re-exported for callers) ---
from backend.chat.events import (
    _emit_no_rag_hits_event,  # noqa: F401  (re-export)
    _emit_quick_answer_lookup_event,  # noqa: F401  (re-export)
    _emit_speculative_retrieval_event,  # noqa: F401  (re-export)
    _metrics_distinct_id,  # noqa: F401  (re-export)
)
from backend.chat.handlers.base import ChatTurnOutcome, HandlerContext, PipelineHandler

# Language helpers: imported here (not only in the language module) because
# this module is the monkeypatch surface for detect_language /
# translate_text_result — see the module docstring.
from backend.chat.language import (
    LangDetectError,  # noqa: F401  (re-export)
    ResolvedLanguageContext,  # noqa: F401  (re-export)
    _language_root,  # noqa: F401  (re-export)
    async_localize_text_to_language_result,  # noqa: F401  (re-export)
    detect_language,  # noqa: F401  (test seam + re-export)
    language_display_name,  # noqa: F401  (re-export)
    log_llm_tokens,  # noqa: F401  (re-export)
    render_direct_faq_answer_result,  # noqa: F401  (re-export)
    translate_text_result,  # noqa: F401  (test seam + re-export)
)
from backend.chat.pipeline import async_run_chat_pipeline  # noqa: F401  (re-export)
from backend.chat.prompts import (
    CLARIFICATION_POLICY,  # noqa: F401  (re-export)
    CONTEXT_FORMAT_NOTE,  # noqa: F401  (re-export)
    DISCLOSURE_HARD_LIMITS,  # noqa: F401  (re-export)
    DISCLOSURE_LEVEL_INSTRUCTIONS,  # noqa: F401  (re-export)
    OUTPUT_LANGUAGE_POLICY,  # noqa: F401  (re-export)
    _user_context_prompt_line,  # noqa: F401  (re-export)
    build_rag_messages,  # noqa: F401  (re-export)
    build_rag_prompt,  # noqa: F401  (re-export)
)
from backend.chat.steps.generate import (
    _assemble_chat_messages,  # noqa: F401  (re-export)
    _async_generate_answer_native,  # noqa: F401  (re-export)
    _build_prior_messages_for_llm,  # noqa: F401  (re-export)
    _enforce_response_language,  # noqa: F401  (re-export)
    _safe_int,  # noqa: F401  (re-export)
    async_generate_answer,  # noqa: F401  (test seam + re-export)
)
from backend.chat.steps.pre_retrieval import (
    _async_lookup_quick_answers,  # noqa: F401  (re-export)
    _lookup_quick_answers,  # noqa: F401  (re-export)
    _quick_answer_keys_for_question,  # noqa: F401  (re-export)
    _quick_answer_quality_score,  # noqa: F401  (re-export)
    _quick_answers_context,  # noqa: F401  (re-export)
    _should_skip_query_rewrite,  # noqa: F401  (re-export)
)
from backend.chat.steps.retrieval import async_retrieve_context  # noqa: F401  (re-export)
from backend.chat.streaming import (
    OFFER_MARKER,  # noqa: F401  (re-export)
    LanguageGateStreamFilter,  # noqa: F401  (re-export)
    LanguageMismatchStreamAbortError,  # noqa: F401  (re-export)
    OfferMarkerStreamFilter,  # noqa: F401  (re-export)
    ThoughtStreamFilter,  # noqa: F401  (re-export)
    _CitationStreamFilter,  # noqa: F401  (re-export)
    _scrub_offer_marker_literal,  # noqa: F401  (re-export)
    _strip_and_detect_offer_marker,  # noqa: F401  (re-export)
    _strip_inline_citations,  # noqa: F401  (re-export)
    _strip_thought_tags,  # noqa: F401  (re-export)
)
from backend.chat.types import (
    ChatPipelineResult,
    RetrievalContext,
    _empty_retrieval_context,  # noqa: F401  (re-export)
)
from backend.chat.types import (
    PipelineState as _PipelineState,  # noqa: F401  (re-export, legacy name)
)
from backend.core.config import settings
from backend.models import Chat, MessageRole
from backend.observability import record_stage_ms

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision-side helpers: retrieval-confidence classification and the
# loop-detection heuristic RagHandler feeds into decide().
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_THRESHOLD = 0.4
_ESCALATION_THRESHOLD = 0.45  # upper bound for "high" KB confidence (see _classify_kb_confidence)

_KB_CONFIDENCE_RANK: dict[KbConfidence, int] = {"low": 0, "medium": 1, "high": 2}


def _floor_kb_confidence(raw: KbConfidence, ceiling: KbConfidence) -> KbConfidence:
    """Return the lower of two KbConfidence tiers."""
    if _KB_CONFIDENCE_RANK[ceiling] < _KB_CONFIDENCE_RANK[raw]:
        return ceiling
    return raw


def _classify_kb_confidence(retrieval: RetrievalContext | None) -> KbConfidence:
    """Map retrieval confidence score to the three-tier KbConfidence used by decide().

    When `retrieval.reliability.cap` is set (contradiction cap → ``low``,
    source_overlap cap → ``medium``), the raw similarity-based tier is floored
    by that cap so the cap actually reaches the decision engine. Without this
    floor, caps live only in observability. We deliberately do NOT floor by
    `reliability.score` because the reliability score uses stricter base
    thresholds (`high` only at top score ≥0.8) than this classifier
    (`high` at ≥0.45); flooring by score would silently downgrade many
    uncapped high-confidence queries to medium.
    """
    if retrieval is None or retrieval.best_confidence_score is None:
        return "low"
    score = retrieval.best_confidence_score
    if score >= _ESCALATION_THRESHOLD:
        raw: KbConfidence = "high"
    elif score >= LOW_CONFIDENCE_THRESHOLD:
        raw = "medium"
    else:
        raw = "low"
    cap = retrieval.reliability.cap
    if cap is None:
        return raw
    return _floor_kb_confidence(raw, cap)


@dataclass(frozen=True)
class LoopSignal:
    """Result of the loop-detection heuristic for one turn.

    ``detected`` (the escalation trigger) requires BOTH component signals:
    ``docs_repeat`` — trailing assistant turns drew on the same documents —
    and ``questions_repeat`` — the current user question repeats a recent
    prior one. Docs overlap alone must never escalate: a tenant whose whole
    KB is a single document yields overlap=1.0 on every coherent
    conversation, and escalating there throws away a correct generated
    answer (see decision.py Block rule 6b).
    """

    detected: bool = False
    docs_repeat: bool = False
    doc_overlap_ratio: float | None = None
    questions_repeat: bool = False
    question_similarity: float | None = None
    window_size: int = 0


def _question_tokens(text: str | None) -> frozenset[str]:
    """Lowercased word tokens for question-similarity comparison.

    ``\\w`` is Unicode-aware, so this works for Cyrillic and other
    non-Latin scripts without language-specific handling.
    """
    if not text:
        return frozenset()
    return frozenset(re.findall(r"\w+", text.lower()))


def _compute_loop_signal(
    chat: Chat | None,
    *,
    current_question: str | None,
    window: int,
    min_overlap: float,
    min_question_similarity: float,
) -> LoopSignal:
    """Detect whether the user is stuck in a loop: the last ``window``
    assistant turns drew on the same knowledge-base documents AND the
    current question repeats one of the questions that produced them —
    a signal that re-answering won't help.

    Component signals:
      - docs_repeat: max pairwise Jaccard overlap of ``source_documents``
        among the trailing assistant turns >= ``min_overlap``. We use *max*
        rather than mean so that two near-identical turns sandwiching a
        slightly different one still count — the user is clearly orbiting
        the same documents.
      - questions_repeat: max length-weighted token-Jaccard similarity
        between ``current_question`` and the user questions that preceded
        those assistant turns >= ``min_question_similarity``. This is what
        separates "user asks the same thing again and again" (a real loop)
        from "coherent conversation around one document" (single-document
        tenants, where docs overlap is 1.0 by construction).

    Returns a no-loop ``LoopSignal`` when:
      - chat is None or has < window assistant turns with source documents,
      - any of the inspected turns has an empty ``source_documents`` set
        (we can't reason about overlap with an empty set; treating it as
        no-loop is the safe default to avoid false-positive escalations).
    """
    if chat is None or window < 2:
        return LoopSignal()
    # Walk backwards from the most recent message and stop once we have
    # ``window`` assistant turns or hit a doc-less assistant turn (greeting /
    # handoff) that resets the chain. Avoids the O(N) full-history
    # sort and the (datetime, UUID) type-comparison risk of ``created_at or id``.
    persisted = sorted(
        chat.messages or [],
        key=lambda m: (m.created_at or datetime.min, str(m.id)),
        reverse=True,
    )
    inspected_reverse: list[frozenset[str]] = []
    prior_questions: list[str | None] = []
    for idx, m in enumerate(persisted):
        if m.role != MessageRole.assistant:
            continue
        docs = m.source_documents or []
        if not docs:
            break
        inspected_reverse.append(frozenset(str(d) for d in docs))
        # The user question that produced this assistant turn: the closest
        # preceding user message. None when absent (e.g. proactive turns).
        prior_questions.append(
            next(
                (
                    p.content
                    for p in persisted[idx + 1 :]
                    if p.role == MessageRole.user
                ),
                None,
            )
        )
        if len(inspected_reverse) >= window:
            break
    if len(inspected_reverse) < window:
        return LoopSignal(window_size=len(inspected_reverse))
    inspected = list(reversed(inspected_reverse))
    max_overlap = 0.0
    for i in range(len(inspected)):
        for j in range(i + 1, len(inspected)):
            a, b = inspected[i], inspected[j]
            union = a | b
            if not union:
                continue
            overlap = len(a & b) / len(union)
            if overlap > max_overlap:
                max_overlap = overlap
    docs_repeat = max_overlap >= min_overlap

    # Question similarity: current question vs each question in the window.
    # Length-weighted Jaccard — function words are short across languages
    # (Zipf), so weighting tokens by length discounts shared scaffolding
    # ("how do I …", "как мне …") without language-specific stopword lists.
    # Plain Jaccard rates "how do I cancel" vs "how do I install" at 0.6 —
    # a false repeat that would re-trigger the discarded-answer bug this
    # heuristic exists to avoid. Missing texts yield similarity 0.0 — the
    # safe default is to deliver the generated answer rather than escalate.
    current_tokens = _question_tokens(current_question)
    max_similarity = 0.0
    for prior in prior_questions:
        prior_tokens = _question_tokens(prior)
        union_weight = sum(len(t) for t in current_tokens | prior_tokens)
        if not union_weight:
            continue
        shared_weight = sum(len(t) for t in current_tokens & prior_tokens)
        similarity = shared_weight / union_weight
        if similarity > max_similarity:
            max_similarity = similarity
    questions_repeat = max_similarity >= min_question_similarity

    return LoopSignal(
        detected=docs_repeat and questions_repeat,
        docs_repeat=docs_repeat,
        doc_overlap_ratio=max_overlap,
        questions_repeat=questions_repeat,
        question_similarity=max_similarity,
        window_size=window,
    )


def _clarify_anchor_turn_id(chat: Chat | None) -> str | None:
    """Return the id of the most recent persisted user message in ``chat``,
    or None when there is no prior user turn. Used only as metadata when the
    current turn is a clarify, to make context-drift bugs (LLM grabbing the
    wrong prior turn) visible in Langfuse without changing decision logic.
    """
    if chat is None:
        return None
    # Walk newest-first and return on the first user turn — avoids sorting
    # the entire history twice (once asc, then reversed) on long sessions.
    persisted = sorted(
        chat.messages or [],
        key=lambda m: (m.created_at or datetime.min, str(m.id)),
        reverse=True,
    )
    for m in persisted:
        if m.role == MessageRole.user:
            return str(m.id)
    return None


# ---------------------------------------------------------------------------
# RagHandler — consumes the pipeline result and owns persistence, analytics
# and escalation side effects.
# ---------------------------------------------------------------------------


class RagHandler(PipelineHandler):
    """Catch-all handler that runs the full RAG pipeline.

    Invoked after Greeting / EscalationStateMachine handlers; this
    one always claims a turn (``can_handle`` returns True for non-empty input
    that didn't trigger an earlier handler). Owns:

      * consuming the precomputed ``ChatPipelineResult`` produced by
        ``async_run_chat_pipeline`` (stashed in ``ctx.extras`` by
        ``_async_dispatch``) — guard rejects, faq_direct fast paths,
        RAG/faq_context normal paths
      * building the policy ``TurnContext``, calling ``decide()``, and
        promoting the decision to escalation if needed
      * persisting the turn (user + assistant messages), emitting analytics
        events, ingesting gap signals, firing the log-analysis threshold
    """

    def can_handle(self, ctx: HandlerContext) -> bool:
        # Empty input is GreetingHandler's domain or rejected outright; anything
        # else falls through to RAG once earlier handlers decline.
        return bool(ctx.question_text)

    async def handle(self, ctx: HandlerContext) -> ChatTurnOutcome:
        from backend.core.db import run_sync

        return await run_sync(ctx.async_db, lambda sync_db: self._handle_sync(ctx, sync_db))

    def _handle_sync(
        self, ctx: HandlerContext, sync_db: Session
    ) -> ChatTurnOutcome:
        ctx.db = sync_db
        from time import perf_counter

        from backend.chat import service as _svc
        from backend.chat.decision import (
            MAX_CLARIFICATIONS_PER_SESSION,
            Decision,
            DecisionKind,
            decide,
        )
        from backend.chat.decision import (
            TurnContext as DecisionTurnContext,
        )
        from backend.escalation.openai_escalation import pre_confirm_fallback_result
        from backend.escalation.service import (
            build_chat_messages_for_openai,
            chunks_preview_from_results,
        )
        from backend.models import EscalationTrigger
        from backend.search.service import (
            build_reliability_projection,
            build_variant_trace_metadata,
            build_variant_trace_tag,
        )

        # Pull side-effecting helpers via the service module so tests' monkey-
        # patches against ``backend.chat.service.X`` keep affecting these calls.
        _emit_chat_escalated_event = _svc._emit_chat_escalated_event
        _emit_chat_turn_event = _svc._emit_chat_turn_event
        _persist_turn_with_response_language = _svc._persist_turn_with_response_language
        _trigger_log_analysis_threshold = _svc._trigger_log_analysis_threshold
        _try_ingest_gap_signal = _svc._try_ingest_gap_signal
        render_pre_confirm_text = _svc.render_pre_confirm_text

        chat = ctx.chat
        # ``_async_dispatch`` always pre-computes the async pipeline result and
        # stores it in ``ctx.extras`` before invoking the handler, so this
        # handler is now strictly a persistence + analytics + escalation step.
        result = ctx.extras.get("_pipeline_result")
        if not isinstance(result, ChatPipelineResult):
            # Use raise rather than assert so the contract still holds when
            # the interpreter runs with -O (assertions stripped).
            raise RuntimeError(
                "RagHandler.handle requires a precomputed pipeline result "
                "in ctx.extras['_pipeline_result']; _async_dispatch must run "
                "async_run_chat_pipeline before invoking the handler."
            )
        if ctx.carryover_tokens:
            result.tokens_used += ctx.carryover_tokens
            ctx.carryover_tokens = 0

        # Guard rejects and faq_direct: persist and return immediately (no escalation).
        if result.is_reject or result.is_faq_direct:
            # Arm the rephrase-prompt tracker only on the zero-hits soft reply.
            # Every other reply path (including other reject reasons and
            # faq_direct) is implicitly reset to False by the persistence
            # layer's default argument — handlers that bypass RagHandler
            # (Greeting, Escalation) get the same reset for free.
            user_message, assistant_message = _persist_turn_with_response_language(
                db=ctx.db,
                chat=chat,
                tenant_id=ctx.tenant_id,
                response_language=ctx.language_context.response_language,
                resolution_reason=ctx.language_context.response_language_resolution_reason,
                user_content=ctx.question,
                assistant_content=result.final_answer,
                document_ids=[],
                extra_tokens=result.tokens_used,
                optional_entity_types=ctx.optional_entity_types,
                language_context=ctx.language_context,
                trace=ctx.trace,
                set_rephrase_flag=(result.reject_reason == "rephrase"),
            )
            _try_ingest_gap_signal(
                chat=chat,
                tenant_id=ctx.tenant_id,
                session_id=ctx.session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                question_text=ctx.redacted_question,
                answer_confidence=(
                    result.retrieval.best_confidence_score if result.retrieval is not None else None
                ),
                was_rejected=result.is_reject,
                had_fallback=False,
                was_escalated=False,
                language=ctx.language_context.response_language,
            )
            source_map = {
                "injection": "guard_reject_injection",
                "not_relevant": "guard_reject_not_relevant",
                "low_retrieval": "guard_reject_low_retrieval",
                "rephrase": "guard_reject_rephrase",
                "social": "guard_social_reply",
                "social_question": "guard_social_question_reply",
            }
            if result.is_reject:
                source = source_map.get(result.reject_reason or "", "guard_reject")
            else:
                source = "faq_direct"
            if ctx.trace is not None:
                ctx.trace.update(
                    output={"answer": result.final_answer, "source": source},
                    metadata={
                        "chat_ended": False,
                        "escalated": False,
                        "strategy": result.strategy,
                        "reject_reason": result.reject_reason,
                        "retrieval_skipped": result.is_faq_direct,
                        "response_language": ctx.language_context.response_language,
                    },
                )
            _emit_chat_turn_event(
                tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
                bot_public_id=ctx.bot_public_id,
                chat_id=str(chat.id) if chat is not None else None,
                session_id=str(chat.session_id) if chat is not None else None,
                strategy=result.strategy,
                reject_reason=result.reject_reason,
                is_reject=result.is_reject,
                escalated=False,
                identified=bool(ctx.user_context),
                latency_ms=int((perf_counter() - ctx.turn_started_at) * 1000),
                retrieval_ms=result.retrieval_ms,
                llm_ms=result.llm_ms,
                llm_lang_retry_ms=result.llm_lang_retry_ms,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
                query_script=result.query_script,
                kb_scripts=result.kb_scripts,
                cross_lingual_triggered=result.cross_lingual_triggered,
                cross_lingual_variants_count=result.cross_lingual_variants_count,
                query_kb_language_match=result.query_kb_language_match,
                retrieval_used_cross_lingual_variant=result.retrieval_used_cross_lingual_variant,
                model=settings.chat_model,
                plan_tier=(ctx.effective_user_ctx or {}).get("plan_tier"),
            )
            return ChatTurnOutcome(
                text=result.final_answer,
                document_ids=[],
                tokens_used=result.tokens_used,
                chat_ended=False,
                chat_id=str(chat.id) if chat is not None else None,
            )

        # Normal RAG / faq_context path: handle escalation side effects, then persist.
        retrieval = result.retrieval
        assert retrieval is not None  # only None for guard_reject / faq_direct
        # The rephrase tracker is reset centrally by the persistence layer
        # (set_rephrase_flag defaults to False) when this turn commits.
        document_ids = list(dict.fromkeys(retrieval.document_ids))
        scores = retrieval.scores
        chunk_texts = retrieval.chunk_texts
        answer = result.final_answer
        tokens_used = result.tokens_used
        escalate = result.escalation_recommended
        esc_trigger = result.escalation_trigger
        reliability_score = retrieval.reliability.score

        # Build TurnContext and call decide() to get the formal policy decision.
        # This is the single authoritative classification of what this turn produced.
        faq_match_obj = result.faq_match
        _kb_confidence = _classify_kb_confidence(retrieval)
        _loop_signal = _compute_loop_signal(
            chat,
            current_question=ctx.question,
            window=settings.loop_detection_window,
            min_overlap=settings.loop_detection_min_overlap,
            min_question_similarity=settings.loop_detection_min_question_similarity,
        )
        _turn_ctx = DecisionTurnContext(
            session_closed=(chat.ended_at is not None),
            active_escalation=(
                chat.escalation_awaiting_ticket_id is not None
                or chat.escalation_pre_confirm_pending
                or chat.escalation_followup_pending
            ),
            clarification_count=chat.clarification_count,
            max_clarifications=MAX_CLARIFICATIONS_PER_SESSION,
            guard_failed=result.is_reject,
            guard_reason=result.reject_reason,
            explicit_human_request=ctx.explicit_human_request,
            faq_direct_hit=result.is_faq_direct,
            faq_top_score=faq_match_obj.top_score if faq_match_obj else None,
            kb_confidence=_kb_confidence,
            # Partial-answer signal: only medium-confidence chunks constitute a usable
            # partial answer. Low-confidence chunks are too unreliable to caveat from;
            # the budget-exhausted path escalates instead (clarify_loop_limit).
            kb_has_partial_answer=_kb_confidence == "medium" and bool(chunk_texts),
            kb_contradiction_detected=(
                retrieval.reliability.cap_reason == "contradiction"
            ),
            low_retrieval_no_chunks=not chunk_texts,
            loop_detected=_loop_signal.detected,
            loop_overlap_ratio=_loop_signal.doc_overlap_ratio,
            loop_window_size=_loop_signal.window_size,
            loop_docs_repeat=_loop_signal.docs_repeat,
            loop_questions_repeat=_loop_signal.questions_repeat,
            loop_question_similarity=_loop_signal.question_similarity,
        )
        _decision: Decision = decide(_turn_ctx)

        # Enforce policy decision: clarify_loop_limit and loop_detected escalations
        # must become real escalations even when the RAG pipeline did not
        # independently recommend it. Both route through the same pre-confirm
        # handoff, just with a different escalate_reason in the trace.
        if (
            _decision.kind == DecisionKind.escalate
            and _decision.escalate_reason
            in ("clarify_loop_limit", "loop_detected_repeat_source_docs")
            and not escalate
        ):
            escalate = True
            esc_trigger = EscalationTrigger.low_similarity

        # Increment clarification counter when the pipeline produced a blocking clarify.
        _clarification_count_before = chat.clarification_count
        if _decision.is_blocking_clarify():
            chat.clarification_count += 1
            ctx.db.add(chat)
            # Counter is committed in the same transaction as the assistant message below.

        if ctx.trace is not None:
            escalation_decision_span = ctx.trace.span(
                name="escalation-check",
                input={
                    "best_confidence_score": retrieval.best_confidence_score,
                    "chunk_count": len(chunk_texts),
                    "reliability_score": reliability_score,
                },
            )
            escalation_decision_span.end(
                output={
                    "escalate": escalate,
                    "trigger": esc_trigger.value if esc_trigger else None,
                    "reliability_score": reliability_score,
                }
            )
            if reliability_score == "low" or escalate:
                ctx.trace.promote(
                    metadata={
                        "sampling_promoted": True,
                        "promotion_reason": "low_reliability_or_escalation",
                    }
                )
        created_ticket_number: str | None = None
        # When True, render_pre_confirm_text failed below and the user-facing
        # reply remained the rephrase-prompt fallback that the pipeline pinned
        # to ``result.final_answer``. The persistence call at the end of this
        # method threads this through ``set_rephrase_flag`` so the next turn
        # still treats the chat as in the consecutive-zero-hits state and
        # retries escalation once OpenAI recovers, rather than restarting from
        # a fresh "first rephrase" prompt.
        _escalation_render_failed_on_zero_hits = False
        if escalate and esc_trigger is not None:
            try:
                preview = chunks_preview_from_results(document_ids, scores, chunk_texts)
                # Store context for deferred ticket creation after user confirms.
                # Ticket is NOT created here — user must confirm first.
                chat.escalation_pre_confirm_pending = True
                chat.escalation_pre_confirm_context = {
                    "trigger": esc_trigger.value,
                    "primary_question": ctx.question,
                    "best_similarity_score": retrieval.best_confidence_score,
                    "retrieved_chunks": preview,
                }
                # Render the canonical pre_confirm message from a static
                # template (same path PR #681 introduced for explicit human
                # requests) instead of letting the general escalation LLM
                # free-text it. The "no_answer" variant leads with a brief
                # "I couldn't find an answer" before the handoff question, so
                # this single message *replaces* the RAG verdict rather than
                # appending to it — no "two voices in one reply". The FSM state
                # set above is committed together with the assistant message by
                # the downstream _persist_turn_with_response_language call.
                #
                # We are inside a SQLAlchemy run_sync greenlet that runs ON the
                # event loop thread; ``render_pre_confirm_text`` is async, so
                # bridge back to the loop via ``await_only``.
                # "How do I contact support?" is an informational question the
                # human-request classifier deliberately routes to RAG (not an
                # immediate handoff). When the KB has no contact page it lands
                # here on a retrieval miss — but the bot itself IS the support
                # channel, so leading with "I couldn't find an answer" misframes
                # the handoff as a failure. The intent is classified up front
                # (in parallel with the human-request classifier) and threaded
                # via ``ctx``, so this path adds no extra serialized LLM call.
                if esc_trigger == EscalationTrigger.user_complaint:
                    # Guard-detected complaint about support silence: lead
                    # with an apology, not "I couldn't find an answer".
                    _pre_confirm_variant = "support_complaint"
                elif ctx.support_contact_question:
                    _pre_confirm_variant = "support_contact"
                else:
                    _pre_confirm_variant = "no_answer"
                _esc_openai_start = perf_counter()
                try:
                    esc = await_only(
                        asyncio.wait_for(
                            render_pre_confirm_text(
                                variant=_pre_confirm_variant,
                                response_language=ctx.language_context.response_language,
                                api_key=ctx.api_key,
                                tenant_id=str(ctx.tenant_id),
                                bot_id=str(ctx.bot_id) if ctx.bot_id else None,
                                chat_id=str(chat.id),
                                # Dialog transcript makes the offer context-
                                # aware: acknowledge the user's problem and say
                                # what will be summarized for support instead
                                # of the canned forwarding question
                                # (86exn3x9u). Degrades to the canonical
                                # template inside the renderer on failure.
                                chat_messages=build_chat_messages_for_openai(
                                    chat, ctx.redacted_question
                                ),
                            ),
                            timeout=settings.escalation_pre_confirm_render_timeout_seconds,
                        )
                    )
                except TimeoutError:
                    # Hard deadline: keep the escalation armed and degrade to
                    # the canonical English template rather than stalling the
                    # turn on a slow localization call (observed up to 21s).
                    logger.warning(
                        "pre-confirm render exceeded %.1fs, using canonical template",
                        settings.escalation_pre_confirm_render_timeout_seconds,
                    )
                    esc = pre_confirm_fallback_result(_pre_confirm_variant)
                record_stage_ms(
                    ctx.trace,
                    "escalation_openai_ms",
                    round((perf_counter() - _esc_openai_start) * 1000, 2),
                )
                answer = esc.message_to_user
                tokens_used = tokens_used + esc.tokens_used
                # The reply is now the generic handoff question, not a RAG
                # answer — drop the retrieved sources so we don't persist or
                # return citations that don't back this message.
                document_ids = []
                ctx.db.add(chat)
            except Exception as e:
                logger.warning("Escalation T-1/T-2 pre-confirm failed, returning RAG answer only: %s", e)
                chat.escalation_pre_confirm_pending = False
                chat.escalation_pre_confirm_context = None
                # If this escalation came from the consecutive-zero-hits fast
                # path, ``answer`` is the rephrase-prompt fallback, not a real
                # RAG verdict. Preserve the rephrase tracker so the next turn
                # can still detect a consecutive zero-hits state and retry
                # escalation when OpenAI recovers — otherwise the user is
                # stuck looping on the same soft-reply.
                if (
                    esc_trigger == EscalationTrigger.no_documents
                    and not chunk_texts
                ):
                    _escalation_render_failed_on_zero_hits = True

        # Safety net: decide() may classify a turn as a confident answer while
        # the LLM still ends its reply with a ticket offer (the system prompt
        # allows this when it judges the docs incomplete). The LLM signals
        # the offer by appending OFFER_MARKER, which async_generate_answer strips
        # and surfaces as ``result.llm_offered_ticket``. Without arming
        # pre_confirm here, the user's "yes" / "да" / "ja" / "oui" reaches the
        # next turn as an ordinary RAG query and is answered afresh instead of
        # being read as acceptance of the ticket offer. The marker is
        # machine-emitted, so this works in any response language.
        if (
            not escalate
            and not chat.escalation_pre_confirm_pending
            and result.llm_offered_ticket
        ):
            chat.escalation_pre_confirm_pending = True
            chat.escalation_pre_confirm_context = {
                "trigger": EscalationTrigger.llm_self_offer.value,
                "primary_question": ctx.question,
                "best_similarity_score": retrieval.best_confidence_score,
                "retrieved_chunks": chunks_preview_from_results(
                    document_ids, scores, chunk_texts
                ),
            }
            ctx.db.add(chat)
            logger.info(
                "Armed escalation_pre_confirm_pending from LLM-offered ticket "
                "(decide=%s, escalate=False) chat_id=%s",
                _decision.kind.value,
                chat.id,
            )

        # Both branches (RAG answer or escalation handoff) write to the user in
        # response_language. escalation_language stays for tenant-side artifacts
        # only and must not leak into the chat reply.
        user_message, assistant_message = _persist_turn_with_response_language(
            db=ctx.db,
            chat=chat,
            tenant_id=ctx.tenant_id,
            response_language=ctx.language_context.response_language,
            resolution_reason=ctx.language_context.response_language_resolution_reason,
            user_content=ctx.question,
            assistant_content=answer,
            set_rephrase_flag=_escalation_render_failed_on_zero_hits,
            document_ids=document_ids,
            extra_tokens=tokens_used,
            optional_entity_types=ctx.optional_entity_types,
            language_context=ctx.language_context,
            trace=ctx.trace,
        )
        _try_ingest_gap_signal(
            chat=chat,
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            question_text=ctx.redacted_question,
            answer_confidence=retrieval.best_confidence_score,
            was_rejected=False,
            had_fallback=False,
            was_escalated=bool(escalate),
            language=ctx.language_context.response_language,
        )

        # Phase 4: fire-and-forget threshold check — never blocks the response.
        _trigger_log_analysis_threshold(ctx.tenant_id, ctx.api_key)

        faq_match = result.faq_match
        if ctx.trace is not None:
            ctx.trace.update(
                output={"answer": answer},
                metadata={
                    "chat_ended": bool(chat.ended_at),
                    "escalated": bool(escalate),
                    "escalation_trigger": esc_trigger.value if esc_trigger else None,
                    "response_language": ctx.language_context.response_language,
                    "response_language_resolution_reason": ctx.language_context.response_language_resolution_reason,
                    "escalation_language": ctx.language_context.escalation_language,
                    "escalation_language_source": ctx.language_context.escalation_language_source,
                    "strategy": result.strategy,
                    "retrieval_mode": retrieval.mode,
                    "best_rank_score": retrieval.best_rank_score,
                    "best_confidence_score": retrieval.best_confidence_score,
                    "source_document_ids": [str(document_id) for document_id in document_ids],
                    "tokens_used": int(tokens_used),
                    **(
                        {
                            "faq_strategy": faq_match.strategy,
                            "faq_top_score": faq_match.top_score,
                            "faq_selected_score": faq_match.selected_score,
                        }
                        if faq_match is not None
                        else {}
                    ),
                    **build_reliability_projection(retrieval.reliability),
                    **build_variant_trace_metadata(retrieval),
                    # Clarification policy trace fields (spec §Trace fields)
                    **_decision.trace_dict(_clarification_count_before),
                    # Loop-detection signals (always emitted so dashboards can
                    # distinguish "not evaluated" from "evaluated false").
                    **_decision.loop_trace_dict(_turn_ctx),
                    # Clarify-anchor traceability: when this turn is a clarify,
                    # record the id of the closest prior user message — the LLM
                    # composes the clarify question from prior_messages, so this
                    # is the natural anchor for debugging context drift.
                    **(
                        {
                            "clarify_anchor_turn_id": _clarify_anchor_turn_id(chat),
                        }
                        if _decision.kind == DecisionKind.clarify
                        else {}
                    ),
                    "allow_clarification": ctx.allow_clarification,
                    "intent_top_class": None,   # no classifier in v1
                    "intent_top_score": None,
                    "intent_runner_up_score": None,
                    "faq_top_score": faq_match.top_score if faq_match else None,
                    "kb_has_partial_answer": bool(chunk_texts),
                },
                tags=[build_variant_trace_tag(retrieval.variant_mode)],
            )
        _emit_chat_turn_event(
            tenant_public_id=getattr(ctx.tenant_row, "public_id", None),
            bot_public_id=ctx.bot_public_id,
            chat_id=str(chat.id) if chat is not None else None,
            session_id=str(chat.session_id) if chat is not None else None,
            strategy=result.strategy,
            reject_reason=None,
            is_reject=False,
            escalated=bool(escalate),
            identified=bool(ctx.user_context),
            latency_ms=int((perf_counter() - ctx.turn_started_at) * 1000),
            retrieval_ms=result.retrieval_ms,
            llm_ms=result.llm_ms,
            llm_lang_retry_ms=result.llm_lang_retry_ms,
            tokens_input=result.tokens_input,
            tokens_output=result.tokens_output,
            reliability_score=reliability_score,
            best_confidence_score=retrieval.best_confidence_score,
            decision=_decision,
            escalation_trigger=esc_trigger.value if esc_trigger else None,
            query_script=result.query_script,
            kb_scripts=result.kb_scripts,
            cross_lingual_triggered=result.cross_lingual_triggered,
            cross_lingual_variants_count=result.cross_lingual_variants_count,
            query_kb_language_match=result.query_kb_language_match,
            retrieval_used_cross_lingual_variant=result.retrieval_used_cross_lingual_variant,
            model=settings.chat_model,
            plan_tier=(ctx.effective_user_ctx or {}).get("plan_tier"),
        )
        return ChatTurnOutcome(
            text=answer,
            document_ids=document_ids,
            tokens_used=tokens_used,
            chat_ended=bool(chat.ended_at),
            ticket_number=created_ticket_number,
            chat_id=str(chat.id) if chat is not None else None,
        )
