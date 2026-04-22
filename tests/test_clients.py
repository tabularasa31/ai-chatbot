"""Tests for tenant management API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.tenants.service import ensure_tenant_for_user
from tests.conftest import register_and_verify_user


def test_create_client_success(tenant: TestClient, db_session: Session) -> None:
    """Create tenant returns 201 and 32-char api_key."""
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
    assert "api_key" in data
    assert data["api_key"].startswith("ck_")
    assert len(data["api_key"]) == 35
    assert "created_at" in data
    assert "updated_at" in data


def test_create_client_duplicate(tenant: TestClient, db_session: Session) -> None:
    """Same user tries to create second tenant → 409."""
    token = register_and_verify_user(tenant, db_session, email="dup@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "First Tenant"},
    )
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Tenant"},
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


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

    existing_client = clients_service.create_tenant(user.id, "Existing Tenant", db_session)
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


def test_get_my_client_success(tenant: TestClient, db_session: Session) -> None:
    """Get own tenant after creation."""
    token = register_and_verify_user(tenant, db_session, email="me@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Tenant"},
    )
    tenant_id = create_resp.json()["id"]
    response = tenant.get(
        "/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == tenant_id
    assert data["name"] == "My Tenant"
    assert "api_key" in data


def test_get_my_client_not_found(tenant: TestClient, db_session: Session) -> None:
    """Get tenant before creating one → 404."""
    token = register_and_verify_user(tenant, db_session, email="noclient@example.com")
    response = tenant.get(
        "/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_get_client_by_id_success(tenant: TestClient, db_session: Session) -> None:
    """Get tenant by UUID."""
    token = register_and_verify_user(tenant, db_session, email="byid@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Tenant"},
    )
    tenant_id = create_resp.json()["id"]
    response = tenant.get(
        f"/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == tenant_id
    assert response.json()["name"] == "Test Tenant"


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
    """Delete tenant → 204, verify gone."""
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
    assert get_resp.status_code == 404


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


def test_validate_api_key_valid(tenant: TestClient, db_session: Session) -> None:
    """Valid api_key → returns tenant_id and name."""
    token = register_and_verify_user(tenant, db_session, email="val@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Validate Tenant"},
    )
    api_key = create_resp.json()["api_key"]
    tenant_id = create_resp.json()["id"]

    response = tenant.get(f"/tenants/validate/{api_key}")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == str(tenant_id)
    assert data["name"] == "Validate Tenant"


def test_validate_api_key_invalid(tenant: TestClient) -> None:
    """Wrong key → 404."""
    response = tenant.get("/tenants/validate/invalid-key-12345")
    assert response.status_code == 404


def test_api_key_is_ck_prefixed(tenant: TestClient, db_session: Session) -> None:
    """Verify api_key is ck_-prefixed, 35 chars total."""
    token = register_and_verify_user(tenant, db_session, email="len@example.com")
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Len Check"},
    )
    assert response.status_code == 201
    key = response.json()["api_key"]
    assert key.startswith("ck_")
    assert len(key) == 35


def test_support_settings_default_falls_back_to_owner_email(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="owner-support@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Tenant"},
    )

    response = tenant.get(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "fallback_email": "owner-support@example.com",
    }


def test_support_settings_put_and_get(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="support-put@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Put"},
    )

    response = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "L2@Example.com"},
    )
    assert response.status_code == 200
    assert response.json()["l2_email"] == "l2@example.com"
    assert response.json()["fallback_email"] == "support-put@example.com"

    response = tenant.get(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["l2_email"] == "l2@example.com"


def test_support_settings_reject_invalid_email(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="support-bad@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Bad"},
    )

    response = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "not-an-email"},
    )
    assert response.status_code == 422


def test_support_settings_null_fields_excluded_from_response(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Unset optional fields are NOT included in the response body (exclude_none=True).

    This documents the intentional API contract: tenants must not rely on
    ``l2_email`` or ``escalation_language`` being present as explicit null keys;
    absence means the field is unset.
    """
    token = register_and_verify_user(tenant, db_session, email="support-exclude@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Exclude None"},
    )

    response = tenant.get(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "l2_email" not in body, "unset l2_email must be absent (exclude_none)"
    assert "escalation_language" not in body, "unset escalation_language must be absent (exclude_none)"
    assert "fallback_email" in body


def test_support_settings_partial_put_preserves_escalation_language(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """PUT with only l2_email must not clear an existing escalation_language."""
    token = register_and_verify_user(tenant, db_session, email="support-partial@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Partial Put"},
    )

    # Set both fields first
    tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "l2@example.com", "escalation_language": "fr"},
    )

    # Update only l2_email — escalation_language must survive
    response = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "new-l2@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["l2_email"] == "new-l2@example.com"
    assert response.json().get("escalation_language") == "fr", (
        "escalation_language must not be cleared by a partial PUT"
    )
