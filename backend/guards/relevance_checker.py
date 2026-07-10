from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client, get_openai_client
from backend.core.openai_retry import async_call_openai_with_retry, call_openai_with_retry
from backend.models import TenantProfile as TenantProfileModel
from backend.observability import TraceHandle, record_stage_ms
from backend.observability.cache_metrics import record_hit, record_miss

_CACHE_NAME = "relevance_guard"

TIMEOUT_SECONDS = 3.0
CACHE_TTL_SECONDS = 5 * 60


def _emit_relevance_guard_metric(
    *, tenant_id: uuid.UUID, cache_hit: bool, blocked: bool = False, score: str = ""
) -> None:
    """Emit relevance_guard.check event to PostHog (cache_hit, blocked, reason)."""
    try:
        from backend.observability.metrics import capture_event
        capture_event(
            "relevance_guard.check",
            distinct_id=str(tenant_id),
            tenant_id=str(tenant_id),
            properties={"cache_hit": cache_hit, "blocked": blocked, "reason": score},
        )
    except Exception:
        pass
MAX_CACHE_SIZE = 2048

# Circuit breaker: after this many consecutive guard failures (timeouts / errors),
# stop calling the LLM and fail open to avoid hammering OpenAI during an outage.
# After CIRCUIT_HALF_OPEN_AFTER_SECONDS the circuit enters half-open: one probe request
# is allowed through. On success the circuit closes; on failure the timer resets.
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_HALF_OPEN_AFTER_SECONDS = 60.0

_cb_lock = threading.Lock()
_consecutive_failures: int = 0
_circuit_opened_at: float | None = None

# Short queries (≤ SHORT_QUERY_WORD_LIMIT words) bypass the LLM relevance check
# and are passed through as relevant so the answer LLM can ask a clarifying
# question rather than the guard blindly rejecting them. Callers that need the
# LLM verdict regardless of length (e.g. the chat pipeline on a *second*
# consecutive zero-RAG-hits turn) can opt out of this fast path by passing
# ``force_llm_check=True``.
#
# Note: there is no off-topic pattern allowlist here — the bot is
# language-agnostic and per-language keyword lists are explicitly out of scope.
# Off-topic short queries are caught downstream by the zero-RAG-hits fast path
# in the chat pipeline, which doesn't depend on the question's surface form.
#
# The same applies to ≤4-word support complaints ("no one replied"): detecting
# them here would need either a per-language phrase list (out of scope, above)
# or an LLM call (which is exactly what the bypass avoids). They ride the
# same downstream net: zero RAG hits → rephrase prompt, and the *next* turn's
# force_llm_check pass — which does see the dialog context — classifies the
# repeated complaint as support_complaint and offers the escalation handoff.
SHORT_QUERY_WORD_LIMIT = 4

_cache: dict[str, tuple[float, bool, str]] = {}


def _cache_get(key: str) -> tuple[bool, str] | None:
    item = _cache.get(key)
    if not item:
        record_miss(_CACHE_NAME)
        return None
    expires_at, relevant, reason = item
    if time.time() > expires_at:
        _cache.pop(key, None)
        record_miss(_CACHE_NAME)
        return None
    record_hit(_CACHE_NAME)
    return relevant, reason


def _cache_set(key: str, relevant: bool, reason: str) -> None:
    # Keep memory bounded across requests.
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        # Prefer eviction of expired items; otherwise drop the one with earliest expiry.
        expired_keys = [k for k, v in _cache.items() if time.time() > v[0]]
        if expired_keys:
            for k in expired_keys[: max(1, len(expired_keys))]:
                _cache.pop(k, None)
        if len(_cache) >= MAX_CACHE_SIZE:
            oldest_key = min(_cache.items(), key=lambda item: item[1][0])[0]
            _cache.pop(oldest_key, None)
    _cache[key] = (time.time() + CACHE_TTL_SECONDS, relevant, reason)


def _profile_is_empty(profile: TenantProfileModel) -> bool:
    if not profile.product_name and not profile.topics and not profile.glossary:
        return True
    return False


def _build_context(profile: TenantProfileModel) -> tuple[str, str, str]:
    modules_list = profile.topics or []
    glossary_items = profile.glossary or []
    glossary_terms = []
    if isinstance(glossary_items, list):
        for item in glossary_items[:10]:
            if isinstance(item, dict):
                term = item.get("term")
                if isinstance(term, str) and term.strip():
                    glossary_terms.append(term.strip())
    return (
        str(profile.product_name or ""),
        ", ".join([str(m) for m in modules_list if str(m).strip()]),
        ", ".join(glossary_terms),
    )


# Categories the guard LLM classifies each message into. Only "relevant"
# continues the RAG pipeline; the others are routed by the caller (offtopic →
# reject, support_complaint → escalation offer, social → polite closing reply,
# social_question → short friendly reply about the bot). The category is
# returned in the ``reason`` slot of the guard's (relevant, reason, profile)
# result tuple.
CATEGORY_RELEVANT = "relevant"
CATEGORY_OFFTOPIC = "offtopic"
CATEGORY_SUPPORT_COMPLAINT = "support_complaint"
CATEGORY_SOCIAL = "social"
# A social turn that *asks* something about the bot itself (its capabilities,
# which languages it speaks, whether it is a bot) rather than the product. It
# is not a product question, so it can't be answered from the knowledge base,
# but refusing it ("I can't help with that") reads as a malfunction — it gets a
# short friendly reply that answers in the user's language and steers back to
# the product. Kept separate from ``social`` because that closing
# acknowledgement ("thanks for reaching out") is the wrong shape for a question.
CATEGORY_SOCIAL_QUESTION = "social_question"

_VALID_CATEGORIES = frozenset(
    {
        CATEGORY_RELEVANT,
        CATEGORY_OFFTOPIC,
        CATEGORY_SUPPORT_COMPLAINT,
        CATEGORY_SOCIAL,
        CATEGORY_SOCIAL_QUESTION,
    }
)


def _build_prompts(
    profile: TenantProfileModel,
    user_question: str,
    dialog_context: str | None = None,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the relevance LLM call."""
    product_name, modules_list, glossary_terms_list = _build_context(profile)
    system_prompt = (
        "You are a message classifier for a customer support bot.\n"
        'Answer ONLY with a JSON object: {"category": "relevant" | "offtopic" | '
        '"support_complaint" | "social" | "social_question", '
        '"reason": "one sentence"}\n'
        "Categories:\n"
        "- relevant: any message that could plausibly be about the product, its "
        "features, pricing, account management, or how to use it — even if the "
        "answer is not in the documentation. Short follow-ups that continue an "
        "on-topic conversation (\"what about X?\", \"and for businesses?\") are "
        "relevant: resolve pronouns and ellipsis against the recent conversation. "
        "A user describing their setup or status in an on-topic conversation is "
        "also relevant.\n"
        "- support_complaint: the user complains that support / the team has not "
        "replied, that they have been waiting too long, or expresses frustration "
        "about being ignored.\n"
        "- social: a pure greeting, thanks, farewell, or politeness with no "
        "question and no actionable request.\n"
        "- social_question: a greeting or small talk that also asks about the bot "
        "itself rather than the product — what it can do, which languages it "
        "speaks, whether it is a bot or a human, and similar meta questions "
        "(\"can you speak English?\", \"what can you help with?\", \"are you a "
        "robot?\"). Use this only when the question is about the assistant, not "
        "about the product.\n"
        "- offtopic: clearly unrelated to the product: general coding tasks, "
        "math, creative writing, or unrelated tech support.\n"
        "When in doubt, return relevant."
    )
    context_block = (
        f"Recent conversation:\n{dialog_context}\n" if dialog_context else ""
    )
    user_prompt = (
        f"The support bot is for: {product_name}\n"
        f"Known topics: {modules_list}\n"
        f"Key terms: {glossary_terms_list}\n"
        f"{context_block}"
        f"Latest user message: {json.dumps(user_question)}\n"
        "Classify the latest user message."
    )
    return system_prompt, user_prompt


def _parse_llm_response(content: str | None) -> tuple[bool, str]:
    """Parse the guard verdict into (relevant, category).

    The category token doubles as the machine-readable ``reason`` so callers
    can route non-relevant verdicts (offtopic / support_complaint / social)
    without a second call. Falls back to the legacy ``relevant`` boolean for
    responses that omit the category field.
    """
    raw = content or "{}"
    parsed = json.loads(raw)
    category = str(parsed.get("category", "")).strip().lower()
    if category not in _VALID_CATEGORIES:
        relevant = bool(parsed.get("relevant", True))
        category = CATEGORY_RELEVANT if relevant else CATEGORY_OFFTOPIC
    return category == CATEGORY_RELEVANT, category


def _check_circuit_breaker() -> tuple[bool, str] | None:
    """Return (True, 'circuit_open') if the circuit is open, else None."""
    global _consecutive_failures, _circuit_opened_at
    with _cb_lock:
        if _consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            now = time.monotonic()
            if _circuit_opened_at is None:
                _circuit_opened_at = now
            if now - _circuit_opened_at < CIRCUIT_HALF_OPEN_AFTER_SECONDS:
                return True, "circuit_open"
            # Half-open: reset timer so only one probe gets through at a time.
            _circuit_opened_at = None
    return None


def _record_failure() -> None:
    with _cb_lock:
        global _consecutive_failures, _circuit_opened_at
        _consecutive_failures += 1
        _circuit_opened_at = time.monotonic()


def _record_success() -> None:
    with _cb_lock:
        global _consecutive_failures, _circuit_opened_at
        _consecutive_failures = 0
        _circuit_opened_at = None


def check_relevance_with_profile(
    *,
    tenant_id: uuid.UUID,
    user_question: str,
    profile: TenantProfileModel | None,
    api_key: str,
    trace: TraceHandle | None = None,
    force_llm_check: bool = False,
    dialog_context: str | None = None,
) -> tuple[bool, str, TenantProfileModel | None]:
    """Relevance pre-check using an already-loaded profile (no DB access).

    Callers that need to run this concurrently should pre-fetch the profile
    on the main thread and pass it here to avoid sharing a SQLAlchemy session
    across threads.

    When ``force_llm_check`` is True, the short-query and circuit-breaker
    fast-paths are skipped and the LLM is always consulted. Used by the chat
    pipeline on a second consecutive zero-RAG-hits turn, where we need the
    model's verdict on domain relevance even for ≤4-word questions.

    ``dialog_context`` (rendered by ``backend.chat.followup.build_dialog_context``)
    lets the classifier resolve anaphoric follow-ups against the preceding
    turns instead of judging the message in isolation.

    Returns: (relevant, reason, profile_for_guard) — on a non-relevant LLM
    verdict ``reason`` is the category token (offtopic / support_complaint /
    social) so callers can route the reply shape.
    """
    if not profile or _profile_is_empty(profile):
        return True, "no_profile", None

    if not force_llm_check:
        word_count = len(user_question.split())
        if word_count <= SHORT_QUERY_WORD_LIMIT:
            return True, "short_query_bypass", profile

        cb = _check_circuit_breaker()
        if cb is not None:
            return cb[0], cb[1], None

    start = time.perf_counter()
    span = None
    if trace is not None:
        span = trace.span(
            name="relevance_guard",
            input={"tenant_id": str(tenant_id), "question_preview": user_question[:60]},
        )

    # Cache key: hash(tenant_id + question[:100] + dialog tail). The dialog
    # context changes the verdict (a follow-up is relevant only in context),
    # so it must be part of the key — otherwise a context-dependent verdict
    # would be replayed for the same text in a different conversation state.
    key_src = f"{tenant_id}:{user_question[:100]}:{(dialog_context or '')[-200:]}"
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        relevant, reason = cached
        if span is not None:
            span.end(
                output={"relevant": relevant, "reason": reason},
                metadata={"cache_hit": True, "latency_ms": 0},
            )
        _emit_relevance_guard_metric(tenant_id=tenant_id, cache_hit=True, blocked=not relevant, score=reason)
        return relevant, reason, profile

    system_prompt, user_prompt = _build_prompts(profile, user_question, dialog_context)

    def _call_llm() -> tuple[bool, str]:
        openai_client = get_openai_client(api_key, timeout=settings.guards_openai_timeout_seconds)
        response = call_openai_with_retry(
            "guard_relevance_check",
            lambda: openai_client.chat.completions.create(
                model=settings.relevance_guard_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_completion_tokens=80,
                response_format={"type": "json_object"},
            ),
            endpoint="chat.completions",
            langfuse_observation=span,
        )
        return _parse_llm_response(response.choices[0].message.content)

    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(_call_llm)
    try:
        relevant, reason = future.result(timeout=TIMEOUT_SECONDS)
    except TimeoutError:
        if span is not None:
            span.end(
                output={"relevant": True, "reason": "timeout"},
                metadata={"timeout": True},
            )
        # Don't wait for the potentially stuck OpenAI request thread.
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
        # See async variant: force callers must not contribute to the shared
        # CB counter, otherwise one tenant's pathological force-stream during
        # an OpenAI outage trips the breaker for every other tenant.
        if not force_llm_check:
            _record_failure()
        return True, "timeout", None
    except Exception:
        if span is not None:
            span.end(output={"relevant": True, "reason": "error"}, metadata={"error": True})
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass
        if not force_llm_check:
            _record_failure()
        return True, "error", None
    else:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass

    if not force_llm_check:
        _record_success()

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if span is not None:
        span.end(
            output={"relevant": relevant, "reason": reason},
            metadata={"latency_ms": latency_ms, "cache_hit": False},
        )
    if trace is not None:
        record_stage_ms(trace, "relevance_guard_ms", latency_ms)

    _emit_relevance_guard_metric(tenant_id=tenant_id, cache_hit=False, blocked=not relevant, score=reason)
    _cache_set(cache_key, relevant, reason)
    return relevant, reason, profile


async def async_check_relevance_with_profile(
    *,
    tenant_id: uuid.UUID,
    user_question: str,
    profile: TenantProfileModel | None,
    api_key: str,
    trace: TraceHandle | None = None,
    force_llm_check: bool = False,
    dialog_context: str | None = None,
) -> tuple[bool, str, TenantProfileModel | None]:
    """Async counterpart of :func:`check_relevance_with_profile`.

    Replaces the ThreadPoolExecutor timeout pattern with ``asyncio.wait_for``
    so the event loop is not blocked during the OpenAI HTTP call.

    See :func:`check_relevance_with_profile` for ``force_llm_check`` semantics.

    Returns: (relevant, reason, profile_for_guard)
    """
    if not profile or _profile_is_empty(profile):
        return True, "no_profile", None

    if not force_llm_check:
        word_count = len(user_question.split())
        if word_count <= SHORT_QUERY_WORD_LIMIT:
            return True, "short_query_bypass", profile

        cb = _check_circuit_breaker()
        if cb is not None:
            return cb[0], cb[1], None

    start = time.perf_counter()
    span = None
    if trace is not None:
        span = trace.span(
            name="relevance_guard",
            input={"tenant_id": str(tenant_id), "question_preview": user_question[:60]},
        )

    key_src = f"{tenant_id}:{user_question[:100]}:{(dialog_context or '')[-200:]}"
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        relevant, reason = cached
        if span is not None:
            span.end(
                output={"relevant": relevant, "reason": reason},
                metadata={"cache_hit": True, "latency_ms": 0},
            )
        _emit_relevance_guard_metric(tenant_id=tenant_id, cache_hit=True, blocked=not relevant, score=reason)
        return relevant, reason, profile

    system_prompt, user_prompt = _build_prompts(profile, user_question, dialog_context)

    async def _call_llm_async() -> tuple[bool, str]:
        client = get_async_openai_client(api_key, timeout=settings.guards_openai_timeout_seconds)
        response = await async_call_openai_with_retry(
            "guard_relevance_check",
            lambda: client.chat.completions.create(
                model=settings.relevance_guard_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_completion_tokens=80,
                response_format={"type": "json_object"},
            ),
            endpoint="chat.completions",
            langfuse_observation=span,
        )
        return _parse_llm_response(response.choices[0].message.content)

    try:
        relevant, reason = await asyncio.wait_for(
            _call_llm_async(), timeout=TIMEOUT_SECONDS
        )
    except TimeoutError:
        if span is not None:
            span.end(
                output={"relevant": True, "reason": "timeout"},
                metadata={"timeout": True},
            )
        # Force callers (chat pipeline consecutive-zero-hits path) deliberately
        # bypass the circuit breaker on the read side; their failures must NOT
        # pollute the shared CB counter — otherwise one tenant's pathological
        # zero-hits stream can trip the breaker for every other tenant's
        # regular relevance checks during an OpenAI outage.
        if not force_llm_check:
            _record_failure()
        return True, "timeout", None
    except Exception:
        if span is not None:
            span.end(output={"relevant": True, "reason": "error"}, metadata={"error": True})
        if not force_llm_check:
            _record_failure()
        return True, "error", None

    if not force_llm_check:
        _record_success()

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if span is not None:
        span.end(
            output={"relevant": relevant, "reason": reason},
            metadata={"latency_ms": latency_ms, "cache_hit": False},
        )
    if trace is not None:
        record_stage_ms(trace, "relevance_guard_ms", latency_ms)

    _emit_relevance_guard_metric(tenant_id=tenant_id, cache_hit=False, blocked=not relevant, score=reason)
    _cache_set(cache_key, relevant, reason)
    return relevant, reason, profile


def check_relevance_precheck(
    *,
    tenant_id: uuid.UUID,
    user_question: str,
    db: Session,
    api_key: str,
    trace: TraceHandle | None = None,
) -> tuple[bool, str, TenantProfileModel | None]:
    """Relevance pre-check before RAG (sync).

    Thin wrapper around check_relevance_with_profile that loads the profile
    from the DB. Use check_relevance_with_profile directly when the profile
    has already been fetched (e.g. for concurrent execution).
    """
    profile = db.get(TenantProfileModel, tenant_id)
    return check_relevance_with_profile(
        tenant_id=tenant_id,
        user_question=user_question,
        profile=profile,
        api_key=api_key,
        trace=trace,
    )


async def async_check_relevance_precheck(
    *,
    tenant_id: uuid.UUID,
    user_question: str,
    db: AsyncSession,
    api_key: str,
    trace: TraceHandle | None = None,
) -> tuple[bool, str, TenantProfileModel | None]:
    """Async counterpart of :func:`check_relevance_precheck`.

    Loads the profile via AsyncSession and delegates to
    :func:`async_check_relevance_with_profile`.
    """
    profile = await db.get(TenantProfileModel, tenant_id)
    return await async_check_relevance_with_profile(
        tenant_id=tenant_id,
        user_question=user_question,
        profile=profile,
        api_key=api_key,
        trace=trace,
    )
