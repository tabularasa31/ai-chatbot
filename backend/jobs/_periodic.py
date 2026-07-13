"""Shared scaffold for periodic background daemon jobs.

A :class:`PeriodicJob` runs a ``work`` callable on a fixed interval in a daemon
thread, after an initial startup delay, until shutdown. Each iteration is
wrapped so an exception logs and the loop survives.

Multi-worker safety
-------------------
These jobs are wired into the FastAPI lifespan, so every Railway worker starts
its own copy. With a single worker that is fine; once the web service scales to
2+ replicas each tick runs N times (N duplicate KB snapshots, N duplicate
session sweeps). A :class:`LockSpec` gates a tick behind a Redis distributed
lock so only one worker performs the work per interval — an interim guard until
these jobs migrate to ARQ (which enforces single-run via unique job ids).

Graceful degradation: when ``REDIS_URL`` is unset (local dev — production
always has Redis) the lock is bypassed and the job runs unguarded, relying on
its own in-process idempotency. A transient Redis outage in production makes
``acquire_lock`` return ``None``; the tick is skipped rather than run
unguarded, so we never trade a rare skipped run for a duplicate run.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LockSpec:
    """Cross-worker gate for a periodic job's tick.

    The lock is a *mutual-exclusion* primitive only: exactly one worker runs the
    work at a time, and the lock is ALWAYS released once the work returns (or
    raises). Its TTL bounds crash recovery — a killed holder's lock self-heals
    after ``ttl_seconds`` so a later tick can take over.

    "Run at most once per window" (e.g. once per day) is a *separate* concern
    handled by an optional durable done-marker, NOT by holding the lock: a
    TTL-bounded lock cannot express "already done today" because it expires
    mid-window, letting a worker that lost the earlier race re-run the work.
    When ``done_marker_factory`` is set, a successful run writes the marker
    (``done_ttl_seconds``) and each tick skips the work while the marker exists.

    Attributes:
        job_kind: stable label for logs/metrics (``lock_acquired job_kind=…``).
        key_factory: produces the mutual-exclusion lock key for the current
            tick. A callable so time-scoped keys recompute each iteration.
        ttl_seconds: lock TTL. MUST exceed the job's worst-case runtime plus a
            buffer so a crashed holder's lock expires and a later tick takes
            over, yet be short enough that recovery lands within an acceptable
            window.
        done_marker_factory: when set, produces a durable "already ran for this
            window" key (e.g. ``done:kb_snapshot:daily:<date>``). Checked after
            acquiring the lock; written after a successful run.
        done_ttl_seconds: marker TTL. MUST outlast the window so a re-check
            within it still sees "done"; the window boundary is encoded in the
            key (a new day → a new key → the marker is absent again).
    """

    job_kind: str
    key_factory: Callable[[], str]
    ttl_seconds: int
    done_marker_factory: Callable[[], str] | None = None
    done_ttl_seconds: int = 0


class PeriodicJob:
    def __init__(
        self,
        *,
        name: str,
        work: Callable[[], None],
        interval_seconds: float,
        startup_delay_seconds: float = 0.0,
        join_timeout_seconds: float = 5.0,
        lock: LockSpec | None = None,
    ) -> None:
        self._name = name
        self._work = work
        self._interval_seconds = interval_seconds
        self._startup_delay_seconds = startup_delay_seconds
        self._join_timeout_seconds = join_timeout_seconds
        self._lock = lock
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=self._name,
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._shutdown_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._join_timeout_seconds)

    def _run(self) -> None:
        self._shutdown_event.wait(self._startup_delay_seconds)
        while not self._shutdown_event.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("%s: iteration failed", self._name)
            self._shutdown_event.wait(self._interval_seconds)

    def run_once(self) -> None:
        """Run a single tick, gated by the distributed lock when configured.

        Public so callers/tests can drive one iteration without the loop.
        """
        if self._lock is None:
            self._work()
            return

        from backend.core.redis import (
            acquire_lock_sync,
            cache_get_sync,
            cache_set_sync,
            is_enabled,
            release_lock_sync,
        )

        if not is_enabled():
            # No Redis configured: local/dev single-process. Run unguarded and
            # rely on the job's own in-process idempotency.
            self._work()
            return

        lock = self._lock
        key = lock.key_factory()
        token = acquire_lock_sync(key, lock.ttl_seconds)
        if token is None:
            # Held by another worker, or Redis is momentarily unreachable.
            # Skip this tick; the holder (or the next tick) does the work.
            self._log_lock("lock_skipped", lock, key)
            return

        self._log_lock("lock_acquired", lock, key)
        try:
            marker = lock.done_marker_factory() if lock.done_marker_factory else None
            if marker is not None and cache_get_sync(marker) is not None:
                # Already completed for this window (durable marker). Nothing to
                # do — release the lock in finally and move on.
                logger.debug("lock_window_done job_kind=%s marker=%s", lock.job_kind, marker)
                return
            self._work()
            # Marker is written only after a successful run, so a failed tick
            # (exception below skips this) retries on the next interval.
            if marker is not None:
                cache_set_sync(marker, "1", lock.done_ttl_seconds)
        finally:
            # Always release: the lock is mutual exclusion only, never the
            # once-per-window guard. Releasing on failure lets the next tick
            # retry instead of waiting out the TTL.
            if release_lock_sync(key, token):
                self._log_lock("lock_released", lock, key)

    def _log_lock(self, event: str, lock: LockSpec, key: str) -> None:
        logger.info(
            "%s job_kind=%s key=%s worker_pid=%s",
            event,
            lock.job_kind,
            key,
            os.getpid(),
        )
