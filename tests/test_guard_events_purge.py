"""Row-selection logic for the guard_events retention purge job.

Covers the two retention windows (unlabeled vs. labeled) and batched deletion.
See backend/jobs/guard_events_purge.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from backend.core.config import settings
from backend.jobs import guard_events_purge
from backend.jobs.guard_events_purge import purge_guard_events
from backend.models import GuardEvent, Tenant


@pytest.fixture()
def tenant_row(db_session):
    tenant = Tenant(name="Purge Tenant", public_id="purge-tenant")
    db_session.add(tenant)
    db_session.commit()
    return tenant


def _add_event(
    db_session,
    tenant_id,
    *,
    created_at: datetime,
    label: str | None = None,
) -> uuid.UUID:
    ev = GuardEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        kind="injection",
        blocked=False,
        reason="ok",
        label=label,
        created_at=created_at,
    )
    db_session.add(ev)
    db_session.commit()
    return ev.id


def _ids(db_session) -> set[uuid.UUID]:
    return {row.id for row in db_session.query(GuardEvent).all()}


def test_deletes_only_unlabeled_rows_past_short_window(db_session, tenant_row):
    now = datetime(2026, 7, 13, 12, 0, 0)
    retention = settings.guard_events_retention_days

    stale = _add_event(
        db_session, tenant_row.id, created_at=now - timedelta(days=retention + 5)
    )
    fresh = _add_event(
        db_session, tenant_row.id, created_at=now - timedelta(days=retention - 5)
    )

    deleted = purge_guard_events(db_session, now=now)

    assert deleted == 1
    assert _ids(db_session) == {fresh}
    assert stale not in _ids(db_session)


def test_labeled_rows_survive_short_window_but_purge_past_long_window(
    db_session, tenant_row
):
    now = datetime(2026, 7, 13, 12, 0, 0)
    short = settings.guard_events_retention_days
    long = settings.guard_events_labeled_retention_days
    assert long > short  # the labeled dataset is kept strictly longer

    # Labeled and older than the short (unlabeled) window, but within the long
    # window → kept, because it is annotated tuning data.
    kept_label = _add_event(
        db_session,
        tenant_row.id,
        created_at=now - timedelta(days=short + 5),
        label="fp",
    )
    # Labeled and past even the long window → finally purged.
    old_label = _add_event(
        db_session,
        tenant_row.id,
        created_at=now - timedelta(days=long + 5),
        label="fn",
    )

    deleted = purge_guard_events(db_session, now=now)

    assert deleted == 1
    assert _ids(db_session) == {kept_label}
    assert old_label not in _ids(db_session)


def test_batched_delete_drains_full_backlog(db_session, tenant_row):
    now = datetime(2026, 7, 13, 12, 0, 0)
    old = now - timedelta(days=settings.guard_events_retention_days + 30)
    for _ in range(7):
        _add_event(db_session, tenant_row.id, created_at=old)

    # batch_size smaller than the backlog exercises the loop across passes.
    deleted = purge_guard_events(db_session, now=now, batch_size=3)

    assert deleted == 7
    assert _ids(db_session) == set()


def test_noop_when_nothing_is_stale(db_session, tenant_row):
    now = datetime(2026, 7, 13, 12, 0, 0)
    recent = _add_event(
        db_session, tenant_row.id, created_at=now - timedelta(days=1)
    )

    deleted = purge_guard_events(db_session, now=now)

    assert deleted == 0
    assert _ids(db_session) == {recent}


def test_purge_once_propagates_failure(db_session):
    """A purge failure must bubble so PeriodicJob skips its done-marker.

    PeriodicJob writes the daily "done" marker only when _work() returns
    cleanly; swallowing the error there would suppress retries for the rest of
    the UTC day. _purge_once must roll back and re-raise.
    """

    class _Boom(Exception):
        pass

    rolled_back = {"count": 0}

    def _rollback():
        rolled_back["count"] += 1

    db_session.rollback = _rollback  # observe rollback without ending the txn

    # _purge_once does `from backend.core.db import SessionLocal` at call time.
    with (
        patch("backend.core.db.SessionLocal", return_value=db_session),
        patch.object(
            guard_events_purge, "purge_guard_events", side_effect=_Boom("db down")
        ),
    ):
        with pytest.raises(_Boom):
            guard_events_purge._purge_once()

    assert rolled_back["count"] == 1
