"""Shared lazy asyncio.Semaphore factory for background-job LLM fan-out.

Each job module gets its own semaphore instance (max 3 concurrent LLM calls,
the shared limit both callers use today) via :func:`get_llm_semaphore`, keyed
by the module's own module-level cache — call it once per module and reuse
the returned singleton. Construction is deferred to first call (not import
time), so it always binds to whichever event loop is running at call time.
"""

from __future__ import annotations

import asyncio

DEFAULT_MAX_CONCURRENT_LLM_CALLS = 3


def make_semaphore_factory(
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_LLM_CALLS,
) -> _LazySemaphore:
    """Return a callable that lazily creates and caches one semaphore."""
    return _LazySemaphore(max_concurrent)


class _LazySemaphore:
    def __init__(self, max_concurrent: int) -> None:
        self._max_concurrent = max_concurrent
        self._semaphore: asyncio.Semaphore | None = None

    def __call__(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore
