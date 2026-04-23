from __future__ import annotations

import hashlib
import json
import threading
import time

from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry

LLM_MODEL = "gpt-4o-mini"
TIMEOUT_SECONDS = 2.0
TEMPERATURE_STRICT = 0
MAX_TOKENS_DETECTION = 30
CACHE_TTL_SECONDS = 5 * 60
MAX_CACHE_SIZE = 2048
_CACHE_EVICT_BATCH = max(1, MAX_CACHE_SIZE // 10)

_cache: dict[str, tuple[float, bool]] = {}
_cache_lock = threading.Lock()

_SYSTEM_PROMPT = (
    "You are a classifier for a customer support bot. "
    "Determine whether the user's message is asking the bot about its own capabilities: "
    "what topics it covers, what it can help with, what it knows, or what it can do. "
    'Answer ONLY with a JSON object: {"is_capability_question": true/false}'
)


def _cache_get(key: str) -> bool | None:
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        expires_at, result = item
        if time.time() > expires_at:
            _cache.pop(key, None)
            return None
        return result


def _cache_set(key: str, result: bool) -> None:
    with _cache_lock:
        now = time.time()
        if len(_cache) >= MAX_CACHE_SIZE and key not in _cache:
            expired = [k for k, v in _cache.items() if now > v[0]]
            for k in expired:
                _cache.pop(k, None)
            # If still full after expiry sweep, evict oldest batch to amortize future scans.
            if len(_cache) >= MAX_CACHE_SIZE:
                for k in list(_cache.keys())[:_CACHE_EVICT_BATCH]:
                    _cache.pop(k, None)
        _cache[key] = (now + CACHE_TTL_SECONDS, result)


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

    try:
        client = get_openai_client(api_key)
        response = call_openai_with_retry(
            "guard_capability_check",
            lambda: client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f'User message: "{question}"'},
                ],
                temperature=TEMPERATURE_STRICT,
                max_tokens=MAX_TOKENS_DETECTION,
                response_format={"type": "json_object"},
                timeout=TIMEOUT_SECONDS,
            ),
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        result = bool(parsed.get("is_capability_question", False))
    except Exception:
        return False

    _cache_set(cache_key, result)
    return result
