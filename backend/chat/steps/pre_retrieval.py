"""Pre-retrieval pipeline steps.

Everything that runs before vector/lexical retrieval, in orchestrator order:

* :func:`prepare_turn` — tenant/guard profile, KB-script detection, dialog
  context, query-rewrite gating.
* :func:`injection_guard` — 2-level injection detector (gating: on detection
  no downstream LLM call is launched).
* :func:`launch_concurrent_tasks` — starts the relevance guard, base query
  embedding and semantic rewrite tasks concurrently.
* :func:`build_query_plan` — collects rewrite/cross-lingual variants and
  embeds them.
* :func:`match_faq` — FAQ matching, ``faq_direct`` short-circuit.
* :func:`start_speculative_retrieval` — kicks off retrieval concurrently with
  the relevance-guard wait.
* :func:`relevance_guard` — awaits the guard verdict and routes rejects.
* :func:`load_generation_inputs` — profile hints + quick answers for the
  generation prompt.

LLM-backed helpers are looked up via ``backend.chat.service`` at call time so
test monkeypatches against ``backend.chat.service.<name>`` keep intercepting
them.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from time import perf_counter

from openai import APIConnectionError, APITimeoutError, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from backend.chat.events import _emit_quick_answer_lookup_event
from backend.chat.followup import build_dialog_context
from backend.chat.steps.refusal import build_reject_result
from backend.chat.types import ChatPipelineResult, PipelineRun, _empty_retrieval_context
from backend.core.config import settings
from backend.faq.faq_matcher import FAQMatchResult
from backend.guards.relevance_checker import (
    CATEGORY_SOCIAL,
    CATEGORY_SOCIAL_QUESTION,
    CATEGORY_SUPPORT_COMPLAINT,
)
from backend.models import TenantProfile
from backend.observability import record_stage_ms
from backend.search.service import detect_query_script_bucket

logger = logging.getLogger(__name__)

_PRICING_QUESTION_RE = re.compile(
    r"\b(price|pricing|plan|plans|billing|subscription|cost|trial)\b"
)
_STATUS_QUESTION_RE = re.compile(r"\b(status|incident|outage|downtime|uptime)\b")
_DOCS_QUESTION_RE = re.compile(
    r"\b(docs|documentation|guide|guides|api reference|help center|knowledge base)\b"
)

# Latin all-caps acronym tokens 2-5 chars (API, VPN, SLA, HTTP, B2B, 2FA, S3).
# Lookahead bounds the length; the trailing pattern requires at least one
# A-Z letter so plain numbers (100, 2023) don't false-positive. Presence of
# such a token suggests the query carries jargon that query-rewrite is likely
# to expand usefully - see _should_skip_query_rewrite.
_ABBR_RE = re.compile(r"\b(?=[A-Z0-9]{2,5}\b)[A-Z0-9]*[A-Z][A-Z0-9]*\b")


def _should_skip_query_rewrite(
    question: str,
    language_match: str,
    min_words: int,
    *,
    has_dialog_context: bool = False,
) -> tuple[bool, str]:
    """Decide whether ``async_semantic_query_rewrite`` can be skipped.

    Returns ``(skip, reason)``. ``reason`` is always set — it doubles as a
    log/trace marker so we can compute "% rewrite skipped" from telemetry.

    ``has_dialog_context`` forces the rewrite on any turn with prior dialog:
    the rewrite is the only stage that can resolve a conversational
    continuation ("yes, how do I check?") into a retrievable query, and no
    surface feature of the current message (length, wording) can rule a
    continuation out — so the skip optimization only applies to the first
    turn of a conversation.
    """
    if has_dialog_context:
        return False, "has_dialog_context"
    if language_match != "native":
        return False, "language_mismatch"
    if len(question.split()) < min_words:
        return False, "short_query"
    if _ABBR_RE.search(question):
        return False, "has_abbreviation"
    return True, "eligible_to_skip"


# ---------------------------------------------------------------------------
# Quick answers (structured tenant facts injected into the prompt).
# ---------------------------------------------------------------------------


def _quick_answer_quality_score(answer) -> tuple[int, int, int]:
    from datetime import datetime

    metadata = answer.metadata_json if isinstance(answer.metadata_json, dict) else {}
    method = str(metadata.get("method") or "").strip().lower()

    method_rank = {
        "mailto": 5,
        "anchor": 4,
        "script": 4,
        "regex": 3,
        "source_url": 2,
    }.get(method, 0)
    source_name = ((getattr(answer.source, "name", None) or "") if getattr(answer, "source", None) else "").lower()
    source_url = ((getattr(answer.source, "url", None) or "") if getattr(answer, "source", None) else "").lower()
    source_intent_rank = 0
    if answer.key == "documentation_url":
        if "doc" in source_name or "help" in source_name or "knowledge" in source_name:
            source_intent_rank = 2
        elif "/docs" in source_url or "docs." in source_url:
            source_intent_rank = 1

    detected_at = getattr(answer, "detected_at", None)
    detected_ts = int(detected_at.timestamp()) if isinstance(detected_at, datetime) else 0
    return (method_rank, source_intent_rank, detected_ts)


def _quick_answer_keys_for_question(
    question: str, *, support_contact_question: bool = False
) -> list[str]:
    lowered = question.casefold()
    selected: list[str] = []

    if _PRICING_QUESTION_RE.search(lowered):
        selected.extend(["pricing_url", "trial_info"])
    if _STATUS_QUESTION_RE.search(lowered):
        selected.append("status_page_url")
    # Support-contact intent is detected by the language-agnostic LLM classifier
    # (``detect_support_contact_question``), not by keyword matching — the bot is
    # language-agnostic, so a per-language keyword list would be the wrong tool.
    # ``support_chat`` is intentionally omitted: surfacing the tenant's "contact
    # us in the panel chat" line dead-ends users who are already in the chat
    # widget. The bot's own forward-to-email handoff (see the system prompt)
    # is the canonical human path instead.
    if support_contact_question:
        selected.extend(["support_email", "status_page_url"])
    if _DOCS_QUESTION_RE.search(lowered):
        selected.append("documentation_url")

    return list(dict.fromkeys(selected))


def _quick_answers_context(
    tenant_id: uuid.UUID,
    question: str,
    db: Session,
    *,
    support_contact_question: bool = False,
) -> list[str]:
    """Return only the structured quick answers relevant to this question."""
    selected_keys = _quick_answer_keys_for_question(
        question, support_contact_question=support_contact_question
    )
    if not selected_keys:
        return []
    return _lookup_quick_answers(tenant_id, selected_keys, db)


_QUICK_ANSWER_LABELS = {
    "support_email": "Support email",
    "documentation_url": "Documentation",
    "pricing_url": "Pricing",
    "trial_info": "Trial info",
    "status_page_url": "Status page",
    "support_chat": "Support chat",
}


def _format_quick_answer_lines(selected_keys: list[str], answers: list) -> list[str]:
    """Pure formatting shared by sync and async ``_lookup_quick_answers``."""
    lines_by_key: dict[str, str] = {}
    for answer in sorted(
        answers,
        key=lambda item: (item.key, tuple(-value for value in _quick_answer_quality_score(item))),
    ):
        if answer.key in lines_by_key:
            continue
        label = _QUICK_ANSWER_LABELS.get(answer.key, answer.key)
        lines_by_key[answer.key] = f"{label}: {answer.value}"
    return [lines_by_key[key] for key in selected_keys if key in lines_by_key]


def _lookup_quick_answers(
    tenant_id: uuid.UUID, selected_keys: list[str], db: Session
) -> list[str]:
    from backend.models import QuickAnswer

    answers = (
        db.query(QuickAnswer)
        .filter(QuickAnswer.tenant_id == tenant_id, QuickAnswer.key.in_(selected_keys))
        .options(selectinload(QuickAnswer.source))
        .all()
    )
    return _format_quick_answer_lines(selected_keys, list(answers))


async def _async_lookup_quick_answers(
    tenant_id: uuid.UUID, selected_keys: list[str], db: AsyncSession
) -> list[str]:
    """Async counterpart of :func:`_lookup_quick_answers`."""
    from sqlalchemy import select as _select

    from backend.models import QuickAnswer

    result = await db.execute(
        _select(QuickAnswer)
        .where(
            QuickAnswer.tenant_id == tenant_id,
            QuickAnswer.key.in_(selected_keys),
        )
        .options(selectinload(QuickAnswer.source))
    )
    answers = list(result.scalars().all())
    return _format_quick_answer_lines(selected_keys, answers)


# ---------------------------------------------------------------------------
# Step functions.
# ---------------------------------------------------------------------------


async def prepare_turn(run: PipelineRun) -> None:
    """Resolve per-turn inputs that gate the concurrent task launch.

    KB scripts, query script bucket, cross-lingual targets, the shared dialog
    tail and the query-rewrite skip decision. Releases the DB connection at
    the end so the injection guard's OpenAI call runs connectionless.
    """
    from backend.chat import service as _svc

    state = run.state
    # Pre-fetch guard profile; use preloaded value if supplied by caller.
    state.guard_profile = (
        run.guard_profile
        if run.guard_profile is not None
        else await run.db.get(TenantProfile, run.tenant_id)
    )

    state.kb_scripts = await _svc.async_detect_tenant_kb_scripts(run.tenant_id, run.db)
    state.query_script = detect_query_script_bucket(run.question)
    state.base_query_variants = list(_svc.expand_query(run.question))

    target_kb_scripts = [s for s in state.kb_scripts if s != state.query_script]
    state.cross_lingual_triggered = len(target_kb_scripts) > 0
    if not state.kb_scripts or state.query_script == "other":
        state.query_kb_language_match = "unknown"
    elif state.query_script in state.kb_scripts:
        state.query_kb_language_match = "native"
    else:
        state.query_kb_language_match = "mismatch"

    # Render the dialog tail once. Three consumers share it: the semantic
    # query rewrite (resolves continuations like "yes, how do I check?"
    # into standalone retrieval queries), the relevance guard (anaphora
    # resolution) and the consecutive-zero-hits force check in the retrieval
    # step. ``chat.messages`` holds only prior turns here — the current user
    # message is persisted after the pipeline runs.
    state.guard_dialog_context = (
        build_dialog_context(run.chat.messages) if run.chat is not None else None
    )

    # Decide whether to skip the (LLM-backed) semantic query rewrite. Cross-
    # lingual rewrites are gated separately by KB-script mismatch and stay
    # independent of this decision.
    skip_rewrite, rewrite_skip_reason = _should_skip_query_rewrite(
        run.question,
        state.query_kb_language_match,
        settings.query_rewrite_skip_min_words,
        has_dialog_context=state.guard_dialog_context is not None,
    )
    state.query_rewrite_skip_reason = rewrite_skip_reason
    logger.info(
        "query_rewrite_gated",
        extra={
            "skipped": skip_rewrite,
            "reason": rewrite_skip_reason,
            "word_count": len(run.question.split()),
            "language_match": state.query_kb_language_match,
        },
    )

    # Release the connection before any OpenAI calls. match_faq will
    # re-acquire it briefly; another close() follows before the relevance
    # guard await so that 2-10 s wait is also connectionless.
    await run.db.close()


async def injection_guard(run: PipelineRun) -> ChatPipelineResult | None:
    """2-level injection detector; short-circuits with a localized refusal.

    Runs BEFORE the concurrent task launch and gates it: on injection
    detection (~25% of chat traffic per PostHog) the relevance guard /
    embedding / semantic-rewrite tasks never launch, so we don't burn 2-5 s
    waiting for the relevance LLM to finish (``task.cancel()`` does not
    reliably interrupt an in-flight httpx call — ``asyncio.gather`` still
    waits for the socket to drain).

    Trade-off: on the 75% non-reject path we lose ~200-500 ms of I/O overlap
    between the injection level-2 embedding call and the relevance task. The
    weighted p50 effect is a net win (-575 ms expected at the current
    traffic mix).
    """
    from backend.chat import service as _svc
    from backend.guards.events import record_guard_event
    from backend.guards.types import VerdictReason

    _inj_start = perf_counter()
    injection_verdict = await _svc.async_detect_injection(
        run.question,
        tenant_id=str(run.tenant_id),
        api_key=run.api_key,
        trace=run.trace,
    )
    _inj_latency_s = perf_counter() - _inj_start

    # Preserve the historical PostHog span property shape (level 1/2, method
    # structural/semantic) derived from the unified verdict reason.
    _inj_level: int | None = None
    _inj_method: str | None = None
    if injection_verdict.reason is VerdictReason.INJECTION_STRUCTURAL:
        _inj_level, _inj_method = 1, "structural"
    elif injection_verdict.reason is VerdictReason.INJECTION_SEMANTIC:
        _inj_level, _inj_method = 2, "semantic"

    record_guard_event(
        tenant_id=run.tenant_id,
        chat_id=run.chat_id,
        kind="injection",
        verdict=injection_verdict,
        latency_ms=round(_inj_latency_s * 1000, 2),
    )
    if run.tenant_public_id is not None or run.bot_public_id is not None:
        from backend.chat.events import _emit_ai_span_event
        _inj_trace_id = (
            getattr(run.trace, "posthog_trace_id", None) if run.trace is not None else None
        )
        _emit_ai_span_event(
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            span_name="injection_guard",
            latency_s=_inj_latency_s,
            trace_id=_inj_trace_id,
            span_id=uuid.uuid4().hex if _inj_trace_id else None,
            parent_id=_inj_trace_id,
            extra_properties={
                "detected": injection_verdict.blocked,
                "level": _inj_level,
                "method": _inj_method,
            },
        )
    if injection_verdict.blocked:
        # Profile is not loaded for the reject render on this path
        # (historical behaviour: the refusal is generic, not product-branded).
        return await build_reject_result(
            run, reject_reason="injection", use_profile=False
        )
    return None


def launch_concurrent_tasks(run: PipelineRun) -> None:
    """Start the relevance guard, base embedding and rewrite tasks concurrently.

    The tasks land on :class:`PipelineState` and are awaited by later steps —
    the event loop handles all I/O overlap. The guard start is marked at task
    creation, not at the later ``await`` site, so the PostHog ``$ai_span``
    latency reflects the guard's full wall-clock (2-10 s OpenAI call) rather
    than the residual wait after FAQ/embed work already overlapped with it.
    """
    from backend.chat import service as _svc

    state = run.state

    if run.status_callback is not None:
        try:
            run.status_callback("searching")
        except Exception:
            logger.debug("status_callback(searching) failed", exc_info=True)

    state.rel_started_at = perf_counter()
    state.rel_task = asyncio.create_task(
        _svc.async_check_relevance_with_profile(
            tenant_id=run.tenant_id,
            user_question=run.question,
            profile=state.guard_profile,
            api_key=run.api_key,
            trace=run.trace,
            dialog_context=state.guard_dialog_context,
        )
    )
    state.base_embed_task = asyncio.create_task(
        _svc.async_embed_queries(
            list(state.base_query_variants),
            api_key=run.api_key,
            timeout=settings.embedding_http_timeout_seconds,
        )
    )
    skip_rewrite = state.query_rewrite_skip_reason == "eligible_to_skip"
    if not skip_rewrite:
        state.rewrite_task = asyncio.create_task(
            _svc.async_semantic_query_rewrite(
                run.question,
                api_key=run.api_key,
                timeout=settings.semantic_query_rewrite_timeout_sec,
                bot_id=run.retry_bot_id,
                dialog_context=state.guard_dialog_context,
            )
        )

    target_kb_scripts = [s for s in state.kb_scripts if s != state.query_script]
    for target_script in target_kb_scripts:
        state.cross_lingual_tasks.append(
            asyncio.create_task(
                _svc.async_semantic_query_rewrite_for_kb(
                    run.question,
                    kb_script=target_script,
                    api_key=run.api_key,
                    timeout=settings.semantic_query_rewrite_timeout_sec,
                    bot_id=run.retry_bot_id,
                )
            )
        )


async def build_query_plan(run: PipelineRun) -> None:
    """Collect rewrite/cross-lingual variants and embed the query plan."""
    from backend.chat import service as _svc

    state = run.state
    trace = run.trace

    embed_start = perf_counter()
    query_variants = list(state.base_query_variants)
    extra_variants: list[str] = []

    rewrite_collect_start = perf_counter()
    if state.rewrite_task is not None:
        try:
            state.rewritten_variant = await asyncio.wait_for(
                state.rewrite_task,
                timeout=settings.semantic_query_rewrite_timeout_sec,
            )
            if state.rewritten_variant and state.rewritten_variant.casefold() not in {
                v.casefold() for v in query_variants
            }:
                extra_variants.append(state.rewritten_variant)
        except Exception:
            state.rewritten_variant = None
    if trace is not None:
        rewrite_span = trace.span(name="query_rewrite")
        wait_ms = (
            0.0
            if state.rewrite_task is None
            else round((perf_counter() - rewrite_collect_start) * 1000, 2)
        )
        rewrite_span.end(
            output={
                "rewritten": state.rewritten_variant is not None,
                "skipped": state.rewrite_task is None,
                "skip_reason": state.query_rewrite_skip_reason,
                "variant_preview": state.rewritten_variant[:100]
                if state.rewritten_variant
                else None,
            },
            metadata={"wait_ms": wait_ms},
        )

    for cl_task in state.cross_lingual_tasks:
        try:
            cross_lingual_variant = await asyncio.wait_for(
                cl_task,
                timeout=settings.semantic_query_rewrite_timeout_sec,
            )
        except Exception:
            continue
        if cross_lingual_variant and cross_lingual_variant.casefold() not in {
            v.casefold() for v in (*query_variants, *extra_variants)
        }:
            extra_variants.append(cross_lingual_variant)
            state.cross_lingual_variants_added += 1

    embed_span = None
    if trace is not None:
        embed_span = trace.span(
            name="query-embedding",
            input={
                "query_variants": [*query_variants, *extra_variants],
                "query_variant_count": len(query_variants) + len(extra_variants),
                "variant_mode": "multi"
                if (len(query_variants) + len(extra_variants)) > 1
                else "single",
                "upstream_precomputed": True,
            },
        )

    # Track embedding-API wall-clock separately from ``embed_ms`` (which
    # also includes the upstream rewrite/cross-lingual rewrite wait). The
    # ``$ai_embedding`` event reports only this narrower window so PostHog
    # latency dashboards reflect the embedding service, not rewrite delay.
    _embed_api_start = perf_counter()
    try:
        assert state.base_embed_task is not None  # created by launch_concurrent_tasks
        base_variant_vectors = await asyncio.wait_for(
            state.base_embed_task,
            timeout=settings.embedding_http_timeout_seconds + 1.0,
        )
    except (APITimeoutError, APIConnectionError, RateLimitError, TimeoutError):
        logger.warning("async_run_chat_pipeline_embed_queries_failed", exc_info=True)
        base_variant_vectors = []

    state.embed_api_request_count = 1 if base_variant_vectors else 0
    extra_variant_vectors: list[list[float]] = []
    if extra_variants and base_variant_vectors:
        try:
            extra_variant_vectors = await _svc.async_embed_queries(
                extra_variants,
                api_key=run.api_key,
                timeout=settings.embedding_http_timeout_seconds,
            )
            state.embed_api_request_count += 1
        except (APITimeoutError, APIConnectionError, RateLimitError):
            logger.warning("async_run_chat_pipeline_embed_extras_failed", exc_info=True)
            extra_variant_vectors = []
    _embed_api_latency_s = perf_counter() - _embed_api_start

    if extra_variant_vectors and len(extra_variant_vectors) == len(extra_variants):
        state.query_variants = [*query_variants, *extra_variants]
        state.variant_vectors = [*base_variant_vectors, *extra_variant_vectors]
    else:
        state.query_variants = query_variants
        state.variant_vectors = base_variant_vectors

    embed_ms = round((perf_counter() - embed_start) * 1000, 2)
    if embed_span is not None:
        embed_span.end(
            output={
                "embedded_query_count": len(state.variant_vectors),
                "extra_embedded_queries": max(len(state.variant_vectors) - 1, 0),
                "embedding_api_request_count": state.embed_api_request_count,
                "extra_embedding_api_requests": max(state.embed_api_request_count - 1, 0),
                "duration_ms": embed_ms,
                "upstream_precomputed": True,
            }
        )
    if trace is not None:
        record_stage_ms(trace, "embed_ms", embed_ms)
    if (
        (run.tenant_public_id is not None or run.bot_public_id is not None)
        and state.embed_api_request_count > 0
    ):
        # Char-based token estimate — embedding API does not surface usage
        # through ``async_embed_queries``, and PostHog only needs an
        # order-of-magnitude figure for latency-vs-volume dashboards. Avoids
        # importing tiktoken on the hot path.
        _embedded_variants = [*query_variants, *extra_variants]
        _input_chars = sum(len(v) for v in _embedded_variants)
        _input_tokens_est = max(_input_chars // 4, 1)
        from backend.chat.events import _emit_ai_embedding_event
        _emit_ai_embedding_event(
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            model=settings.embedding_model,
            input_tokens=_input_tokens_est,
            latency_s=_embed_api_latency_s,
            operation="chat/embed",
            trace_id=getattr(embed_span, "posthog_trace_id", None),
            span_id=getattr(embed_span, "posthog_span_id", None),
            parent_id=getattr(embed_span, "posthog_parent_id", None),
            input_count=len(_embedded_variants),
        )


async def match_faq(run: PipelineRun) -> ChatPipelineResult | None:
    """FAQ matching; short-circuits the pipeline on a ``faq_direct`` hit."""
    from backend.chat import service as _svc
    from backend.chat.language import render_direct_faq_answer_result

    state = run.state
    trace = run.trace
    base_question_embedding = state.variant_vectors[0] if state.variant_vectors else []

    faq_start = perf_counter()
    try:
        state.faq_match = await _svc.async_match_faq(
            tenant_id=run.tenant_id,
            question=run.question,
            question_embedding=base_question_embedding,
            db=run.db,
        )
    except Exception:
        state.faq_match = FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="faq_match_error_degraded_to_rag_only",
        )

    faq_ms = round((perf_counter() - faq_start) * 1000, 2)
    if trace is not None:
        faq_span = trace.span(name="faq_match", input={"question_preview": run.question[:80]})
        retrieval_skipped = state.faq_match.strategy == "faq_direct"
        faq_span.end(
            metadata={
                "tenant_id": str(run.tenant_id),
                "strategy": state.faq_match.strategy,
                "top_score": state.faq_match.top_score,
                "selected_score": state.faq_match.selected_score,
                "faq_ids": [str(item.id) for item in state.faq_match.faq_items],
                "selected_faq_id": state.faq_match.selected_faq_id,
                "direct_guard_used": state.faq_match.direct_guard_used,
                "direct_guard_passed": state.faq_match.direct_guard_passed,
                "decision_reason": state.faq_match.decision_reason,
                "retrieval_skipped": retrieval_skipped,
                "generation_skipped": retrieval_skipped,
                "duration_ms": faq_ms,
            },
        )
        record_stage_ms(trace, "faq_match_ms", faq_ms)

    if state.faq_match.strategy == "faq_direct":
        if state.rel_task is not None and not state.rel_task.done():
            state.rel_task.cancel()
            await asyncio.gather(state.rel_task, return_exceptions=True)
        direct_answer_result = await render_direct_faq_answer_result(
            answer_text=state.faq_match.faq_items[0].answer
            if state.faq_match.faq_items
            else "",
            response_language=run.language_context.response_language,
            api_key=run.api_key,
        )
        return ChatPipelineResult(
            raw_answer=direct_answer_result.text,
            final_answer=direct_answer_result.text,
            tokens_used=direct_answer_result.tokens_used,
            strategy="faq_direct",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=True,
            retrieval=None,
            escalation_recommended=False,
            escalation_trigger=None,
            faq_match=state.faq_match,
            language_context=run.language_context,
        )
    return None


def start_speculative_retrieval(run: PipelineRun) -> None:
    """Start retrieval concurrently with the relevance-guard wait.

    Most turns pass the guard, so overlapping BM25+vector search with the
    2-10 s guard call saves ~150-500 ms on p50. On guard reject the task is
    cancelled and its result discarded (see the retrieval step for why this
    leaves no DB artifacts and no dangling connections). Retrieval always
    runs on the raw question plus the precomputed variants — the dialog-aware
    rewrite variant (already embedded) carries the continuation context, so
    no query replacement is needed.
    """
    from backend.chat.steps.retrieval import _speculative_retrieval

    state = run.state
    state.retrieve_kwargs = dict(
        precomputed_query_variants=state.query_variants,
        precomputed_variant_vectors=state.variant_vectors,
        precomputed_embedding_api_request_count=state.embed_api_request_count,
        rewritten_variant=state.rewritten_variant,
    )
    if state.variant_vectors:
        state.spec_retrieval_task = asyncio.create_task(_speculative_retrieval(run))


async def relevance_guard(run: PipelineRun) -> ChatPipelineResult | None:
    """Await the relevance-guard verdict and route rejects.

    A support complaint routes to the pre-confirm escalation handoff; social
    turns get a polite reply; everything else off-topic gets the standard
    NOT_RELEVANT reject.
    """
    from backend.chat.steps.retrieval import cancel_speculative_retrieval

    state = run.state

    # match_faq re-acquired a connection; release it before awaiting the
    # relevance guard (an OpenAI call, 2-10 s).
    await run.db.close()
    assert state.rel_task is not None  # created by launch_concurrent_tasks
    from backend.guards.events import record_guard_event
    from backend.guards.types import Verdict, VerdictReason

    try:
        rel_verdict = await state.rel_task
    except asyncio.CancelledError:
        rel_verdict = Verdict.of(VerdictReason.CANCELLED)
    relevant = not rel_verdict.blocked
    guard_reason = rel_verdict.reason.value
    # The guard no longer echoes the profile back. Reconstruct the historical
    # ``state.profile`` assignment: it is cleared to None only on fail-open
    # paths that rendered no real judgment (no_profile / circuit_open / timeout
    # / error); every other verdict keeps the profile the guard was given.
    state.profile = (
        None
        if rel_verdict.reason
        in (
            VerdictReason.NO_PROFILE,
            VerdictReason.CIRCUIT_OPEN,
            VerdictReason.TIMEOUT,
            VerdictReason.ERROR,
        )
        else state.guard_profile
    )
    state.guard_bypassed_short_query = guard_reason == "short_query_bypass"
    _rel_latency_s = perf_counter() - state.rel_started_at
    record_guard_event(
        tenant_id=run.tenant_id,
        chat_id=run.chat_id,
        kind="relevance",
        verdict=rel_verdict,
        latency_ms=round(_rel_latency_s * 1000, 2),
    )
    if run.tenant_public_id is not None or run.bot_public_id is not None:
        from backend.chat.events import _emit_ai_span_event
        _rel_trace_id = (
            getattr(run.trace, "posthog_trace_id", None) if run.trace is not None else None
        )
        _emit_ai_span_event(
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            span_name="relevance_guard",
            latency_s=_rel_latency_s,
            trace_id=_rel_trace_id,
            span_id=uuid.uuid4().hex if _rel_trace_id else None,
            parent_id=_rel_trace_id,
            extra_properties={"blocked": not relevant},
        )

    if not relevant:
        await cancel_speculative_retrieval(run)

        # A complaint about support being unresponsive must never dead-end
        # in a refusal — offer the escalation handoff instead. The empty
        # retrieval context routes _handle_sync through the same
        # pre-confirm arming path as the consecutive-zero-hits escalation.
        if guard_reason == CATEGORY_SUPPORT_COMPLAINT:
            from backend.chat.steps.refusal import build_pre_confirm_escalation_result
            from backend.escalation.openai_escalation import pre_confirm_fallback_result
            from backend.models import EscalationTrigger

            fallback = pre_confirm_fallback_result("support_complaint")
            return build_pre_confirm_escalation_result(
                run,
                message_to_user=fallback.message_to_user,
                tokens_used=fallback.tokens_used,
                trigger=EscalationTrigger.user_complaint,
                retrieval=_empty_retrieval_context(),
            )

        # Social turns that slipped past the greeting handler get a polite
        # reply, not a refusal: a pure thanks/farewell gets a closing
        # acknowledgement, and a question about the bot itself ("do you
        # speak English?") gets a short friendly invite.
        if guard_reason == CATEGORY_SOCIAL:
            reject_reason = "social"
        elif guard_reason == CATEGORY_SOCIAL_QUESTION:
            reject_reason = "social_question"
        else:
            reject_reason = "not_relevant"
        return await build_reject_result(run, reject_reason=reject_reason)

    return None


async def load_generation_inputs(run: PipelineRun) -> None:
    """Profile-derived prompt hints, FAQ context and quick answers."""
    state = run.state

    state.client_product_name = state.profile.product_name if state.profile else None
    if state.profile and isinstance(state.profile.topics, list) and state.profile.topics:
        state.topic_hint = ", ".join(
            [str(m) for m in state.profile.topics[:3] if str(m).strip()]
        )

    state.faq_context_items = (
        state.faq_match.faq_items
        if state.faq_match is not None and state.faq_match.strategy == "faq_context"
        else None
    )
    selected_quick_answer_keys = _quick_answer_keys_for_question(
        run.question, support_contact_question=run.support_contact_question
    )
    if selected_quick_answer_keys:
        # Looked up via the service module so test monkeypatches on
        # ``backend.chat.service._async_lookup_quick_answers`` intercept it.
        from backend.chat import service as _svc

        state.quick_answer_items = await _svc._async_lookup_quick_answers(
            run.tenant_id, selected_quick_answer_keys, run.db
        )
        _emit_quick_answer_lookup_event(
            selected_keys=selected_quick_answer_keys,
            matched_count=len(state.quick_answer_items),
            text_length=len(run.question),
            tenant_public_id=run.tenant_public_id,
            bot_public_id=run.bot_public_id,
            chat_id=run.chat_id,
        )
    else:
        state.quick_answer_items = []
    state.strategy = "faq_context" if state.faq_context_items else "rag_only"
