"""Retry policy helpers for Gap Analyzer jobs."""

from __future__ import annotations

import random

from backend.core.config import settings
from backend.core.openai_errors import OpenAIFailureKind
from backend.models import GapAnalyzerJob

_GAP_UNKNOWN_MAX_ATTEMPTS = 3
_GAP_JITTER_FRACTION = 0.25


def effective_max_attempts(job: GapAnalyzerJob, kind: OpenAIFailureKind) -> int:
    configured = int(job.max_attempts or 0)
    if kind == OpenAIFailureKind.PERMANENT:
        return int(job.attempt_count or 0)
    if kind == OpenAIFailureKind.UNKNOWN:
        return min(configured or _GAP_UNKNOWN_MAX_ATTEMPTS, _GAP_UNKNOWN_MAX_ATTEMPTS)
    return max(configured, settings.gap_transient_max_attempts)


def retry_delay_for_kind(
    *,
    attempt_count: int,
    failure_kind: OpenAIFailureKind,
    retry_after_seconds: float | None,
) -> float:
    max_delay = settings.gap_max_delay_seconds
    if failure_kind == OpenAIFailureKind.RATE_LIMIT and retry_after_seconds is not None:
        base = min(retry_after_seconds, max_delay)
        jitter = random.uniform(0, base * 0.2)
        return max(base + jitter, 1.0)

    exponent = max(attempt_count - 1, 0)
    base = min(settings.gap_base_delay_seconds * (2 ** exponent), max_delay)
    jitter = random.uniform(-_GAP_JITTER_FRACTION, _GAP_JITTER_FRACTION)
    return max(base * (1 + jitter), 1.0)
