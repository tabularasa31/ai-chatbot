"""Daily ARQ cron: delete Langfuse traces past the retention window.

Self-hosted OSS Langfuse keeps traces forever — automatic retention is an
Enterprise Edition feature and our server (2.95.x) predates it anyway. Traces
are the conversations themselves (question previews, answers, and with
``OBSERVABILITY_CAPTURE_FULL_PROMPTS`` the full prompt and every chunk), so
without this job the Langfuse database grows linearly with traffic and
visitor conversations are kept indefinitely without anyone deciding so.

The job is the documented fallback for a retention setting Langfuse does not
give us: once a day it asks the public API for traces older than
``LANGFUSE_TRACE_RETENTION_DAYS`` and deletes them in batches. Deletion goes
through the API only — never a ``DELETE`` against Langfuse's tables.

A Sentry Crons monitor (``langfuse-trace-retention``) heartbeats each run, so a
dead worker or a failing Langfuse host shows up as a missed check-in instead
of as a silently growing database.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from arq.cron import cron

from backend.core.config import settings
from backend.core.queue import _CRON_JOBS
from backend.observability.langfuse_purge import delete_traces_older_than

logger = logging.getLogger(__name__)

# Off the hour so it does not stack on the other daily jobs; low-traffic UTC
# night for the Langfuse host. ``schedule`` in the monitor config must match.
_CRON_HOUR = 3
_CRON_MINUTE = 17

# A first run against months of backlog pages through tens of thousands of
# traces; ``max_runtime`` (minutes) has to cover that, not just the steady
# state of one day's worth.
_CRON_MONITOR_SLUG = "langfuse-trace-retention"
_CRON_MONITOR_CONFIG = {
    "schedule": {"type": "crontab", "value": f"{_CRON_MINUTE} {_CRON_HOUR} * * *"},
    "checkin_margin": 60,
    "max_runtime": 180,
    "failure_issue_threshold": 1,
    "recovery_threshold": 1,
}


def retention_cutoff(now: datetime | None = None) -> datetime:
    """Traces with a timestamp before this moment are past the window."""
    reference = now or datetime.now(UTC)
    return reference - timedelta(days=settings.langfuse_trace_retention_days)


async def _tick_langfuse_retention(ctx: dict[str, Any]) -> None:
    from backend.observability import capture_cron_checkin

    check_in_id = capture_cron_checkin(
        monitor_slug=_CRON_MONITOR_SLUG,
        status="in_progress",
        monitor_config=_CRON_MONITOR_CONFIG,
    )
    started = datetime.now(UTC)
    cutoff = retention_cutoff(started)

    try:
        deleted = await delete_traces_older_than(cutoff)
    except Exception:
        capture_cron_checkin(
            monitor_slug=_CRON_MONITOR_SLUG,
            status="error",
            check_in_id=check_in_id,
            duration=(datetime.now(UTC) - started).total_seconds(),
            monitor_config=_CRON_MONITOR_CONFIG,
        )
        raise

    capture_cron_checkin(
        monitor_slug=_CRON_MONITOR_SLUG,
        status="ok",
        check_in_id=check_in_id,
        duration=(datetime.now(UTC) - started).total_seconds(),
        monitor_config=_CRON_MONITOR_CONFIG,
    )
    logger.info(
        "langfuse_retention_tick cutoff=%s retention_days=%d deleted=%d",
        cutoff.isoformat(),
        settings.langfuse_trace_retention_days,
        deleted,
    )


langfuse_retention_cron = cron(
    _tick_langfuse_retention, hour={_CRON_HOUR}, minute={_CRON_MINUTE}
)
_CRON_JOBS.append(langfuse_retention_cron)
