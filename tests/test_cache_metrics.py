"""Admin cache-stats endpoint: admin-only, returns the counters snapshot."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.observability import cache_metrics
from tests.conftest import register_and_verify_user


@pytest.fixture(autouse=True)
def _reset_cache_metrics():
    cache_metrics.reset()
    yield
    cache_metrics.reset()


def test_admin_cache_stats_endpoint_requires_admin_then_returns_snapshot(
    tenant: TestClient, db_session: Session
) -> None:
    """403 for a non-admin caller; 200 with the real snapshot once promoted."""
    cache_metrics.reset()
    cache_metrics.record_hit("relevance_guard")
    cache_metrics.record_miss("relevance_guard")
    cache_metrics.record_miss("embedding")

    token = register_and_verify_user(
        tenant, db_session, email="cache-stats-admin@example.com"
    )
    resp = tenant.get(
        "/admin/metrics/cache-stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    from backend.models import User

    user = (
        db_session.query(User)
        .filter(User.email == "cache-stats-admin@example.com")
        .first()
    )
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = tenant.get(
        "/admin/metrics/cache-stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["caches"]["relevance_guard"]["hits"] == 1
    assert body["caches"]["relevance_guard"]["misses"] == 1
    assert body["caches"]["relevance_guard"]["hit_rate"] == 0.5
    assert body["caches"]["embedding"]["misses"] == 1
