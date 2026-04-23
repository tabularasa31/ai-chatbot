from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry

LLM_MODEL = "gpt-4o-mini"
TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 5 * 60
MAX_CACHE_SIZE = 2048

_cache: dict[str, tuple[float, bool]] = {}

_SYSTEM_PROMPT = (
    "You are a classifier for a customer support bot. "
    "Determine whether the user's message is asking the bot about its own capabilities: "
    "what topics it covers, what it can help with, what it knows, or what it can do. "
    'Answer ONLY with a JSON object: {"is_capability_question": true/false}'
)


def _cache_get(key: str) -> bool | None:
    item = _cache.get(key)
    if not item:
        return None
    expires_at, result = item
    if time.time() > expires_at:
        _cache.pop(key, None)
        return None
    return result


def _cache_set(key: str, result: bool) -> None:
    if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
        expired_keys = [k for k, v in _cache.items() if time.time() > v[0]]
        if expired_keys:
            for k in expired_keys[: max(1, len(expired_keys))]:
                _cache.pop(k, None)
        if len(_cache) >= MAX_CACHE_SIZE:
            oldest_key = min(_cache.items(), key=lambda item: item[1][0])[0]
            _cache.pop(oldest_key, None)
    _cache[key] = (time.time() + CACHE_TTL_SECONDS, result)


def detect_capability_question(question: str, *, api_key: str) -> bool:
    """Return True if the question is asking about the bot's capabilities.

    Language-agnostic LLM classifier. Falls back to False on timeout or error
    so the normal pipeline runs unchanged.
    """
    key_src = f"cap:{question[:100]}"
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    def _call_llm() -> bool:
        client = get_openai_client(api_key)
        response = call_openai_with_retry(
            "guard_capability_check",
            lambda: client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f'User message: "{question}"'},
                ],
                temperature=0,
                max_tokens=30,
                response_format={"type": "json_object"},
            ),
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return bool(parsed.get("is_capability_question", False))

    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(_call_llm)
    try:
        result = future.result(timeout=TIMEOUT_SECONDS)
    except (TimeoutError, Exception):
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
        return False
    else:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass

    _cache_set(cache_key, result)
    return result
