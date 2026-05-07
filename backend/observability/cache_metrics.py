"""In-process hit/miss counters for the small per-process caches.

Used to decide whether `embedding_cache`, the relevance-guard cache, and the
human-request cache are pulling their weight under real traffic. Counters are
process-local and exposed via the admin API.

Thread safety: a single module-level `Lock` guards all reads and writes,
including the `+= 1` increment (which decomposes into LOAD_FAST / BINARY_OP /
STORE_SUBSCR bytecodes — the GIL can be released between them, so naked
increments would lose updates under contention). The lock is held only for
the trivial dict op, which is cheap relative to the cache miss paths these
counters track (OpenAI calls).

Per-event PostHog capture is intentionally avoided: cache lookups happen on
hot paths and would dominate event volume without adding signal beyond an
aggregate hit-rate.
"""

from __future__ import annotations

from threading import Lock
from typing import TypedDict


class CacheCounters(TypedDict):
    hits: int
    misses: int


_counters: dict[str, CacheCounters] = {}
_lock = Lock()


def record_hit(cache: str) -> None:
    """Increment the hit counter for `cache`."""
    with _lock:
        bucket = _counters.setdefault(cache, {"hits": 0, "misses": 0})
        bucket["hits"] += 1


def record_miss(cache: str) -> None:
    """Increment the miss counter for `cache`."""
    with _lock:
        bucket = _counters.setdefault(cache, {"hits": 0, "misses": 0})
        bucket["misses"] += 1


def snapshot() -> dict[str, dict[str, float | int]]:
    """Return a point-in-time view: {cache_name: {hits, misses, hit_rate}}."""
    with _lock:
        items = [(name, dict(c)) for name, c in _counters.items()]
    out: dict[str, dict[str, float | int]] = {}
    for name, c in items:
        total = c["hits"] + c["misses"]
        hit_rate = (c["hits"] / total) if total else 0.0
        out[name] = {
            "hits": c["hits"],
            "misses": c["misses"],
            "hit_rate": round(hit_rate, 4),
        }
    return out


def reset() -> None:
    """Clear all counters. For tests."""
    with _lock:
        _counters.clear()
