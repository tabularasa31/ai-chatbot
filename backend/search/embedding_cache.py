"""In-process LRU cache for OpenAI embedding vectors.

Same pattern as the relevance guard cache (backend/guards/relevance_checker.py):
a bounded dict with per-entry TTL and LRU-ish eviction.  Embedding vectors are
deterministic for a given text, so cached results are always correct.

Thread safety: Python's GIL makes individual dict reads/writes atomic.
The eviction block in ``put`` is not atomic but is idempotent — a race
between two writers produces a consistent cache, just with a slightly
different eviction order.

Memory budget: 1 536 floats x 8 bytes x 2 048 entries ~ 24 MB worst-case.
In practice entries are much smaller and the 60-minute TTL keeps the working
set warm across a full support session.
"""

from __future__ import annotations

import hashlib
import time

_CACHE_TTL_SECONDS = 3600      # 60 minutes — vectors are deterministic per model
_MAX_CACHE_SIZE    = 2048

# key → (expires_at, vector)
_cache: dict[str, tuple[float, list[float]]] = {}


def _make_key(text: str) -> str:
    """Stable cache key: SHA-256 of the normalised text."""
    normalised = text.strip().casefold()
    return hashlib.sha256(normalised.encode()).hexdigest()


def get(text: str) -> list[float] | None:
    """Return the cached embedding vector, or ``None`` on miss / expiry."""
    key = _make_key(text)
    item = _cache.get(key)
    if item is None:
        return None
    expires_at, vector = item
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        return None
    return vector


def put(text: str, vector: list[float]) -> None:
    """Store an embedding vector with a fixed TTL."""
    key = _make_key(text)
    if len(_cache) >= _MAX_CACHE_SIZE and key not in _cache:
        # Dict preserves insertion order; oldest entry is first → O(1) eviction.
        now = time.monotonic()
        while _cache and next(iter(_cache.values()))[0] < now:
            _cache.pop(next(iter(_cache)))
        if len(_cache) >= _MAX_CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, vector)


def clear() -> None:
    """Flush the entire cache. Intended for tests only."""
    _cache.clear()


def stats() -> dict[str, int]:
    """Return a snapshot of current cache size (for health checks / tests)."""
    now = time.monotonic()
    live = sum(1 for v in _cache.values() if v[0] > now)
    return {"size": len(_cache), "live": live}
