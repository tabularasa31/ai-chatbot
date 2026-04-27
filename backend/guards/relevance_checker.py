from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
from backend.models import TenantProfile as TenantProfileModel
from backend.observability import TraceHandle

TIMEOUT_SECONDS = 3.0
CACHE_TTL_SECONDS = 5 * 60
MAX_CACHE_SIZE = 2048

# Circuit breaker: after this many consecutive guard failures (timeouts / errors),
# stop calling the LLM and fail open to avoid hammering OpenAI during an outage.
CIRCUIT_BREAKER_THRESHOLD = 5

_cb_lock = threading.Lock()
_consecutive_failures: int = 0

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

    # Circuit breaker: fail open immediately if the guard has timed out/errored too many
    # times in a row (e.g. OpenAI embeddings endpoint degraded), to stop hammering the API.
    global _consecutive_failures
    with _cb_lock:
        if _consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            return True, "circuit_open", None

    # Cache key: hash(tenant_id + question[:100])
    key_src = f"{tenant_id}:{user_question[:100]}"
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        relevant, reason = cached
        return relevant, reason, profile

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

    start = time.perf_counter()
    span = None
    if trace is not None:
        span = trace.span(
            name="relevance_check",
            input={"tenant_id": str(tenant_id), "question_preview": user_question[:60]},
        )

    def _call_llm() -> tuple[bool, str]:
        openai_client = get_openai_client(api_key, timeout=settings.guards_openai_timeout_seconds)
        response = call_openai_with_retry(
            "guard_relevance_check",
            lambda: openai_client.chat.completions.create(
                model=settings.guards_model,
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
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        relevant = bool(parsed.get("relevant", True))
        reason = str(parsed.get("reason", "")) or "unknown"
        return relevant, reason

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
        with _cb_lock:
            _consecutive_failures += 1
        return True, "timeout", None
    except Exception:
        if span is not None:
            span.end(output={"relevant": True, "reason": "error"}, metadata={"error": True})
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass
        with _cb_lock:
            _consecutive_failures += 1
        return True, "error", None
    else:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass

    # Successful call — reset the circuit breaker.
    with _cb_lock:
        _consecutive_failures = 0

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if span is not None:
        span.end(
            output={"relevant": relevant, "reason": reason},
            metadata={"latency_ms": latency_ms},
        )

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
    """Relevance pre-check before RAG.

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
