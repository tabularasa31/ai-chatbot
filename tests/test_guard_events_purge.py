"""Row-selection logic for the guard_events retention purge job.

Covers the two retention windows (unlabeled vs. labeled) and batched deletion.
See backend/jobs/guard_events_purge.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from backend.core.config import settings
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


@pytest.mark.parametrize(
    "rows, batch_size, expected_kept",
    [
        pytest.param(
            [("short+5", None), ("short-5", None)],
            None,
            {1},
            id="unlabeled_past_short_window_deleted",
        ),
        pytest.param(
            [("short+5", "fp"), ("long+5", "fn")],
            None,
            {0},
            id="labeled_survives_short_window_purged_past_long",
        ),
        pytest.param(
            [("short+30", None)] * 7,
            3,
            set(),
            id="batches_drain_backlog_larger_than_one_batch",
        ),
    ],
)
def test_purge_windows_and_batching(
    db_session, tenant_row, rows, batch_size, expected_kept
):
    now = datetime(2026, 7, 13, 12, 0, 0)
    short = settings.guard_events_retention_days
    long = settings.guard_events_labeled_retention_days
    assert long > short
    age = {"short+5": short + 5, "short-5": short - 5, "short+30": short + 30, "long+5": long + 5}

    ids = [
        _add_event(db_session, tenant_row.id, created_at=now - timedelta(days=age[a]), label=label)
        for a, label in rows
    ]

    kwargs = {"batch_size": batch_size} if batch_size else {}
    deleted = purge_guard_events(db_session, now=now, **kwargs)

    assert deleted == len(rows) - len(expected_kept)
    assert _ids(db_session) == {ids[i] for i in expected_kept}
