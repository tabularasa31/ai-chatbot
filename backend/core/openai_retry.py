"""In-process retry wrapper for user-facing OpenAI calls."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from backend.core.config import settings
from backend.core.openai_errors import OpenAIFailureKind, classify_openai_error

T = TypeVar("T")

logger = logging.getLogger(__name__)

_USER_BASE_DELAY_SECONDS = 0.3
_USER_MAX_DELAY_SECONDS = 1.0
_USER_RATE_LIMIT_CAP_SECONDS = 1.5
_USER_BUDGET_HEADROOM_SECONDS = 0.05


def call_openai_with_retry(
    operation: str,
    fn: Callable[[], T],
) -> T:
    started = time.monotonic()
    max_attempts = settings.openai_user_retry_max_attempts
    total_budget = settings.openai_user_retry_budget_seconds
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:
            classified = classify_openai_error(exc)
            if classified.kind == OpenAIFailureKind.PERMANENT:
                raise

            elapsed = time.monotonic() - started
            remaining = total_budget - elapsed
            if (
                classified.kind == OpenAIFailureKind.RATE_LIMIT
                and classified.retry_after_seconds is not None
                and classified.retry_after_seconds > total_budget
            ):
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                raise
            if attempt >= max_attempts or remaining <= 0:
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                raise

            delay = _delay_for_user(
                classified=classified,
                attempt=attempt,
                budget_seconds=total_budget,
            )
            if delay > remaining:
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                raise

            delay = min(delay, max(remaining - _USER_BUDGET_HEADROOM_SECONDS, 0.0))
            logger.info(
                "openai_user_retry",
                extra={
                    "operation": operation,
                    "attempt": attempt,
                    "delay_ms": int(delay * 1000),
                    "kind": classified.kind.value,
                    "status_code": classified.status_code,
                },
            )
            time.sleep(delay)
            last_exc = exc

    if last_exc is not None:  # pragma: no cover
        raise last_exc
    raise RuntimeError("openai_retry_unreachable")


def _delay_for_user(
    *,
    classified: Any,
    attempt: int,
    budget_seconds: float,
) -> float:
    if (
        classified.kind == OpenAIFailureKind.RATE_LIMIT
        and classified.retry_after_seconds is not None
    ):
        return min(
            classified.retry_after_seconds,
            _USER_RATE_LIMIT_CAP_SECONDS,
            budget_seconds,
        )

    base = min(_USER_BASE_DELAY_SECONDS * (2 ** (attempt - 1)), _USER_MAX_DELAY_SECONDS)
    jitter = random.uniform(0, base * 0.3)
    return base + jitter


def _log_retry_exhausted(
    operation: str,
    attempt: int,
    elapsed: float,
    classified: Any,
) -> None:
    logger.warning(
        "openai_user_retry_exhausted",
        extra={
            "operation": operation,
            "attempt": attempt,
            "elapsed_ms": int(elapsed * 1000),
            "kind": classified.kind.value,
            "status_code": classified.status_code,
        },
    )
