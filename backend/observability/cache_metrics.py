"""In-process hit/miss counters for the small per-process caches.

Used to decide whether `embedding_cache`, the relevance-guard cache, and the
human-request cache are pulling their weight under real traffic. Counters are
process-local, lock-free (the GIL makes individual int reads/writes atomic for
CPython), and exposed via the admin API.

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


def _bucket(cache: str) -> CacheCounters:
    bucket = _counters.get(cache)
    if bucket is None:
        with _lock:
            bucket = _counters.setdefault(cache, {"hits": 0, "misses": 0})
    return bucket


def record_hit(cache: str) -> None:
    """Increment the hit counter for `cache`."""
    bucket = _bucket(cache)
    bucket["hits"] += 1


def record_miss(cache: str) -> None:
    """Increment the miss counter for `cache`."""
    bucket = _bucket(cache)
    bucket["misses"] += 1


def snapshot() -> dict[str, dict[str, float | int]]:
    """Return a point-in-time view: {cache_name: {hits, misses, hit_rate}}."""
    out: dict[str, dict[str, float | int]] = {}
    for name, c in list(_counters.items()):
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
