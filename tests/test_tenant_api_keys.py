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


def test_create_tenant_returns_plaintext_once_and_stores_hash(
    tenant: TestClient, db_session: Session
) -> None:
    _, plain = _create_tenant(tenant, db_session, "rot1@example.com")
    assert plain.startswith("ck_")
    # Plaintext is never stored — only its hash is.
    rows = db_session.query(TenantApiKey).all()
    assert len(rows) == 1
    assert rows[0].key_hash == hash_api_key(plain)
    assert rows[0].key_hint == plain[-4:]
    assert rows[0].status == "active"


def _probe_key(client: TestClient, key: str) -> int:
    """POST /chat with X-API-Key; returns status code.
    400 = key accepted (no OpenAI configured), 401 = key rejected.
    """
    return client.post(
        "/chat",
        json={"question": "test"},
        headers={"X-API-Key": key},
    ).status_code


def test_api_key_works_with_initial_key(
    tenant: TestClient, db_session: Session
) -> None:
    _, plain = _create_tenant(tenant, db_session, "rot-widget@example.com")
    # 400 = key accepted but OpenAI not configured (expected in tests)
    assert _probe_key(tenant, plain) == 400


def test_api_key_rejects_unknown_key(tenant: TestClient) -> None:
    assert _probe_key(tenant, "ck_deadbeef" + "0" * 24) == 401


def test_rotate_grace_old_key_still_works_then_expires(
    tenant: TestClient, db_session: Session
) -> None:
    token, old = _create_tenant(tenant, db_session, "rot-grace@example.com")
    # Promote the user to owner — rotation requires owner role.
    from backend.models import User
    user = db_session.query(User).filter(User.email == "rot-grace@example.com").first()
    user.role = "owner"
    db_session.commit()

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
    from backend.models import User
    user = db_session.query(User).filter(
        User.email == "rot-immediate@example.com"
    ).first()
    user.role = "owner"
    db_session.commit()

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


def test_revoke_endpoint_kills_specified_key(
    tenant: TestClient, db_session: Session
) -> None:
    token, old = _create_tenant(tenant, db_session, "rot-del@example.com")
    from backend.models import User
    user = db_session.query(User).filter(User.email == "rot-del@example.com").first()
    user.role = "owner"
    db_session.commit()

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


def test_cannot_revoke_only_active_key(
    tenant: TestClient, db_session: Session
) -> None:
    token, _plain = _create_tenant(tenant, db_session, "rot-only@example.com")
    from backend.models import User
    user = db_session.query(User).filter(User.email == "rot-only@example.com").first()
    user.role = "owner"
    db_session.commit()

    rows = (
        db_session.query(TenantApiKey)
        .filter(TenantApiKey.tenant_id == user.tenant_id)
        .all()
    )
    assert len(rows) == 1
    only_id = rows[0].id

    resp = tenant.delete(
        f"/tenants/me/api-keys/{only_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


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
    from backend.models import User
    user = db_session.query(User).filter(User.email == "rot-rl@example.com").first()
    user.role = "owner"
    db_session.commit()

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
        u2 = db_session.query(User).filter(User.email == "rot-rl2@example.com").first()
        u2.role = "owner"
        db_session.commit()
        resp = tenant.post(
            "/tenants/me/api-keys/rotate",
            headers={"Authorization": f"Bearer {token2}", "x-test-owner": "fixed-B"},
            json={"reason": "scheduled"},
        )
        assert resp.status_code == 201, resp.json()
    finally:
        set_owner_jwt_rate_limit_key_override(None)


def test_retry_after_uses_remaining_rate_limit_window(monkeypatch) -> None:
    from backend import main as app_main

    request = SimpleNamespace(
        state=SimpleNamespace(view_rate_limit=("limit", ["key", "scope"]))
    )

    class FakeStorageLimiter:
        @staticmethod
        def get_window_stats(limit, *args):
            assert limit == "limit"
            assert args == ("key", "scope")
            return (125.2, 0)

    monkeypatch.setattr(app_main.limiter, "_limiter", FakeStorageLimiter())
    monkeypatch.setattr(app_main.time, "time", lambda: 100.0)

    assert app_main._retry_after_seconds(request) == 26


def test_retry_after_falls_back_when_window_stats_unavailable(monkeypatch) -> None:
    from backend import main as app_main

    request = SimpleNamespace(
        state=SimpleNamespace(view_rate_limit=("limit", ["key", "scope"]))
    )

    class BrokenStorageLimiter:
        @staticmethod
        def get_window_stats(*_args):
            raise RuntimeError("storage unavailable")

    monkeypatch.setattr(app_main.limiter, "_limiter", BrokenStorageLimiter())

    assert (
        app_main._retry_after_seconds(request)
        == app_main.RATE_LIMIT_RETRY_AFTER_FALLBACK_SECONDS
    )


def test_revoke_rate_limited_per_tenant(
    tenant: TestClient, db_session: Session
) -> None:
    """21st revoke in an hour returns 429."""
    from backend.core.limiter import set_owner_jwt_rate_limit_key_override

    token, _ = _create_tenant(tenant, db_session, "rev-rl@example.com")
    from backend.models import User
    user = db_session.query(User).filter(User.email == "rev-rl@example.com").first()
    user.role = "owner"
    db_session.commit()

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
