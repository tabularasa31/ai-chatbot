"""In-process retry wrapper for user-facing OpenAI calls.

PostHog metric semantics — read before building a "retries per turn" chart:

``openai_retry.attempt`` fires once per OpenAI API call, **including the first
(non-retry) call** (``is_retry=False``). A single chat turn fans out into ~7-10
distinct OpenAI calls by design (injection guard, relevance guard, embed,
semantic rewrite, generate, validate, …), so ``count(openai_retry.attempt)``
divided by chat turns lands around 7-10x even when nothing was ever retried.
That ratio measures fan-out, not retries — do not read it as a "retry storm".

The canonical numerator for *actual* retries is either of these (they agree):
  • ``count(openai_retry.attempt WHERE is_retry = true)``
  • ``count(openai_retry.retry_scheduled)``
Each fires exactly once per real retry. Per-turn retry rate =
``<one of the above> / count(chat turns)``. Do NOT sum a per-attempt counter
like ``attempt - 1`` — summed across the attempt events of one call it
double-counts (0+1+2 = 3 for a call that retried twice).

``openai_retry.exhausted`` does NOT imply any retry happened. It commonly
fires at ``final_attempt=1`` with no preceding ``retry_scheduled`` /
``is_retry=true`` — e.g. an ``APITimeoutError`` (reason ``timeout_no_retry``:
retrying a call that already burned the client timeout only doubles
user-facing latency under the per-turn budget), a ``retry-after`` that
exceeds the whole budget (``rate_limit_over_budget``), or any transient
failure at a call site that passed ``max_attempts=1`` to disable retries
(reason ``max_attempts``, still attempt 1). So ``count(exhausted) > 0`` while
``count(attempt WHERE is_retry=true) = 0`` is EXPECTED, not a tracking bug.

To decide whether retries actually ran, use ``final_attempt > 1`` (equal to
``attempt_count``) or ``count(attempt WHERE is_retry=true)`` — NOT the
``reason`` bucket. ``reason`` explains *why the loop stopped*, not *how many
attempts ran*: ``max_attempts`` fires on attempt 1 when retries are disabled,
and ``timeout_no_retry`` / ``rate_limit_over_budget`` can carry
``final_attempt > 1`` when a non-retryable failure lands on a later attempt
after an earlier transient one already retried. Breaking ``exhausted`` down by
``reason`` is still useful for *categorizing* failures, but gate any "lost
retries" chart/alert on ``final_attempt > 1`` (or ``is_retry=true``).
"""

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
    max_attempts: int | None = None,
    emit_chat_failed: bool = False,
    langfuse_observation: Any | None = None,
) -> T:
    """Retry ``fn`` with exponential backoff on transient OpenAI errors.

    Pass ``max_attempts=1`` to disable retries (fail fast on first error).
    Pass ``emit_chat_failed=True`` at call sites where exhaustion means the
    user-visible chat turn failed (e.g. the main LLM generate call). Helper
    paths that catch and recover from OpenAI errors should leave it False.

    Pass ``langfuse_observation`` (a ``SpanHandle`` / ``GenerationHandle``) to
    stamp the observation with ``attempt_count`` and ``was_retried`` on
    success, or ``attempt_count`` / ``was_retried`` / ``retry_exhausted`` on
    final failure. This lets Langfuse trace readers distinguish a stage that
    succeeded on the first try from one that retried — vs counting the number
    of observations in a trace as "retries" (a common misread, since one chat
    turn fans out ~7-10 distinct OpenAI calls by design). Best-effort: any
    exception from the observation update is swallowed.
    """
    started = time.monotonic()
    max_attempts = max_attempts if max_attempts is not None else settings.openai_user_retry_max_attempts
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
            result = fn()
        except Exception as exc:
            classified = classify_openai_error(exc)
            if classified.kind == OpenAIFailureKind.PERMANENT:
                _stamp_failure_observation(
                    langfuse_observation, attempt=attempt, classified=classified
                )
                raise

            elapsed = time.monotonic() - started
            remaining = total_budget - elapsed
            exhaust_reason = _classify_exhaustion(
                classified=classified,
                attempt=attempt,
                max_attempts=max_attempts,
                remaining=remaining,
                total_budget=total_budget,
            )
            delay: float | None = None
            if exhaust_reason is None:
                delay = _delay_for_user(
                    classified=classified,
                    attempt=attempt,
                    budget_seconds=total_budget,
                )
                if delay > remaining:
                    exhaust_reason = "delay_over_remaining"

            if exhaust_reason is not None:
                _finalize_exhaustion(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason=exhaust_reason,
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                    emit_chat_failed=emit_chat_failed,
                    langfuse_observation=langfuse_observation,
                )
                raise

            assert delay is not None  # exhaust_reason is None ⇒ delay was set
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
        else:
            _stamp_observation(
                langfuse_observation,
                attempt_count=attempt,
                was_retried=attempt > 1,
            )
            return result

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
    max_attempts: int | None = None,
    emit_chat_failed: bool = False,
    langfuse_observation: Any | None = None,
) -> T:
    """Async counterpart of :func:`call_openai_with_retry`.

    ``fn`` must be a zero-argument async callable (e.g. a coroutine factory).
    Same retry policy and budget as the sync version; retries use
    ``asyncio.sleep`` so the event loop is not blocked during back-off.
    Pass ``max_attempts=1`` to disable retries (fail fast on first error).
    Pass ``emit_chat_failed=True`` at call sites where exhaustion means the
    user-visible chat turn failed. See :func:`call_openai_with_retry`.
    Pass ``langfuse_observation`` to stamp ``attempt_count`` and
    ``was_retried`` on the observation — see :func:`call_openai_with_retry`.
    """
    started = time.monotonic()
    max_attempts = max_attempts if max_attempts is not None else settings.openai_user_retry_max_attempts
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
            result = await fn()
        except Exception as exc:
            classified = classify_openai_error(exc)
            if classified.kind == OpenAIFailureKind.PERMANENT:
                _stamp_failure_observation(
                    langfuse_observation, attempt=attempt, classified=classified
                )
                raise

            elapsed = time.monotonic() - started
            remaining = total_budget - elapsed
            exhaust_reason = _classify_exhaustion(
                classified=classified,
                attempt=attempt,
                max_attempts=max_attempts,
                remaining=remaining,
                total_budget=total_budget,
            )
            delay: float | None = None
            if exhaust_reason is None:
                delay = _delay_for_user(
                    classified=classified,
                    attempt=attempt,
                    budget_seconds=total_budget,
                )
                if delay > remaining:
                    exhaust_reason = "delay_over_remaining"

            if exhaust_reason is not None:
                _finalize_exhaustion(
                    operation=operation,
                    attempt=attempt,
                    elapsed=elapsed,
                    classified=classified,
                    reason=exhaust_reason,
                    tenant_id=tenant_id,
                    bot_id=bot_id,
                    exc=exc,
                    endpoint=endpoint,
                    call_type=call_type,
                    emit_chat_failed=emit_chat_failed,
                    langfuse_observation=langfuse_observation,
                )
                raise

            assert delay is not None  # exhaust_reason is None ⇒ delay was set
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
        else:
            _stamp_observation(
                langfuse_observation,
                attempt_count=attempt,
                was_retried=attempt > 1,
            )
            return result

    if last_exc is not None:  # pragma: no cover
        raise last_exc
    raise RuntimeError("async_openai_retry_unreachable")


def _classify_exhaustion(
    *,
    classified: ClassifiedError,
    attempt: int,
    max_attempts: int,
    remaining: float,
    total_budget: float,
) -> str | None:
    """Return the exhaustion reason string, or ``None`` if a retry is viable.

    Mirrors the original 4 hard-stop branches in the retry loop:
    ``timeout_no_retry`` / ``rate_limit_over_budget`` / ``max_attempts`` /
    ``budget_exhausted``. ``delay_over_remaining`` is detected by the caller
    after ``_delay_for_user`` returns, since it depends on the computed delay.
    """
    if classified.kind == OpenAIFailureKind.TIMEOUT:
        return "timeout_no_retry"
    if (
        classified.kind == OpenAIFailureKind.RATE_LIMIT
        and classified.retry_after_seconds is not None
        and classified.retry_after_seconds > total_budget
    ):
        return "rate_limit_over_budget"
    if attempt >= max_attempts:
        return "max_attempts"
    if remaining <= 0:
        return "budget_exhausted"
    return None


def _finalize_exhaustion(
    *,
    operation: str,
    attempt: int,
    elapsed: float,
    classified: ClassifiedError,
    reason: str,
    tenant_id: str | None,
    bot_id: str | None,
    exc: Exception,
    endpoint: str | None,
    call_type: str,
    emit_chat_failed: bool,
    langfuse_observation: Any | None,
) -> None:
    """Single exit point for transient/timeout/rate-limit/budget exhaustion.

    Logs the warning, emits the PostHog ``openai_retry.exhausted`` event, and
    stamps the Langfuse observation. Caller re-raises after returning — kept
    separate so the original ``raise`` preserves the active traceback.
    """
    _log_retry_exhausted(operation, attempt, elapsed, classified)
    _emit_retry_exhausted(
        operation=operation,
        attempt=attempt,
        elapsed=elapsed,
        classified=classified,
        reason=reason,
        tenant_id=tenant_id,
        bot_id=bot_id,
        exc=exc,
        endpoint=endpoint,
        call_type=call_type,
        emit_chat_failed=emit_chat_failed,
    )
    _stamp_failure_observation(
        langfuse_observation, attempt=attempt, classified=classified
    )


def _stamp_failure_observation(
    observation: Any | None,
    *,
    attempt: int,
    classified: ClassifiedError,
) -> None:
    """Stamp the observation with retry-exhaustion metadata.

    Used both for PERMANENT errors (where no retry was ever attempted on this
    failure kind) and for transient exhaustion paths. Always sets
    ``retry_exhausted=True``; ``was_retried`` reflects the actual attempt
    counter so PERMANENT-on-first-attempt is distinguishable from
    PERMANENT-after-N-attempts.
    """
    _stamp_observation(
        observation,
        attempt_count=attempt,
        was_retried=attempt > 1,
        retry_exhausted=True,
        retry_failure_kind=classified.kind.value,
    )


def _stamp_observation(observation: Any | None, **kvs: Any) -> None:
    """Best-effort stamp of retry metadata onto a Langfuse observation.

    The observation is expected to expose ``update_metadata(**kvs)`` (see
    ``backend.observability.service.SpanHandle``). Duck-typed: any object with
    a callable ``update_metadata`` works, which keeps the retry wrapper
    decoupled from the observability layer. Any failure is swallowed — retry
    correctness must not depend on metric/trace plumbing.
    """
    if observation is None or not kvs:
        return
    try:
        update_fn = getattr(observation, "update_metadata", None)
        if callable(update_fn):
            update_fn(**kvs)
    except Exception:  # pragma: no cover — defensive only
        logger.debug("openai_retry_stamp_observation_failed", exc_info=True)


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

    Fires on EVERY attempt, including the first (``is_retry=False``). This is
    not the retry numerator — see the module docstring. To count real retries,
    filter ``is_retry = true`` (equivalently, count ``openai_retry.retry_scheduled``).

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
                "is_retry": attempt > 1,
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
    emit_chat_failed: bool = False,
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
                # True only when exhaustion failed the user-visible chat turn.
                # Auxiliary calls that degrade gracefully (NER, etc.) set this
                # False, so a "users got errors" chart can filter on it instead
                # of misreading every exhausted event as a user-facing failure.
                "user_facing": emit_chat_failed,
            },
            groups={"tenant": tenant_id} if tenant_id else None,
        )
    except Exception:
        logger.warning("Failed to emit openai_retry.exhausted event", exc_info=True)
    if emit_chat_failed:
        try:
            capture_event(
                "chat.failed",
                distinct_id=_retry_distinct_id(tenant_id, bot_id),
                tenant_id=tenant_id,
                bot_id=bot_id,
                properties={
                    "operation": operation,
                    "failure_kind": classified.kind.value,
                    "error_type": type(exc).__name__ if exc is not None else classified.kind.value,
                    "call_type": call_type,
                    "reason": reason,
                    "attempt_count": attempt,
                    "status_code": classified.status_code,
                    "elapsed_ms": int(elapsed * 1000),
                    "endpoint": endpoint,
                },
                groups={"tenant": tenant_id} if tenant_id else None,
            )
        except Exception:
            logger.warning("Failed to emit chat.failed event", exc_info=True)
