"""Shared keyed circuit breaker for guard LLM/embedding calls.

After ``threshold`` consecutive failures for a key the circuit opens and
callers should fail open (skip the call) until ``half_open_after_seconds``
elapses; then one probe request is allowed through — on success the circuit
closes, on failure the timer resets.

Callers that only ever use a single, process-global circuit (no per-tenant
scoping) can omit ``key`` and rely on the default.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import TypeVar

_GLOBAL_KEY = "_global"

_T = TypeVar("_T", int, float)


def _resolve(value: _T | Callable[[], _T]) -> _T:
    return value() if callable(value) else value


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    circuit_opened_at: float | None = None
    # Wall-clock-ish ordering token for eviction (monotonic seconds of last touch).
    last_touch: float = field(default=0.0)


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int | Callable[[], int],
        half_open_after_seconds: float | Callable[[], float],
        max_keys: int | None = None,
    ) -> None:
        # Threshold/cooldown may be given as callables so callers whose module
        # constants are monkeypatched in tests keep observing the live value
        # rather than one captured at construction time.
        self._threshold = threshold
        self._half_open_after = half_open_after_seconds
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._states: dict[str, _BreakerState] = {}

    def is_open(self, key: str = _GLOBAL_KEY) -> bool:
        with self._lock:
            st = self._states.get(key)
            if st is None or st.consecutive_failures < _resolve(self._threshold):
                return False
            now = monotonic()
            if st.circuit_opened_at is None:
                st.circuit_opened_at = now
            if now - st.circuit_opened_at < _resolve(self._half_open_after):
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
