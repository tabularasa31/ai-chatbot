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

There is deliberately no Sentry Crons monitor here: the plan allows a single
one and it is held by the scheduled-crawl tick, which already answers "is the
worker alive". A failing Langfuse host surfaces as a regular Sentry error
from the raised exception; a quietly growing database is checked by hand per
``docs/07-observability-rollout.md``.
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
# night for the Langfuse host.
_CRON_HOUR = 3
_CRON_MINUTE = 17


def retention_cutoff(now: datetime | None = None) -> datetime:
    """Traces with a timestamp before this moment are past the window."""
    reference = now or datetime.now(UTC)
    return reference - timedelta(days=settings.langfuse_trace_retention_days)


async def _tick_langfuse_retention(ctx: dict[str, Any]) -> None:
    cutoff = retention_cutoff()
    deleted = await delete_traces_older_than(cutoff)
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
