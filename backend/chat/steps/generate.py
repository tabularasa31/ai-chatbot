"""Generation pipeline step.

Owns the LLM answer call (:func:`async_generate_answer` /
:func:`_async_generate_answer_native`), the language-mismatch retry and the
final :class:`ChatPipelineResult` assembly (:func:`run_generation`).

Test seam note: the pipeline resolves ``async_generate_answer``,
``detect_language`` and ``translate_text_result`` through
``backend.chat.handlers.rag`` module globals at call time, so existing
``monkeypatch.setattr("backend.chat.handlers.rag.<name>", ...)`` keeps
intercepting them.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any

from backend.chat.language import (
    _language_root,
    async_localize_text_to_language_result,
    log_llm_tokens,
)
from backend.chat.prompts import build_rag_messages
from backend.chat.streaming import (
    LanguageGateStreamFilter,
    LanguageMismatchStreamAbortError,
    OfferMarkerStreamFilter,
    ThoughtStreamFilter,
    _scrub_offer_marker_literal,
    _strip_and_detect_offer_marker,
    _strip_inline_citations,
    _strip_thought_tags,
    _thought_truncated,
)
from backend.chat.types import ChatPipelineResult, PipelineRun
from backend.core.config import settings
from backend.core.openai_client import is_reasoning_model
from backend.core.openai_retry import async_call_openai_with_retry
from backend.faq.faq_matcher import FAQRow
from backend.models import Chat, MessageRole
from backend.observability import TraceHandle, record_stage_ms
from backend.observability.formatters import truncate_text

logger = logging.getLogger(__name__)


def _rag_module():
    """The ``backend.chat.handlers.rag`` module, resolved lazily.

    ``rag`` is the documented monkeypatch surface for the generation seams
    (``async_generate_answer``, ``detect_language``, ``translate_text_result``);
    resolving it at call time keeps ``monkeypatch.setattr`` on that module
    effective for the call sites that now live here.
    """
    from backend.chat.handlers import rag

    return rag


def _safe_int(value: Any) -> int:
    """Convert SDK usage fields to int without trusting mock-like objects."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _assemble_chat_messages(
    *,
    system_prompt: str,
    user_message: str,
    prior_messages: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Build the OpenAI ``messages`` list: system → optional history → user.

    The history is sandwiched so the model interprets the latest user turn
    in the context of its prior follow-up questions instead of in isolation.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt}
    ]
    if prior_messages:
        messages.extend(prior_messages)
    messages.append({"role": "user", "content": user_message})
    return messages


def _prompt_cache_kwargs(*candidate_ids: str | None) -> dict[str, Any]:
    """Return ``{"extra_body": {"prompt_cache_key": <bot id>}}`` for the request, or ``{}``.

    OpenAI routes requests sharing a ``prompt_cache_key`` to the same cache node,
    materially raising the prefix cache-hit rate once the prefix clears the
    1024-token floor. We key on a stable per-bot id so every turn of a bot lands
    on the same node; turns from different bots never contend. The key is opaque
    to OpenAI (no PII) — a public bot/tenant id is fine. Omitted when no id is
    available (e.g. in unit tests) so the request shape is unchanged there.

    The field is passed via ``extra_body`` rather than as a named kwarg so it is
    forwarded in the request body on every SDK version allowed by
    ``requirements.txt`` (``openai>=1.70.0``); older 1.x releases predate the
    typed ``prompt_cache_key`` parameter and would raise ``TypeError`` if it were
    spread as a direct keyword argument.
    """
    for candidate in candidate_ids:
        if candidate:
            return {"extra_body": {"prompt_cache_key": candidate}}
    return {}


def _generation_sampling_kwargs(reasoning: bool) -> dict[str, Any]:
    """Sampling parameters for the main chat generation call (telemetry shape).

    Non-reasoning models keep the historical fixed ``temperature=0.2``.
    Reasoning models get ``reasoning_effort`` (the OpenAI default of "medium"
    dominated generation latency for gpt-5-mini) and, for the gpt-5 family
    only, ``verbosity`` — other reasoning models reject the parameter.

    This flat dict is what trace metadata records; the actual request kwargs
    are assembled by :func:`_generation_request_kwargs`, which relocates
    ``verbosity`` into ``extra_body`` for SDK compatibility.
    """
    if not reasoning:
        return {"temperature": 0.2}
    kwargs: dict[str, Any] = {"reasoning_effort": settings.chat_reasoning_effort}
    if settings.chat_model.lower().startswith("gpt-5"):
        kwargs["verbosity"] = settings.chat_verbosity
    return kwargs


def _generation_request_kwargs(
    sampling_kwargs: dict[str, Any], cache_kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Merge sampling and prompt-cache kwargs into ``create()`` keyword args.

    ``verbosity`` rides in ``extra_body`` alongside ``prompt_cache_key``:
    older SDK releases allowed by ``requirements.txt`` (``openai>=1.70.0``)
    predate the typed parameter and would raise ``TypeError`` if it were
    spread as a direct keyword argument. ``reasoning_effort`` and
    ``temperature`` have been typed parameters since well before 1.70, so
    they stay top-level.
    """
    kwargs = dict(sampling_kwargs)
    extra_body = dict(cache_kwargs.get("extra_body") or {})
    verbosity = kwargs.pop("verbosity", None)
    if verbosity is not None:
        extra_body["verbosity"] = verbosity
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def _estimate_prompt_tokens(text: str) -> int:
    """Cheap local estimate used only for cache-readiness telemetry."""
    stripped = (text or "").strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def _prompt_prefix_fingerprint(system_prompt: str) -> str:
    """Short stable fingerprint of the system prompt for cache telemetry.

    OpenAI prompt caching requires the prefix to be byte-identical across
    requests; this fingerprint makes any prefix drift directly observable by
    comparing the value across adjacent turns in Langfuse / PostHog instead of
    inferring stability from the token-count estimate. 16 hex chars of SHA-256
    is plenty for equality checks and leaks nothing about the prompt content.
    """
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]


def _usage_cached_tokens(usage: Any) -> int:
    """Extract OpenAI prompt cache hit tokens from SDK objects or dicts."""
    if usage is None:
        return 0
    details: Any
    if isinstance(usage, dict):
        details = usage.get("prompt_tokens_details")
    else:
        details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    if isinstance(details, dict):
        return _safe_int(details.get("cached_tokens"))
    return _safe_int(getattr(details, "cached_tokens", 0))


def _build_prior_messages_for_llm(
    chat: Chat | None,
    *,
    max_messages: int,
    char_cap: int,
) -> list[dict[str, str]] | None:
    """Take the trailing N persisted turns of ``chat`` and format them for the
    OpenAI chat API. Returns None when there is nothing to add.

    The current user turn is NOT yet persisted at this point in the pipeline
    (async_run_chat_pipeline runs before _persist_turn_with_response_language), so
    the full chat.messages list is "prior" context.
    """
    if chat is None or max_messages <= 0:
        return None
    persisted = sorted(chat.messages or [], key=lambda m: m.created_at or m.id)
    out: list[dict[str, str]] = []
    for m in persisted[-max_messages:]:
        text = (m.content or "").strip()
        if not text:
            continue
        if len(text) > char_cap:
            text = text[:char_cap].rstrip() + "…"
        role = "user" if m.role == MessageRole.user else "assistant"
        out.append({"role": role, "content": text})
    return out or None


async def _enforce_response_language(
    answer_text: str,
    *,
    response_language: str,
    api_key: str | None,
) -> tuple[str, int]:
    """Translate the answer to ``response_language`` if it drifted to another language.

    Safety net for the case where the LLM follows the context language instead
    of the language directive (notably when retrieved chunks are in a different
    language than the user's question — see PR #513 cross-lingual retrieval).
    Returns ``(text, extra_tokens)`` where ``extra_tokens`` is the cost of the
    translation call (0 when no translation was needed). Returns the original
    text unchanged if detection is unreliable, the answer is empty, the api_key
    is missing, or translation fails.

    NOTE: only safe to call after non-streamed generation. In streaming flows,
    chunks have already been emitted to the client via ``stream_callback`` and
    rewriting the final text would cause a UI ↔ persisted-history mismatch.
    Caller is responsible for skipping this guard when streaming.
    """
    from backend.chat.language import LangDetectError

    _rag = _rag_module()
    stripped = (answer_text or "").strip()
    if not stripped or not api_key:
        return answer_text, 0
    try:
        detection = _rag.detect_language(stripped)
    except LangDetectError:
        return answer_text, 0
    if not detection.is_reliable or detection.detected_language == "unknown":
        return answer_text, 0
    if _language_root(detection.detected_language) == _language_root(response_language):
        return answer_text, 0
    try:
        result = await _rag.translate_text_result(
            source_text=answer_text,
            target_language=response_language,
            api_key=api_key,
        )
    except Exception as exc:  # pragma: no cover - defensive; helper already swallows internally
        logger.warning("post-gen language guard translation failed: %s", exc)
        return answer_text, 0
    translated = result.text or answer_text
    return translated, int(result.tokens_used or 0)


async def _async_generate_answer_native(
    question: str,
    context_chunks: list[str],
    *,
    api_key: str,
    response_language: str = "en",
    user_context_line: str | None = None,
    disclosure_config: dict[str, Any] | None = None,
    client_product_name: str | None = None,
    topic_hint: str | None = None,
    faq_context_items: list[FAQRow] | None = None,
    quick_answer_items: list[str] | None = None,
    agent_instructions: str | None = None,
    low_context: bool = False,
    allow_clarification: bool = True,
    trace: TraceHandle | None = None,
    retry_bot_id: str | None = None,
    stream_callback: Callable[[str], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    metrics_tenant_id: str | None = None,
    metrics_bot_id: str | None = None,
    prior_messages: list[dict[str, str]] | None = None,
) -> tuple[str, int, int, int, bool]:
    """Native async LLM answer generation.

    Uses ``AsyncOpenAI`` + ``async_call_openai_with_retry``. The stream loop is
    ``async for`` so back-pressure on slow OpenAI tokens does not occupy a
    default-executor thread for the full duration of the call.
    ``stream_callback`` is kept sync — it's a thin synchronous push to the
    queue backing the SSE response.
    """
    from backend.chat import service as _svc

    if not context_chunks and not faq_context_items and not quick_answer_items:
        result = await async_localize_text_to_language_result(
            canonical_text="I don't have information about this.",
            target_language=response_language,
            api_key=api_key,
        )
        return (result.text, 0, 0, 0, False)

    system_prompt, user_message = build_rag_messages(
        question,
        context_chunks,
        response_language=response_language,
        user_context_line=user_context_line,
        disclosure_config=disclosure_config,
        client_product_name=client_product_name,
        topic_hint=topic_hint,
        faq_context_items=faq_context_items,
        quick_answer_items=quick_answer_items,
        agent_instructions=agent_instructions,
        low_context=low_context,
        allow_clarification=allow_clarification,
    )
    messages = _assemble_chat_messages(
        system_prompt=system_prompt,
        user_message=user_message,
        prior_messages=prior_messages,
    )
    prompt_cache_prefix_tokens_estimate = _estimate_prompt_tokens(system_prompt)
    prompt_cache_prefix_fingerprint = _prompt_prefix_fingerprint(system_prompt)
    _cache_kwargs = _prompt_cache_kwargs(metrics_bot_id, retry_bot_id)
    openai_client = _svc.get_async_openai_client(api_key)
    _reasoning = is_reasoning_model(settings.chat_model)
    _sampling_kwargs = _generation_sampling_kwargs(_reasoning)
    _request_kwargs = _generation_request_kwargs(_sampling_kwargs, _cache_kwargs)
    _max_completion_tokens = (
        settings.chat_response_max_tokens_reasoning
        if _reasoning
        else settings.chat_response_max_tokens
    )
    generation = None
    if trace is not None:
        if settings.observability_capture_full_prompts:
            generation_input: Any = messages
        else:
            generation_input = {
                "question_preview": truncate_text(question),
                "context_chunk_count": len(context_chunks),
                "quick_answer_count": len(quick_answer_items or []),
                "prompt_cache_prefix_tokens_estimate": prompt_cache_prefix_tokens_estimate,
            }
        generation = trace.generation(
            name="llm-generation",
            model=settings.chat_model,
            input=generation_input,
            metadata={
                **_sampling_kwargs,
                "max_completion_tokens": _max_completion_tokens,
                "response_language": response_language,
                "context_chunk_count": len(context_chunks),
                "quick_answer_count": len(quick_answer_items or []),
                "prompt_cache_prefix_tokens_estimate": prompt_cache_prefix_tokens_estimate,
                "prompt_cache_prefix_meets_minimum": prompt_cache_prefix_tokens_estimate >= 1024,
                "prompt_cache_prefix_fingerprint": prompt_cache_prefix_fingerprint,
                "captures_full_prompt": settings.observability_capture_full_prompts,
                "finish_reason_expected": "stop_or_length",
                "system_prompt": (
                    system_prompt if settings.observability_capture_full_prompts else None
                ),
                "context_chunks": (
                    context_chunks if settings.observability_capture_full_prompts else None
                ),
            },
        )
    started_at = perf_counter()
    try:
        prompt_tokens_raw = 0
        completion_tokens_raw = 0
        cached_tokens_raw = 0
        finish_reason: str | None = None
        actual_model: str = settings.chat_model
        _was_thought_truncated: bool = False
        if stream_callback is not None:
            stream = await async_call_openai_with_retry(
                "chat_generate_stream",
                lambda: openai_client.chat.completions.create(
                    model=settings.chat_model,
                    messages=messages,
                    **_request_kwargs,
                    max_completion_tokens=_max_completion_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                ),
                bot_id=retry_bot_id,
                emit_chat_failed=True,
                langfuse_observation=generation,
            )
            chunks: list[str] = []
            total_tokens = 0
            _offer_filter = OfferMarkerStreamFilter(stream_callback)
            _filter = ThoughtStreamFilter(_offer_filter.feed, on_phase_change=status_callback)
            try:
                async for chunk in stream:
                    if isinstance(getattr(chunk, "model", None), str):
                        actual_model = chunk.model
                    if getattr(chunk, "usage", None):
                        total_tokens = chunk.usage.total_tokens or 0
                        prompt_tokens_raw = getattr(chunk.usage, "prompt_tokens", 0) or 0
                        completion_tokens_raw = getattr(chunk.usage, "completion_tokens", 0) or 0
                        cached_tokens_raw = _usage_cached_tokens(chunk.usage)
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    delta = getattr(choice.delta, "content", None) if choice.delta else None
                    if delta:
                        chunks.append(delta)
                        _filter.feed(delta)
                _filter.flush_end()
                _offer_filter.flush_end()
            except LanguageMismatchStreamAbortError:
                # The language gate rejected the streamed head before anything
                # reached the client. Stop consuming OpenAI tokens immediately;
                # the caller regenerates once with the expected language forced.
                try:
                    await stream.close()
                except Exception:
                    logger.debug("stream close after language abort failed", exc_info=True)
                raise
            _raw_answer = "".join(chunks)
            _was_thought_truncated = _thought_truncated(_raw_answer)
            answer_text = _strip_thought_tags(_raw_answer)
        else:
            response = await async_call_openai_with_retry(
                "chat_generate",
                lambda: openai_client.chat.completions.create(
                    model=settings.chat_model,
                    messages=messages,
                    **_request_kwargs,
                    max_completion_tokens=_max_completion_tokens,
                ),
                bot_id=retry_bot_id,
                emit_chat_failed=True,
                langfuse_observation=generation,
            )
            actual_model = response.model if isinstance(getattr(response, "model", None), str) else settings.chat_model
            _raw_content = response.choices[0].message.content or ""
            _was_thought_truncated = _thought_truncated(_raw_content)
            answer_text = _strip_thought_tags(_raw_content)
            total_tokens = response.usage.total_tokens if response.usage else 0
            if response.usage:
                prompt_tokens_raw = getattr(response.usage, "prompt_tokens", 0) or 0
                completion_tokens_raw = getattr(response.usage, "completion_tokens", 0) or 0
                cached_tokens_raw = _usage_cached_tokens(response.usage)
            if response.choices:
                finish_reason = getattr(response.choices[0], "finish_reason", None)
        # Language-agnostic ticket-offer signal: the LLM appends
        # OFFER_MARKER when (and only when) it ends its reply with a
        # support-ticket offer. Detect once on the post-thought-strip text;
        # the boolean is surfaced through the return tuple so
        # _handle_sync can arm escalation_pre_confirm_pending without
        # natural-language pattern matching. The follow-up scrub removes
        # any mid-text occurrence (against prompt contract) so the literal
        # never reaches the UI even when detection itself stayed False.
        answer_text, offered_ticket = _strip_and_detect_offer_marker(answer_text)
        answer_text = _scrub_offer_marker_literal(answer_text)
        log_llm_tokens(
            operation="generate",
            target_language=response_language,
            tokens=total_tokens,
            model=actual_model,
        )
        _input_tokens = _safe_int(prompt_tokens_raw)
        _output_tokens = _safe_int(completion_tokens_raw)
        _cached_tokens = _safe_int(cached_tokens_raw)
        _cost_usd = settings.compute_cost_usd(actual_model, _input_tokens, _output_tokens)
        _duration_s = perf_counter() - started_at
        _gen_duration_ms = round(_duration_s * 1000, 2)
        if generation is not None:
            _cost_rates = settings.openai_model_costs.get(
                actual_model,
                {
                    "input": settings.openai_default_cost_per_1m_input_tokens,
                    "output": settings.openai_default_cost_per_1m_output_tokens,
                },
            )
            generation.end(
                output=answer_text.strip(),
                usage={
                    "input": _input_tokens,
                    "output": _output_tokens,
                },
                metadata={
                    "total_tokens": _safe_int(total_tokens),
                    "finish_reason": finish_reason,
                    "thought_truncated": _was_thought_truncated,
                    "cost_usd": _cost_usd,
                    "cost_rate_usd_per_1m": _cost_rates,
                    "duration_ms": _gen_duration_ms,
                    "prompt_cache_cached_tokens": _cached_tokens,
                    "prompt_cache_hit": _cached_tokens > 0,
                },
            )
        if trace is not None:
            record_stage_ms(trace, "llm_generate_ms", _gen_duration_ms)
        if metrics_tenant_id is not None or metrics_bot_id is not None:
            from backend.chat.events import _emit_ai_generation_event
            _emit_ai_generation_event(
                tenant_public_id=metrics_tenant_id,
                bot_public_id=metrics_bot_id,
                model=actual_model,
                input_tokens=_input_tokens,
                output_tokens=_output_tokens,
                cached_tokens=_cached_tokens,
                prompt_cache_prefix_tokens_estimate=prompt_cache_prefix_tokens_estimate,
                prompt_cache_prefix_fingerprint=prompt_cache_prefix_fingerprint,
                cost_usd=_cost_usd,
                latency_s=_duration_s,
                operation="chat/generate",
                trace_id=getattr(generation, "posthog_trace_id", None),
                span_id=getattr(generation, "posthog_span_id", None),
                parent_id=getattr(generation, "posthog_parent_id", None),
            )
        # Post-gen language guard runs only on non-streamed generation. In
        # the streaming path the answer was already emitted to the client
        # chunk-by-chunk via ``stream_callback``; rewriting the final text
        # here would produce a UI/history mismatch.
        # Resolved via the rag module: tests monkeypatch
        # ``backend.chat.handlers.rag._enforce_response_language``.
        if stream_callback is None:
            final_text, extra_tokens = await _rag_module()._enforce_response_language(
                answer_text.strip(),
                response_language=response_language,
                api_key=api_key,
            )
            total_tokens = (total_tokens or 0) + extra_tokens
            _output_tokens += extra_tokens
        else:
            final_text = answer_text.strip()
        return (final_text, total_tokens, _input_tokens, _output_tokens, offered_ticket)
    except LanguageMismatchStreamAbortError as abort_exc:
        # Not an error: the language gate aborted the stream early so the
        # caller can regenerate in the expected language. End the observation
        # cleanly with the abort reason instead of an ERROR status.
        if generation is not None:
            generation.end(
                metadata={
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                    "aborted_reason": "language_mismatch",
                    "detected_language": abort_exc.detected_language,
                },
            )
        raise
    except Exception as exc:
        log_llm_tokens(
            operation="generate",
            target_language=response_language,
            tokens=0,
            model=settings.chat_model,
        )
        if generation is not None:
            generation.end(
                metadata={
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                },
                level="ERROR",
                status_message=str(exc),
            )
        raise


async def async_generate_answer(
    question: str,
    context_chunks: list[str],
    **kwargs: Any,
) -> tuple[str, int, int, int, bool]:
    """Generation entry point and the test seam for the LLM hop.

    Kept as a thin wrapper (rather than exposing the native function
    directly) so tests can monkeypatch
    ``backend.chat.handlers.rag.async_generate_answer`` with an async fake;
    the pipeline resolves that name from the rag module at call time.
    """
    return await _async_generate_answer_native(question, context_chunks, **kwargs)


async def run_generation(run: PipelineRun) -> ChatPipelineResult:
    """LLM answer (+ language-mismatch retry), validate, escalation decision."""
    from backend.chat import service as _svc

    _rag = _rag_module()
    state = run.state
    trace = run.trace
    language_context = run.language_context
    retrieval = state.retrieval
    assert retrieval is not None  # invariant: set by run_retrieval

    prior_messages = _build_prior_messages_for_llm(
        run.chat,
        max_messages=settings.chat_history_turns,
        char_cap=settings.chat_history_message_char_cap,
    )

    if run.status_callback is not None:
        try:
            run.status_callback("writing")
        except Exception:
            logger.debug("status_callback(writing) failed", exc_info=True)

    llm_start = perf_counter()

    # --- Expected output language (resolved BEFORE generation) ---
    # Use the pre-resolved response_language as the expected output language for confirmed
    # non-English conversations. For "en" (often a resolution fallback, not a confirmed
    # detection) and unset/auto cases, fall back to detect_language(question) to preserve
    # the original mismatch-detection behaviour.
    # Motivation: detect_language on short clarifying questions (the clarify branch) is
    # unreliable when the text mixes product terms from the English KB with the user's
    # language, causing false-positive retries. language_context.response_language is
    # computed from conversation history + user locale + KB language and is a better signal
    # for confirmed non-English sessions.
    _expected_lang: str | None = language_context.response_language if language_context else None
    # "en" may be a default fallback rather than a confirmed detection, so re-verify via
    # the question itself. For any other confirmed non-English language, trust the
    # pre-resolved value and skip the extra detect_language call on the question.
    if not _expected_lang or _expected_lang in ("auto", "en"):
        _q_lang = _rag.detect_language(run.question)
        _expected_lang = (
            _q_lang.detected_language
            if _q_lang.is_reliable and _q_lang.detected_language not in ("unknown", "en")
            else None
        )

    # Streaming: verify the language on the buffered head of the stream
    # BEFORE any text reaches the client. On a reliable mismatch the gate
    # aborts the stream (nothing shown yet) and we regenerate once with the
    # expected language forced, streaming the retry to the client. This
    # replaces the old post-hoc check that regenerated AFTER the user had
    # already watched the wrong-language answer stream in, then silently
    # swapped the persisted text.
    _gate: LanguageGateStreamFilter | None = None
    _gated_stream_callback = run.stream_callback
    if run.stream_callback is not None and _expected_lang and _expected_lang != "en":
        _gate = LanguageGateStreamFilter(
            run.stream_callback, expected_language=_expected_lang
        )
        _gated_stream_callback = _gate.feed

    _generate_kwargs: dict[str, Any] = dict(
        api_key=run.api_key,
        user_context_line=run.user_context_line,
        disclosure_config=run.disclosure_config,
        client_product_name=state.client_product_name,
        topic_hint=state.topic_hint,
        faq_context_items=state.faq_context_items,
        quick_answer_items=state.quick_answer_items,
        agent_instructions=run.agent_instructions,
        low_context=not state.reranker_rescued and retrieval.reliability.score == "low",
        allow_clarification=run.allow_clarification,
        trace=trace,
        retry_bot_id=run.retry_bot_id,
        status_callback=run.status_callback,
        metrics_tenant_id=run.tenant_public_id,
        metrics_bot_id=run.bot_public_id,
        prior_messages=prior_messages,
    )

    _lang_retry_ms = 0   # set below if the language-mismatch retry fires
    raw_answer = ""
    tokens_used = 0
    _input_toks = 0
    _output_toks = 0
    llm_offered_ticket = False
    try:
        raw_answer, tokens_used, _input_toks, _output_toks, llm_offered_ticket = await _rag.async_generate_answer(
            run.question,
            retrieval.chunk_texts,
            response_language=language_context.response_language,
            stream_callback=_gated_stream_callback,
            **_generate_kwargs,
        )
        if _gate is not None:
            # Answers shorter than the gate threshold are checked here;
            # a reliable mismatch still aborts (nothing was emitted yet).
            _gate.flush_end()
    except LanguageMismatchStreamAbortError as _abort:
        # The aborted attempt's tokens are not returned by the closed
        # stream, so they are absent from the per-turn usage totals; the
        # abort fires within the first ~80 visible chars, so the loss is
        # bounded and rare.
        _lang_retry_start = perf_counter()
        lang_span = None
        if trace is not None:
            lang_span = trace.span(
                name="language-check",
                input={
                    "expected_lang": _expected_lang,
                    "answer_lang": _abort.detected_language,
                    "stream_aborted_early": True,
                },
            )
        retry_answer, retry_tokens, retry_in, retry_out, retry_offered_ticket = await _rag.async_generate_answer(
            run.question,
            retrieval.chunk_texts,
            response_language=_expected_lang,
            # Retry streams to the REAL callback: the user has seen nothing
            # yet, and gating the retry too could loop on a stubborn model.
            stream_callback=run.stream_callback,
            **_generate_kwargs,
        )
        # Accumulate rather than assign: when the abort came from
        # _gate.flush_end() (short answer), the first attempt completed and
        # its token counts were already assigned above.
        raw_answer = retry_answer
        tokens_used += retry_tokens
        _input_toks += retry_in
        _output_toks += retry_out
        llm_offered_ticket = llm_offered_ticket or retry_offered_ticket
        _lang_retry_ms = int((perf_counter() - _lang_retry_start) * 1000)
        record_stage_ms(trace, "llm_lang_retry_ms", _lang_retry_ms)
        if lang_span is not None:
            lang_span.end(
                output={
                    "regenerated": True,
                    "forced_language": _expected_lang,
                    "retry_ms": _lang_retry_ms,
                }
            )
    llm_ms = int((perf_counter() - llm_start) * 1000)
    record_stage_ms(trace, "llm_ms", llm_ms)

    # Non-streamed generation: nothing has been shown to the client, but a
    # full regeneration is still the wrong tool — translate the finished
    # answer with the localization model instead. (async_generate_answer's own
    # _enforce_response_language guard compares against response_language;
    # this covers the residual case where response_language was an "en"
    # fallback while the question was reliably non-English.)
    if run.stream_callback is None and _expected_lang and _expected_lang != "en":
        a_lang = _rag.detect_language(raw_answer)
        if (
            a_lang.is_reliable
            and a_lang.detected_language != "unknown"
            and _language_root(a_lang.detected_language) != _language_root(_expected_lang)
        ):
            _lang_retry_start = perf_counter()
            lang_span = None
            if trace is not None:
                lang_span = trace.span(
                    name="language-check",
                    input={
                        "expected_lang": _expected_lang,
                        "answer_lang": a_lang.detected_language,
                    },
                )
            try:
                _translation = await _rag.translate_text_result(
                    source_text=raw_answer,
                    target_language=_expected_lang,
                    api_key=run.api_key,
                )
                if _translation.text:
                    raw_answer = _translation.text
                    tokens_used += int(_translation.tokens_used or 0)
                    _output_toks += int(_translation.tokens_used or 0)
            except Exception as _translate_exc:
                logger.warning(
                    "language-mismatch translation failed, keeping original answer: %s",
                    _translate_exc,
                )
            _lang_retry_ms = int((perf_counter() - _lang_retry_start) * 1000)
            record_stage_ms(trace, "llm_lang_retry_ms", _lang_retry_ms)
            if lang_span is not None:
                lang_span.end(
                    output={
                        "translated": True,
                        "forced_language": _expected_lang,
                        "retry_ms": _lang_retry_ms,
                    }
                )

    raw_answer = _strip_inline_citations(raw_answer)

    final_answer = raw_answer

    # --- Escalation decision ---
    escalate, esc_trigger = _svc.should_escalate(
        retrieval.best_confidence_score,
        len(retrieval.chunk_texts),
        best_rank_score=retrieval.best_rank_score,
    )

    return ChatPipelineResult(
        raw_answer=raw_answer,
        final_answer=final_answer,
        tokens_used=int(tokens_used),
        tokens_input=_input_toks,
        tokens_output=_output_toks,
        strategy=state.strategy,
        reject_reason=None,
        is_reject=False,
        is_faq_direct=False,
        retrieval=retrieval,
        escalation_recommended=escalate,
        escalation_trigger=esc_trigger,
        llm_offered_ticket=llm_offered_ticket,
        retrieval_ms=int(retrieval.retrieval_duration_ms),
        llm_ms=llm_ms,
        llm_lang_retry_ms=_lang_retry_ms,
        faq_match=state.faq_match,
        language_context=language_context,
        query_script=state.query_script,
        kb_scripts=list(state.kb_scripts),
        cross_lingual_triggered=state.cross_lingual_triggered,
        cross_lingual_variants_count=state.cross_lingual_variants_added,
        query_kb_language_match=state.query_kb_language_match,
        retrieval_used_cross_lingual_variant=(
            state.cross_lingual_triggered
            and state.cross_lingual_variants_added > 0
            and bool(retrieval.chunk_texts)
        ),
    )
