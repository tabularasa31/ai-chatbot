from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
from backend.models import TenantProfile as TenantProfileModel
from backend.observability import TraceHandle

LLM_MODEL = "gpt-4o-mini"
TIMEOUT_SECONDS = 3.0
CACHE_TTL_SECONDS = 5 * 60
MAX_CACHE_SIZE = 2048

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
    if not profile.product_name and not profile.modules and not profile.glossary:
        return True
    return False


def _build_context(profile: TenantProfileModel) -> tuple[str, str, str]:
    modules_list = profile.modules or []
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


def check_relevance_precheck(
    *,
    client_id: uuid.UUID,
    user_question: str,
    db: Session,
    api_key: str,
    trace: TraceHandle | None = None,
) -> tuple[bool, str, TenantProfileModel | None]:
    """
    Relevance pre-check before RAG.

    Returns: (relevant, reason, profile_for_guard)
    If profile is empty → returns (True, "no_profile", None) to pass through.
    If LLM times out or errors → returns (True, "timeout_or_error", None) to pass through.
    """
    profile = db.get(TenantProfileModel, client_id)
    if not profile or _profile_is_empty(profile):
        return True, "no_profile", None

    # Cache key: hash(client_id + question[:100])
    key_src = f"{client_id}:{user_question[:100]}"
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        relevant, reason = cached
        return relevant, reason, profile

    product_name, modules_list, glossary_terms_list = _build_context(profile)

    system_prompt = (
        "You are a relevance classifier for a customer support bot.\n"
        'Answer ONLY with a JSON object: {"relevant": true/false, "reason": "one sentence"}'
    )
    user_prompt = (
        f"The support bot is for: {product_name}\n"
        f"Known topics: {modules_list}\n"
        f"Key terms: {glossary_terms_list}\n"
        f'User question: "{user_question}"\n'
        "Is this question relevant to this product's documentation?\n"
        "If uncertain — return true (prefer false negatives over false positives)."
    )

    start = time.perf_counter()
    span = None
    if trace is not None:
        span = trace.span(
            name="relevance_check",
            input={"client_id": str(client_id), "question_preview": user_question[:60]},
        )

    def _call_llm() -> tuple[bool, str]:
        openai_client = get_openai_client(api_key)
        response = call_openai_with_retry(
            "guard_relevance_check",
            lambda: openai_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=80,
                response_format={"type": "json_object"},
            ),
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
        return True, "timeout", None
    except Exception:
        if span is not None:
            span.end(output={"relevant": True, "reason": "error"}, metadata={"error": True})
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass
        return True, "error", None
    else:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass

    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    if span is not None:
        span.end(
            output={"relevant": relevant, "reason": reason},
            metadata={"latency_ms": latency_ms},
        )

    _cache_set(cache_key, relevant, reason)
    return relevant, reason, profile
