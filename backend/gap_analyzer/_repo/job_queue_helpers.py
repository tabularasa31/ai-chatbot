"""Pure helpers and constants for the gap-analyzer job queue.

Kept separate from ``job_queue.py`` so the queue implementation stays
focused on persistence logic and does not drift past its architecture
guardrail.  These helpers are intentionally side-effect free and do not
import SQLAlchemy session state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.gap_analyzer._repo.capabilities import _aware_datetime
from backend.gap_analyzer.enums import GapJobKind, GapJobStatus

_GAP_JOB_LEASE_SECONDS = 1800
_GAP_JOB_CLAIM_MAX_ATTEMPTS = 3
_GAP_JOB_LAST_ERROR_MAX_CHARS = 4000


def _gap_job_status(value: GapJobStatus | str) -> GapJobStatus:
    if isinstance(value, GapJobStatus):
        return value
    return GapJobStatus(str(value))


def _gap_job_kind(value: GapJobKind | str) -> GapJobKind:
    if isinstance(value, GapJobKind):
        return value
    return GapJobKind(str(value))


def _remaining_lease_seconds(lease_expires_at: datetime | None) -> int | None:
    if lease_expires_at is None:
        return None
    aware_lease_expires_at = _aware_datetime(lease_expires_at)
    remaining = int((aware_lease_expires_at - datetime.now(UTC)).total_seconds())
    return max(1, remaining) if remaining > 0 else None


def _truncate_gap_job_error(error_message: str) -> str:
    if len(error_message) <= _GAP_JOB_LAST_ERROR_MAX_CHARS:
        return error_message
    truncated_prefix = "...[truncated]\n"
    tail_size = _GAP_JOB_LAST_ERROR_MAX_CHARS - len(truncated_prefix)
    if tail_size <= 0:
        return error_message[-_GAP_JOB_LAST_ERROR_MAX_CHARS:]
    # Keep the traceback tail because the final frames and exception text are usually the most actionable.
    return truncated_prefix + error_message[-tail_size:]
