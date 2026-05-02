"""In-process retry wrapper for user-facing OpenAI calls."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from backend.core.config import settings
from backend.core.openai_errors import (
    ClassifiedError,
    OpenAIFailureKind,
    classify_openai_error,
)
from backend.observability.metrics import capture_event

T = TypeVar("T")

logger = logging.getLogger(__name__)

_USER_BASE_DELAY_SECONDS = 0.3
_USER_MAX_DELAY_SECONDS = 1.0
_USER_RATE_LIMIT_CAP_SECONDS = 1.5
_USER_BUDGET_HEADROOM_SECONDS = 0.05


def call_openai_with_retry(
    operation: str,
    fn: Callable[[], T],
    *,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    endpoint: str | None = None,
    call_type: str = "chat_completion",
) -> T:
    started = time.monotonic()
    max_attempts = settings.openai_user_retry_max_attempts
    total_budget = settings.openai_user_retry_budget_seconds
    last_exc: Exception | None = None
    last_classified: ClassifiedError | None = None

    for attempt in range(1, max_attempts + 1):
        _emit_call_attempt(
            operation=operation,
            attempt=attempt,
            elapsed=time.monotonic() - started,
            tenant_id=tenant_id,
            bot_id=bot_id,
            call_type=call_type,
            prev_exc=last_exc,
            prev_classified=last_classified,
        )
        try:
            return fn()
        except Exception as exc:
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
                _emit_retry_exhausted(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason="rate_limit_over_budget",
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                )
                raise
            if attempt >= max_attempts or remaining <= 0:
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                _emit_retry_exhausted(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason="max_attempts" if attempt >= max_attempts else "budget_exhausted",
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                )
                raise

            delay = _delay_for_user(
                classified=classified,
                attempt=attempt,
                budget_seconds=total_budget,
            )
            if delay > remaining:
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                _emit_retry_exhausted(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason="delay_over_remaining",
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                )
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
            _emit_retry_scheduled(
                operation=operation,
                attempt=attempt,
                delay_seconds=delay,
                elapsed=elapsed,
                remaining=remaining,
                classified=classified,
                exc=exc,
                tenant_id=tenant_id,
                bot_id=bot_id,
                call_type=call_type,
            )
            time.sleep(delay)
            last_exc = exc
            last_classified = classified

    if last_exc is not None:  # pragma: no cover
        raise last_exc
    raise RuntimeError("openai_retry_unreachable")


async def async_call_openai_with_retry(
    operation: str,
    fn: Callable[[], Coroutine[Any, Any, T]],
    *,
    tenant_id: str | None = None,
    bot_id: str | None = None,
    endpoint: str | None = None,
    call_type: str = "chat_completion",
) -> T:
    """Async counterpart of :func:`call_openai_with_retry`.

    ``fn`` must be a zero-argument async callable (e.g. a coroutine factory).
    Same retry policy and budget as the sync version; retries use
    ``asyncio.sleep`` so the event loop is not blocked during back-off.
    """
    started = time.monotonic()
    max_attempts = settings.openai_user_retry_max_attempts
    total_budget = settings.openai_user_retry_budget_seconds
    last_exc: Exception | None = None
    last_classified: ClassifiedError | None = None

    for attempt in range(1, max_attempts + 1):
        _emit_call_attempt(
            operation=operation,
            attempt=attempt,
            elapsed=time.monotonic() - started,
            tenant_id=tenant_id,
            bot_id=bot_id,
            call_type=call_type,
            prev_exc=last_exc,
            prev_classified=last_classified,
        )
        try:
            return await fn()
        except Exception as exc:
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
                _emit_retry_exhausted(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason="rate_limit_over_budget",
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                )
                raise
            if attempt >= max_attempts or remaining <= 0:
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                _emit_retry_exhausted(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason="max_attempts" if attempt >= max_attempts else "budget_exhausted",
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                )
                raise

            delay = _delay_for_user(
                classified=classified,
                attempt=attempt,
                budget_seconds=total_budget,
            )
            if delay > remaining:
                _log_retry_exhausted(operation, attempt, elapsed, classified)
                _emit_retry_exhausted(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason="delay_over_remaining",
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                )
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
            _emit_retry_scheduled(
                operation=operation,
                attempt=attempt,
                delay_seconds=delay,
                elapsed=elapsed,
                remaining=remaining,
                classified=classified,
                exc=exc,
                tenant_id=tenant_id,
                bot_id=bot_id,
                call_type=call_type,
            )
            await asyncio.sleep(delay)
            last_exc = exc
            last_classified = classified

    if last_exc is not None:  # pragma: no cover
        raise last_exc
    raise RuntimeError("async_openai_retry_unreachable")


def _delay_for_user(
    *,
    classified: ClassifiedError,
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
    classified: ClassifiedError,
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


def _retry_distinct_id(tenant_id: str | None, bot_id: str | None) -> str:
    return bot_id or tenant_id or "system"


def _emit_call_attempt(
    *,
    operation: str,
    attempt: int,
    elapsed: float,
    tenant_id: str | None,
    bot_id: str | None,
    call_type: str,
    prev_exc: Exception | None = None,
    prev_classified: ClassifiedError | None = None,
) -> None:
    """Fired once per API call attempt (before fn() executes).

    For attempt > 1, prev_exc and prev_classified describe what triggered the retry.
    """
    try:
        capture_event(
            "openai_retry.attempt",
            distinct_id=_retry_distinct_id(tenant_id, bot_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "operation": operation,
                "attempt": attempt,
                "elapsed_ms": int(elapsed * 1000),
                "call_type": call_type,
                "error_type": type(prev_exc).__name__ if prev_exc is not None else None,
                "failure_kind": prev_classified.kind.value if prev_classified is not None else None,
            },
            groups={"tenant": tenant_id} if tenant_id else None,
        )
    except Exception:
        logger.warning("Failed to emit openai_retry.attempt event", exc_info=True)


def _emit_retry_scheduled(
    *,
    operation: str,
    attempt: int,
    delay_seconds: float,
    elapsed: float,
    remaining: float,
    classified: ClassifiedError,
    exc: Exception,
    tenant_id: str | None,
    bot_id: str | None,
    call_type: str,
) -> None:
    """Fired when a retry sleep is scheduled (attempt failed, will try again)."""
    try:
        capture_event(
            "openai_retry.retry_scheduled",
            distinct_id=_retry_distinct_id(tenant_id, bot_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "operation": operation,
                "attempt": attempt,
                "failure_kind": classified.kind.value,
                "error_type": type(exc).__name__,
                "status_code": classified.status_code,
                "delay_ms": int(delay_seconds * 1000),
                "elapsed_ms": int(elapsed * 1000),
                "remaining_budget_ms": max(int(remaining * 1000), 0),
                "call_type": call_type,
            },
            groups={"tenant": tenant_id} if tenant_id else None,
        )
    except Exception:
        logger.warning("Failed to emit openai_retry.retry_scheduled event", exc_info=True)


def _emit_retry_exhausted(
    *,
    operation: str,
    attempt: int,
    elapsed: float,
    classified: ClassifiedError,
    reason: str,
    tenant_id: str | None,
    bot_id: str | None,
    exc: Exception | None = None,
    endpoint: str | None = None,
    call_type: str,
) -> None:
    try:
        capture_event(
            "openai_retry.exhausted",
            distinct_id=_retry_distinct_id(tenant_id, bot_id),
            tenant_id=tenant_id,
            bot_id=bot_id,
            properties={
                "operation": operation,
                "final_attempt": attempt,
                "attempt_count": attempt,
                "failure_kind": classified.kind.value,
                "error_type": type(exc).__name__ if exc is not None else classified.kind.value,
                "endpoint": endpoint,
                "status_code": classified.status_code,
                "elapsed_ms": int(elapsed * 1000),
                "reason": reason,
                "call_type": call_type,
            },
            groups={"tenant": tenant_id} if tenant_id else None,
        )
    except Exception:
        logger.warning("Failed to emit openai_retry.exhausted event", exc_info=True)
