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
from backend.observability import TraceHandle

TIMEOUT_SECONDS = 3.0
CACHE_TTL_SECONDS = 5 * 60


def _emit_relevance_guard_metric(
    *, tenant_id: uuid.UUID, cache_hit: bool, blocked: bool = False, score: str = ""
) -> None:
    """Emit relevance_guard.check event to PostHog with cache_hit attribute."""
    try:
        from backend.observability.metrics import capture_event
        capture_event(
            "relevance_guard.check",
            distinct_id=str(tenant_id),
            tenant_id=str(tenant_id),
            properties={"cache_hit": cache_hit, "blocked": blocked, "score": score},
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

# Short queries (≤ SHORT_QUERY_WORD_LIMIT words) are passed through as relevant so the
# LLM can ask a clarifying question rather than the guard blindly rejecting them.
# Exception: queries that match an explicit off-topic pattern are still rejected.
SHORT_QUERY_WORD_LIMIT = 4

_cache: dict[str, tuple[float, bool, str]] = {}


def _cache_get(key: str) -> tuple[bool, str] | None:
    item = _cache.get(key)
    if not item:
        return None
    expires_at, relevant, reason = item
    if time.time() > expires_at:
        _cache.pop(key, None)
        return None
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


def _build_prompts(
    profile: TenantProfileModel,
    user_question: str,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the relevance LLM call."""
    product_name, modules_list, glossary_terms_list = _build_context(profile)
    system_prompt = (
        "You are a relevance classifier for a customer support bot.\n"
        'Answer ONLY with a JSON object: {"relevant": true/false, "reason": "one sentence"}\n'
        "Return relevant=true for any question that could plausibly be about the product, "
        "its features, pricing, account management, or how to use it — even if the answer "
        "is not in the documentation.\n"
        "Return relevant=false ONLY for questions that are clearly unrelated to the product: "
        "e.g. general coding tasks, math, creative writing, or unrelated tech support.\n"
        "When in doubt, return true."
    )
    user_prompt = (
        f"The support bot is for: {product_name}\n"
        f"Known topics: {modules_list}\n"
        f"Key terms: {glossary_terms_list}\n"
        f"User question: {json.dumps(user_question)}\n"
        "Is this question related to this product or its use?"
    )
    return system_prompt, user_prompt


def _parse_llm_response(content: str | None) -> tuple[bool, str]:
    raw = content or "{}"
    parsed = json.loads(raw)
    relevant = bool(parsed.get("relevant", True))
    reason = str(parsed.get("reason", "")) or "unknown"
    return relevant, reason


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
) -> tuple[bool, str, TenantProfileModel | None]:
    """Relevance pre-check using an already-loaded profile (no DB access).

    Callers that need to run this concurrently should pre-fetch the profile
    on the main thread and pass it here to avoid sharing a SQLAlchemy session
    across threads.

    Returns: (relevant, reason, profile_for_guard)
    """
    if not profile or _profile_is_empty(profile):
        return True, "no_profile", None

    # Very short queries are ambiguous — let the LLM ask for clarification instead of
    # the guard blindly rejecting. Skip only if the query matches a clear off-topic pattern.
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

    # Cache key: hash(tenant_id + question[:100])
    key_src = f"{tenant_id}:{user_question[:100]}"
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

    system_prompt, user_prompt = _build_prompts(profile, user_question)

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
        _record_failure()
        return True, "timeout", None
    except Exception:
        if span is not None:
            span.end(output={"relevant": True, "reason": "error"}, metadata={"error": True})
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass
        _record_failure()
        return True, "error", None
    else:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass

    _record_success()

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if span is not None:
        span.end(
            output={"relevant": relevant, "reason": reason},
            metadata={"latency_ms": latency_ms, "cache_hit": False},
        )

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
) -> tuple[bool, str, TenantProfileModel | None]:
    """Async counterpart of :func:`check_relevance_with_profile`.

    Replaces the ThreadPoolExecutor timeout pattern with ``asyncio.wait_for``
    so the event loop is not blocked during the OpenAI HTTP call.

    Returns: (relevant, reason, profile_for_guard)
    """
    if not profile or _profile_is_empty(profile):
        return True, "no_profile", None

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

    key_src = f"{tenant_id}:{user_question[:100]}"
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

    system_prompt, user_prompt = _build_prompts(profile, user_question)

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
        _record_failure()
        return True, "timeout", None
    except Exception:
        if span is not None:
            span.end(output={"relevant": True, "reason": "error"}, metadata={"error": True})
        _record_failure()
        return True, "error", None

    _record_success()

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if span is not None:
        span.end(
            output={"relevant": relevant, "reason": reason},
            metadata={"latency_ms": latency_ms, "cache_hit": False},
        )

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
