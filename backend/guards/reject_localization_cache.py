"""In-process LRU cache for reject-response localizations.

Reject canonical texts are deterministic for a given (RejectReason, tenant
profile fragment, soft-invite flag) tuple, and the localization step is a
pure ``canonical_text + target_language → translated_text`` mapping. The
output is stable per (canonical_text, target_language), so caching is
always correct.

Sized for ~10 reject canonicals across ~30 reasonable target languages
with slack for variants.

Thread safety: same trade-off as ``backend/search/embedding_cache.py`` —
the GIL makes individual dict reads/writes atomic; eviction races are
idempotent.
"""

from __future__ import annotations

import hashlib
import time

from backend.observability.cache_metrics import record_hit, record_miss

_CACHE_NAME = "reject_localization"
_CACHE_TTL_SECONDS = 24 * 3600   # canonical texts are static; long TTL is safe
_MAX_CACHE_SIZE = 512

# key → (expires_at, (text, tokens_used))
_cache: dict[str, tuple[float, tuple[str, int]]] = {}


def _make_key(canonical_text: str, target_language: str) -> str:
    raw = f"{target_language}\x00{canonical_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(canonical_text: str, target_language: str) -> tuple[str, int] | None:
    """Return cached ``(text, tokens_used)`` or ``None`` on miss / expiry."""
    key = _make_key(canonical_text, target_language)
    item = _cache.get(key)
    if item is None:
        record_miss(_CACHE_NAME)
        return None
    expires_at, payload = item
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        record_miss(_CACHE_NAME)
        return None
    record_hit(_CACHE_NAME)
    return payload


def put(canonical_text: str, target_language: str, text: str, tokens_used: int) -> None:
    """Store the localized payload with a fixed TTL."""
    key = _make_key(canonical_text, target_language)
    if len(_cache) >= _MAX_CACHE_SIZE and key not in _cache:
        now = time.monotonic()
        # Drop expired entries first (cheap on a dict that preserves insertion order).
        while _cache and next(iter(_cache.values()))[0] < now:
            _cache.pop(next(iter(_cache)))
        if len(_cache) >= _MAX_CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, (text, tokens_used))


def clear() -> None:
    """Flush the entire cache. Intended for tests."""
    _cache.clear()


def stats() -> dict[str, int]:
    """Return a snapshot of current cache size (for health checks / tests)."""
    now = time.monotonic()
    live = sum(1 for v in _cache.values() if v[0] > now)
    return {"size": len(_cache), "live": live}
