"""Daily background job: purge stale ``guard_events`` rows.

``guard_events`` records ~2 rows per chat turn (injection + relevance, plus
post-retrieval re-checks) and has no natural expiry, so it grows unbounded and
slowly bloats the table and its indexes. This job trims it once per day.

Two windows, keyed on ``label``:

- **Unlabeled rows** (``label IS NULL``) are pure telemetry — deleted once older
  than ``GUARD_EVENTS_RETENTION_DAYS`` (short window).
- **Labeled rows** (``label IS NOT NULL``) are the hand-annotated FP/FN dataset
  used to tune the guards; they are kept for ``GUARD_EVENTS_LABELED_RETENTION_DAYS``
  (a much longer window) so a purge never eats the training signal.

The delete runs in bounded batches, each committed separately, so it never
holds a long lock on this write-heavy table. The cutoff is fixed at call time,
so rows written during a run are never eligible and the loop always terminates.

Runs as a :class:`~backend.jobs._periodic.PeriodicJob` daemon thread. Across
workers a Redis distributed lock (keyed on the UTC date) plus a durable
"done today" marker ensure exactly one worker purges per calendar day. Without
Redis (local dev) it runs unguarded — single-process safe, and the DELETE is
idempotent anyway (a row deleted once cannot be re-selected).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.jobs._periodic import PeriodicJob, daily_lock_spec, purge_in_batches, run_purge_once
from backend.models import GuardEvent
from backend.models.base import _utcnow

logger = logging.getLogger(__name__)

_STARTUP_DELAY_SECONDS = 120
_CHECK_INTERVAL_SECONDS = 3600
# The lock only guards mutual exclusion for the run's duration. 10 min covers a
# batched purge with ample buffer; a crashed holder self-heals before the next
# hourly tick.
_LOCK_TTL_SECONDS = 600
# Keyed on the UTC date, so a new day is a fresh key; 26h TTL just auto-cleans
# the marker after the day it belongs to.
_DONE_MARKER_TTL_SECONDS = 26 * 3600
# Rows deleted per committed batch. Small enough to avoid a long lock on the
# hot table, large enough that even a big backlog drains within one daily run.
_BATCH_SIZE = 1000


def purge_guard_events(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = _BATCH_SIZE,
) -> int:
    """Delete guard_events past their retention window; return rows deleted.

    Unlabeled rows use ``guard_events_retention_days``; labeled rows the longer
    ``guard_events_labeled_retention_days``. The two predicates are mutually
    exclusive (a row is labeled or it isn't), so there is no double counting.
    Deletes in ``batch_size`` chunks, committing each, to keep locks short.
    """
    reference = now or _utcnow()
    unlabeled_cutoff = reference - timedelta(days=settings.guard_events_retention_days)
    labeled_cutoff = reference - timedelta(
        days=settings.guard_events_labeled_retention_days
    )
    condition = or_(
        and_(GuardEvent.label.is_(None), GuardEvent.created_at < unlabeled_cutoff),
        and_(GuardEvent.label.isnot(None), GuardEvent.created_at < labeled_cutoff),
    )

    total = purge_in_batches(db, GuardEvent, condition, batch_size)
    if total:
        logger.info("guard_events_purge: deleted %d stale rows", total)
    return total


_job = PeriodicJob(
    name="guard-events-purge",
    work=lambda: run_purge_once(purge_guard_events),
    interval_seconds=_CHECK_INTERVAL_SECONDS,
    startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    lock=daily_lock_spec(
        job_kind="guard_events_purge",
        key_prefix="guard_events_purge",
        ttl_seconds=_LOCK_TTL_SECONDS,
        done_ttl_seconds=_DONE_MARKER_TTL_SECONDS,
    ),
)


def start_guard_events_purge_thread() -> None:
    _job.start()


def shutdown_guard_events_purge_thread() -> None:
    _job.shutdown()
