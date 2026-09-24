"""Shared keyed circuit breaker for guard LLM/embedding calls.

After ``threshold`` consecutive failures for a key the circuit opens and
callers should fail open (skip the call) until ``half_open_after_seconds``
elapses; then one probe request is allowed through — on success the circuit
closes, on failure the timer resets.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import monotonic

_GLOBAL_KEY = "_global"


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    circuit_opened_at: float | None = None
    last_touch: float = field(default=0.0)


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int,
        half_open_after_seconds: float,
        max_keys: int | None = None,
    ) -> None:
        self._threshold = threshold
        self._half_open_after = half_open_after_seconds
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._states: dict[str, _BreakerState] = {}

    def is_open(self, key: str = _GLOBAL_KEY) -> bool:
        with self._lock:
            st = self._states.get(key)
            if st is None or st.consecutive_failures < self._threshold:
                return False
            now = monotonic()
            if st.circuit_opened_at is None:
                st.circuit_opened_at = now
            if now - st.circuit_opened_at < self._half_open_after:
                return True
            # Half-open: reset timer so only one probe gets through at a time.
            st.circuit_opened_at = None
            return False

    def record_failure(self, key: str = _GLOBAL_KEY) -> None:
        now = monotonic()
        with self._lock:
            st = self._states.get(key)
            if st is None:
                if self._max_keys is not None and len(self._states) >= self._max_keys:
                    # Evict the least-recently-touched breaker to stay bounded.
                    oldest = min(self._states, key=lambda k: self._states[k].last_touch)
                    self._states.pop(oldest, None)
                st = _BreakerState()
                self._states[key] = st
            st.consecutive_failures += 1
            st.circuit_opened_at = now
            st.last_touch = now

    def record_success(self, key: str = _GLOBAL_KEY) -> None:
        # A closed breaker needs no state; dropping the entry keeps the map small.
        with self._lock:
            self._states.pop(key, None)
