"""Tests for widget API key rotation (tenant_api_keys table).

Covers the contract documented in [Security] API key rotation:
  * one ACTIVE key per tenant on creation
  * rotate puts the old key into REVOKING with a grace window
  * widget continues to authenticate with the old key during grace
  * widget rejects the old key after the grace window
  * immediate revoke kills a key with no grace
  * cannot revoke the only remaining usable key
  * lookups go through key_hash, not plaintext
"""

from __future__ import annotations

import datetime as dt

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


def test_widget_init_works_with_initial_key(
    tenant: TestClient, db_session: Session
) -> None:
    _, plain = _create_tenant(tenant, db_session, "rot-widget@example.com")
    resp = tenant.post("/widget/session/init", json={"api_key": plain})
    # 200 on success, 404 if key not found, 403 if inactive — must be 200 here.
    assert resp.status_code == 200, resp.json()


def test_widget_rejects_unknown_key(tenant: TestClient) -> None:
    resp = tenant.post(
        "/widget/session/init",
        json={"api_key": "ck_deadbeef" + "0" * 24},
    )
    assert resp.status_code == 404


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
    r_old = tenant.post("/widget/session/init", json={"api_key": old})
    r_new = tenant.post("/widget/session/init", json={"api_key": new_plain})
    assert r_old.status_code == 200
    assert r_new.status_code == 200

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

    r_old_after = tenant.post("/widget/session/init", json={"api_key": old})
    assert r_old_after.status_code == 404


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

    r_old = tenant.post("/widget/session/init", json={"api_key": old})
    assert r_old.status_code == 404
    r_new = tenant.post("/widget/session/init", json={"api_key": new_plain})
    assert r_new.status_code == 200

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

    r_old = tenant.post("/widget/session/init", json={"api_key": old})
    assert r_old.status_code == 404


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


