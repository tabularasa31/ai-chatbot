"""Generic thread-safe TTL cache shared by guard/escalation classifier caches.

Compound eviction scans run under a lock to avoid "dict changed size during
iteration" when reached from worker threads (tests, sync tooling) as well as
the event loop. All ops are in-memory and cheap, so holding the lock is fine.
Hits/misses are reported under ``name`` via ``cache_metrics``.
"""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar

from backend.observability.cache_metrics import record_hit, record_miss

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(self, *, name: str, ttl: float, maxsize: int) -> None:
        self._name = name
        self._ttl = ttl
        self._max = maxsize
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, V]] = {}

    def get(self, key: str) -> V | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                record_miss(self._name)
                return None
            expires_at, value = item
            if time.time() > expires_at:
                self._data.pop(key, None)
                record_miss(self._name)
                return None
            record_hit(self._name)
            return value

    def set(self, key: str, value: V) -> None:
        now = time.time()
        with self._lock:
            if len(self._data) >= self._max and key not in self._data:
                expired = [k for k, v in self._data.items() if now > v[0]]
                for k in expired:
                    self._data.pop(k, None)
                if len(self._data) >= self._max:
                    oldest = min(self._data.items(), key=lambda x: x[1][0])[0]
                    self._data.pop(oldest, None)
            self._data[key] = (now + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
