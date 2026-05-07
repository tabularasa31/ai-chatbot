"""Unit tests for backend.observability.cache_metrics + admin endpoint."""

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


def test_record_hit_and_miss_accumulates() -> None:
    cache_metrics.record_hit("foo")
    cache_metrics.record_hit("foo")
    cache_metrics.record_miss("foo")
    cache_metrics.record_miss("bar")

    snap = cache_metrics.snapshot()
    assert snap["foo"]["hits"] == 2
    assert snap["foo"]["misses"] == 1
    assert snap["foo"]["hit_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert snap["bar"]["hits"] == 0
    assert snap["bar"]["misses"] == 1
    assert snap["bar"]["hit_rate"] == 0.0


def test_snapshot_empty_when_no_recordings() -> None:
    assert cache_metrics.snapshot() == {}


def test_embedding_cache_get_records_metrics() -> None:
    from backend.search import embedding_cache

    embedding_cache.clear()
    cache_metrics.reset()

    # Miss on empty cache
    assert embedding_cache.get("hello") is None
    # Put + hit
    embedding_cache.put("hello", [0.1, 0.2])
    assert embedding_cache.get("hello") == [0.1, 0.2]

    snap = cache_metrics.snapshot()
    assert snap["embedding"]["hits"] == 1
    assert snap["embedding"]["misses"] == 1


def test_admin_cache_stats_endpoint_requires_admin(
    tenant: TestClient, db_session: Session
) -> None:
    # Non-admin user → 403
    token = register_and_verify_user(
        tenant, db_session, email="cache-stats-user@example.com"
    )
    resp = tenant.get(
        "/admin/metrics/cache-stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_admin_cache_stats_endpoint_returns_snapshot(
    tenant: TestClient, db_session: Session
) -> None:
    cache_metrics.reset()
    cache_metrics.record_hit("relevance_guard")
    cache_metrics.record_miss("relevance_guard")
    cache_metrics.record_miss("embedding")

    token = register_and_verify_user(
        tenant,
        db_session,
        email="cache-stats-admin@example.com",
    )
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
