"""Shared scaffold for periodic background daemon jobs.

A :class:`PeriodicJob` runs a ``work`` callable on a fixed interval in a daemon
thread, after an initial startup delay, until shutdown. Single-process model
(one Railway dyno), matching how these jobs are wired into the FastAPI
lifespan. Each iteration is wrapped so an exception logs and the loop survives.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class PeriodicJob:
    def __init__(
        self,
        *,
        name: str,
        work: Callable[[], None],
        interval_seconds: float,
        startup_delay_seconds: float = 0.0,
        join_timeout_seconds: float = 5.0,
    ) -> None:
        self._name = name
        self._work = work
        self._interval_seconds = interval_seconds
        self._startup_delay_seconds = startup_delay_seconds
        self._join_timeout_seconds = join_timeout_seconds
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
                self._work()
            except Exception:
                logger.exception("%s: iteration failed", self._name)
            self._shutdown_event.wait(self._interval_seconds)
