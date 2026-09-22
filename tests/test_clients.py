"""Tests for tenant management API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.tenants.service import ensure_tenant_for_user
from tests.conftest import register_and_verify_user


def test_create_client_journey_success_then_duplicate_rejected(
    tenant: TestClient, db_session: Session
) -> None:
    """201 with a ck_-prefixed 35-char api_key on first create; a second
    tenant for the same user is rejected with 409."""
    token = register_and_verify_user(tenant, db_session, email="user@example.com")
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Tenant"},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["name"] == "My Tenant"
    assert data["api_key"].startswith("ck_")
    assert len(data["api_key"]) == 35
    assert "created_at" in data
    assert "updated_at" in data

    dup_response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Tenant"},
    )
    assert dup_response.status_code == 409
    assert "already exists" in dup_response.json()["detail"].lower()


def test_ensure_client_for_user_returns_existing_on_conflict(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_tenant_for_user should stay idempotent if create races with another request."""
    from backend.tenants import service as clients_service
    from backend.core.security import hash_password
    from backend.models import User

    user = User(
        email="ensure-tenant@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    existing_client, _ = clients_service.create_tenant(user.id, "Existing Tenant", db_session)
    lookup_calls = 0

    def fake_create_client(user_id, name, db):
        raise HTTPException(status_code=409, detail="Tenant already exists for this user")

    def fake_get_client_by_user(user_id, db):
        nonlocal lookup_calls
        lookup_calls += 1
        if user_id != user.id:
            return None
        return None if lookup_calls == 1 else existing_client

    monkeypatch.setattr(clients_service, "create_tenant", fake_create_client)
    monkeypatch.setattr(clients_service, "get_tenant_by_user", fake_get_client_by_user)

    resolved = ensure_tenant_for_user(user.id, db_session)
    assert resolved.id == existing_client.id
    assert lookup_calls == 2


def test_create_client_unauthenticated(tenant: TestClient) -> None:
    """No JWT → 401."""
    response = tenant.post(
        "/tenants",
        json={"name": "My Tenant"},
    )
    assert response.status_code == 401


def test_get_client_success_via_me_and_by_id(tenant: TestClient, db_session: Session) -> None:
    """Own tenant is fetchable both via /tenants/me and /tenants/{id}."""
    token = register_and_verify_user(tenant, db_session, email="me@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Tenant"},
    )
    tenant_id = create_resp.json()["id"]

    me_resp = tenant.get("/tenants/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["id"] == tenant_id
    assert me_data["name"] == "My Tenant"
    assert me_data.get("api_key_hint") and len(me_data["api_key_hint"]) == 4
    assert "api_key" not in me_data

    by_id_resp = tenant.get(
        f"/tenants/{tenant_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert by_id_resp.status_code == 200
    assert by_id_resp.json()["id"] == tenant_id
    assert by_id_resp.json()["name"] == "My Tenant"


def test_get_my_client_not_found(tenant: TestClient, db_session: Session) -> None:
    """Get tenant before creating one → 404."""
    token = register_and_verify_user(tenant, db_session, email="noclient@example.com")
    response = tenant.get(
        "/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_get_client_by_id_wrong_user(tenant: TestClient, db_session: Session) -> None:
    """User B tries to get user A's tenant → 404."""
    token_a = register_and_verify_user(tenant, db_session, email="userA@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Tenant"},
    )
    tenant_id = create_resp.json()["id"]

    token_b = register_and_verify_user(tenant, db_session, email="userB@example.com")

    response = tenant.get(
        f"/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_delete_client_success(tenant: TestClient, db_session: Session) -> None:
    """Delete tenant → 204, verify gone — the owner's account with it.

    Members go with the workspace (``delete_tenant``), so afterwards the
    token has no principal at all: 401, not 404. Leaving the account behind
    would strand it with ``tenant_id = NULL`` and permanently burn the
    address, since invites and registration both refuse an existing e-mail.
    """
    token = register_and_verify_user(tenant, db_session, email="del@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "To Delete"},
    )
    tenant_id = create_resp.json()["id"]

    response = tenant.delete(
        f"/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    get_resp = tenant.get(
        "/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 401


def test_delete_client_wrong_user(tenant: TestClient, db_session: Session) -> None:
    """User B tries to delete user A's tenant → 404."""
    token_a = register_and_verify_user(tenant, db_session, email="delA@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Tenant"},
    )
    tenant_id = create_resp.json()["id"]

    token_b = register_and_verify_user(tenant, db_session, email="delB@example.com")

    response = tenant.delete(
        f"/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_support_settings_journey_default_update_and_partial_preserve(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Support-settings lifecycle in one pass:
    - default falls back to the owner's email, unset fields absent (exclude_none)
    - PUT rejects an invalid l2_email
    - PUT persists l2_email + escalation_language, GET reflects it
    - a partial PUT (l2_email only) must not clear escalation_language
    """
    token = register_and_verify_user(tenant, db_session, email="owner-support@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Tenant"},
    )

    default_resp = tenant.get(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert default_resp.status_code == 200
    default_body = default_resp.json()
    assert default_body == {"fallback_email": "owner-support@example.com"}
    assert "l2_email" not in default_body, "unset l2_email must be absent (exclude_none)"
    assert "escalation_language" not in default_body

    invalid_resp = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "not-an-email"},
    )
    assert invalid_resp.status_code == 422

    put_resp = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "L2@Example.com", "escalation_language": "fr"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["l2_email"] == "l2@example.com"
    assert put_resp.json()["fallback_email"] == "owner-support@example.com"

    get_resp = tenant.get(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["l2_email"] == "l2@example.com"
    assert get_resp.json()["escalation_language"] == "fr"

    partial_resp = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "new-l2@example.com"},
    )
    assert partial_resp.status_code == 200
    assert partial_resp.json()["l2_email"] == "new-l2@example.com"
    assert partial_resp.json().get("escalation_language") == "fr", (
        "escalation_language must not be cleared by a partial PUT"
    )
