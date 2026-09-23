"""Tests for widget API key rotation (tenant_api_keys table).

Covers the contract documented in [Security] API key rotation:
  * one ACTIVE key per tenant on creation
  * rotate puts the old key into REVOKING with a grace window
  * API continues to authenticate with the old key during grace
  * API rejects the old key after the grace window
  * immediate revoke kills a key with no grace
  * cannot revoke the only remaining usable key
  * lookups go through key_hash, not plaintext

Key validity is probed via POST /chat with X-API-Key header:
  * valid key (no OpenAI configured) → 400
  * unknown / revoked key → 401
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import TenantApiKey
from backend.tenants.api_keys_service import (
    find_active_tenant_by_plain_key,
    hash_api_key,
)
from tests.conftest import register_and_verify_user


def _create_tenant(client: TestClient, db: Session, email: str) -> tuple[str, str]:
    """Returns (jwt, plaintext_widget_key)."""
    token = register_and_verify_user(client, db, email=email)
    resp = client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Rotation Tenant"},
    )
    assert resp.status_code == 201, resp.json()
    return token, resp.json()["api_key"]


def _make_owner(db: Session, email: str) -> None:
    from backend.models import User

    user = db.query(User).filter(User.email == email).first()
    user.role = "owner"
    db.commit()


def _probe_key(client: TestClient, key: str) -> int:
    """POST /chat with X-API-Key; returns status code.
    400 = key accepted (no OpenAI configured), 401 = key rejected.
    """
    return client.post(
        "/chat",
        json={"question": "test"},
        headers={"X-API-Key": key},
    ).status_code


def test_create_tenant_key_lifecycle_hash_and_probe(
    tenant: TestClient, db_session: Session
) -> None:
    """Plaintext is returned once and never stored; only its hash + hint
    are; the plaintext key authenticates, an unknown one does not."""
    _, plain = _create_tenant(tenant, db_session, "rot1@example.com")
    assert plain.startswith("ck_")
    rows = db_session.query(TenantApiKey).all()
    assert len(rows) == 1
    assert rows[0].key_hash == hash_api_key(plain)
    assert rows[0].key_hint == plain[-4:]
    assert rows[0].status == "active"

    assert _probe_key(tenant, plain) == 400
    assert _probe_key(tenant, "ck_deadbeef" + "0" * 24) == 401


def test_rotate_grace_old_key_still_works_then_expires(
    tenant: TestClient, db_session: Session
) -> None:
    token, old = _create_tenant(tenant, db_session, "rot-grace@example.com")
    _make_owner(db_session, "rot-grace@example.com")

    rot = tenant.post(
        "/tenants/me/api-keys/rotate",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "leaked"},
    )
    assert rot.status_code == 201, rot.json()
    new_plain = rot.json()["api_key"]
    assert new_plain != old
    assert new_plain.startswith("ck_")

    # Both keys must work during the grace window.
    assert _probe_key(tenant, old) == 400
    assert _probe_key(tenant, new_plain) == 400

    # Force-expire the old key by rewinding its expires_at into the past.
    old_row = (
        db_session.query(TenantApiKey)
        .filter(TenantApiKey.key_hash == hash_api_key(old))
        .first()
    )
    assert old_row is not None
    assert old_row.status == "revoking"
    old_row.expires_at = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(
        seconds=10
    )
    db_session.commit()

    assert _probe_key(tenant, old) == 401


def test_rotate_with_immediate_revoke_kills_old_key_now(
    tenant: TestClient, db_session: Session
) -> None:
    token, old = _create_tenant(tenant, db_session, "rot-immediate@example.com")
    _make_owner(db_session, "rot-immediate@example.com")

    rot = tenant.post(
        "/tenants/me/api-keys/rotate",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "compromise", "revoke_old_immediately": True},
    )
    assert rot.status_code == 201
    new_plain = rot.json()["api_key"]

    assert _probe_key(tenant, old) == 401
    assert _probe_key(tenant, new_plain) == 400

    old_row = (
        db_session.query(TenantApiKey)
        .filter(TenantApiKey.key_hash == hash_api_key(old))
        .first()
    )
    assert old_row.status == "revoked"
    assert old_row.revoked_reason == "compromise"


def test_revoke_endpoint_then_cannot_revoke_only_remaining_key(
    tenant: TestClient, db_session: Session
) -> None:
    """DELETE revokes a specified key (killing auth with it); once only one
    active key remains, revoking it is blocked with 409."""
    token, old = _create_tenant(tenant, db_session, "rot-del@example.com")
    _make_owner(db_session, "rot-del@example.com")

    # Rotate first so we have two keys (active + revoking).
    rot = tenant.post(
        "/tenants/me/api-keys/rotate",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "scheduled"},
    )
    assert rot.status_code == 201
    old_row = (
        db_session.query(TenantApiKey)
        .filter(TenantApiKey.key_hash == hash_api_key(old))
        .first()
    )

    resp = tenant.delete(
        f"/tenants/me/api-keys/{old_row.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"
    assert _probe_key(tenant, old) == 401

    remaining = (
        db_session.query(TenantApiKey)
        .filter(TenantApiKey.tenant_id == old_row.tenant_id, TenantApiKey.status == "active")
        .all()
    )
    assert len(remaining) == 1
    resp2 = tenant.delete(
        f"/tenants/me/api-keys/{remaining[0].id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 409


def test_list_api_keys_returns_all_no_plaintext(
    tenant: TestClient, db_session: Session
) -> None:
    token, _ = _create_tenant(tenant, db_session, "rot-list@example.com")
    resp = tenant.get(
        "/tenants/me/api-keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert "key_hint" in item and len(item["key_hint"]) == 4
    assert "key_hash" not in item
    assert "api_key" not in item


def test_lookup_uses_hash_not_plaintext(
    tenant: TestClient, db_session: Session
) -> None:
    _, plain = _create_tenant(tenant, db_session, "rot-hash@example.com")

    # Direct service call — must hit by hashing the input.
    found = find_active_tenant_by_plain_key(plain, db_session)
    assert found is not None
    # Wrong plaintext (different by one char) must not match — proves we are
    # not doing substring or prefix matching.
    not_found = find_active_tenant_by_plain_key(plain[:-1] + "x", db_session)
    assert not_found is None


def test_rotate_rate_limited_per_tenant(
    tenant: TestClient, db_session: Session
) -> None:
    """11th rotation in an hour returns 429 with Retry-After (per-tenant)."""
    from backend.core.limiter import set_owner_jwt_rate_limit_key_override

    token, _ = _create_tenant(tenant, db_session, "rot-rl@example.com")
    _make_owner(db_session, "rot-rl@example.com")

    set_owner_jwt_rate_limit_key_override(lambda r: r.headers.get("x-test-owner", "fixed-A"))
    try:
        for i in range(10):
            resp = tenant.post(
                "/tenants/me/api-keys/rotate",
                headers={"Authorization": f"Bearer {token}", "x-test-owner": "fixed-A"},
                json={"reason": "scheduled"},
            )
            assert resp.status_code == 201, (i, resp.json())
        resp = tenant.post(
            "/tenants/me/api-keys/rotate",
            headers={"Authorization": f"Bearer {token}", "x-test-owner": "fixed-A"},
            json={"reason": "scheduled"},
        )
        assert resp.status_code == 429
        assert "retry-after" in {k.lower() for k in resp.headers.keys()}

        # Different tenant identity → not throttled.
        token2, _ = _create_tenant(tenant, db_session, "rot-rl2@example.com")
        _make_owner(db_session, "rot-rl2@example.com")
        resp = tenant.post(
            "/tenants/me/api-keys/rotate",
            headers={"Authorization": f"Bearer {token2}", "x-test-owner": "fixed-B"},
            json={"reason": "scheduled"},
        )
        assert resp.status_code == 201, resp.json()
    finally:
        set_owner_jwt_rate_limit_key_override(None)


def test_revoke_rate_limited_per_tenant(
    tenant: TestClient, db_session: Session
) -> None:
    """21st revoke in an hour returns 429."""
    from backend.core.limiter import set_owner_jwt_rate_limit_key_override

    token, _ = _create_tenant(tenant, db_session, "rev-rl@example.com")
    _make_owner(db_session, "rev-rl@example.com")

    set_owner_jwt_rate_limit_key_override(lambda r: r.headers.get("x-test-owner", "fixed-D"))
    try:
        # Hammer DELETE on a non-existent key — slowapi counts before route logic,
        # so 404s still consume quota.
        bogus = uuid.uuid4()
        for i in range(20):
            resp = tenant.delete(
                f"/tenants/me/api-keys/{bogus}",
                headers={"Authorization": f"Bearer {token}", "x-test-owner": "fixed-D"},
            )
            # 404 (key not found) is fine — we're testing the limiter, not the route.
            assert resp.status_code in (200, 404, 409), (i, resp.status_code)
        resp = tenant.delete(
            f"/tenants/me/api-keys/{bogus}",
            headers={"Authorization": f"Bearer {token}", "x-test-owner": "fixed-D"},
        )
        assert resp.status_code == 429
    finally:
        set_owner_jwt_rate_limit_key_override(None)


def test_rotate_requires_owner_role(
    tenant: TestClient, db_session: Session
) -> None:
    token, _ = _create_tenant(tenant, db_session, "rot-noowner@example.com")
    from backend.models import User
    user = db_session.query(User).filter(
        User.email == "rot-noowner@example.com"
    ).first()
    user.role = "member"
    db_session.commit()
    resp = tenant.post(
        "/tenants/me/api-keys/rotate",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "scheduled"},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "get_window_stats,expected",
    [
        pytest.param(lambda limit, *a: (125.2, 0), 26, id="normal_uses_remaining_window"),
        pytest.param(
            lambda *a: (_ for _ in ()).throw(RuntimeError("storage unavailable")),
            None,
            id="falls_back_when_window_stats_unavailable",
        ),
    ],
)
def test_retry_after_seconds_computation(monkeypatch, get_window_stats, expected) -> None:
    from backend import main as app_main

    request = SimpleNamespace(
        state=SimpleNamespace(view_rate_limit=("limit", ["key", "scope"]))
    )

    class FakeStorageLimiter:
        @staticmethod
        def get_window_stats(limit, *args):
            return get_window_stats(limit, *args)

    monkeypatch.setattr(app_main.limiter, "_limiter", FakeStorageLimiter())
    monkeypatch.setattr(app_main.time, "time", lambda: 100.0)

    if expected is None:
        assert (
            app_main._retry_after_seconds(request)
            == app_main.RATE_LIMIT_RETRY_AFTER_FALLBACK_SECONDS
        )
    else:
        assert app_main._retry_after_seconds(request) == expected
