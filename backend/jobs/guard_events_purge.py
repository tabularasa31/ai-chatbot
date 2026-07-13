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
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.jobs._periodic import LockSpec, PeriodicJob
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

    total = 0
    while True:
        ids = (
            db.execute(select(GuardEvent.id).where(condition).limit(batch_size))
            .scalars()
            .all()
        )
        if not ids:
            break
        db.execute(sa_delete(GuardEvent).where(GuardEvent.id.in_(ids)))
        db.commit()
        total += len(ids)
        if len(ids) < batch_size:
            break
    if total:
        logger.info("guard_events_purge: deleted %d stale rows", total)
    return total


def _purge_once() -> None:
    from backend.core.db import SessionLocal

    db = SessionLocal()
    try:
        purge_guard_events(db)
    except Exception:
        # Roll back the failed batch and let the error propagate. PeriodicJob
        # writes its durable "done today" marker only when _work() returns
        # cleanly, so re-raising leaves the marker unset and the next hourly
        # tick retries — instead of a transient DB blip suppressing the purge
        # for the rest of the UTC day. Batches committed before the failure
        # persist (partial progress is kept). The loop wrapper logs the raise.
        db.rollback()
        raise
    finally:
        db.close()


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _daily_lock_key() -> str:
    return f"lock:guard_events_purge:daily:{_today()}"


def _daily_done_marker() -> str:
    return f"done:guard_events_purge:daily:{_today()}"


_job = PeriodicJob(
    name="guard-events-purge",
    work=_purge_once,
    interval_seconds=_CHECK_INTERVAL_SECONDS,
    startup_delay_seconds=_STARTUP_DELAY_SECONDS,
    lock=LockSpec(
        job_kind="guard_events_purge",
        key_factory=_daily_lock_key,
        ttl_seconds=_LOCK_TTL_SECONDS,
        done_marker_factory=_daily_done_marker,
        done_ttl_seconds=_DONE_MARKER_TTL_SECONDS,
    ),
)


def start_guard_events_purge_thread() -> None:
    _job.start()


def shutdown_guard_events_purge_thread() -> None:
    _job.shutdown()
