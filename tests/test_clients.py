"""Tests for client management API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.clients.service import ensure_client_for_user
from tests.conftest import register_and_verify_user


def test_create_client_success(client: TestClient, db_session: Session) -> None:
    """Create client returns 201 and 32-char api_key."""
    token = register_and_verify_user(client, db_session, email="user@example.com")
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Client"},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["name"] == "My Client"
    assert "api_key" in data
    assert len(data["api_key"]) == 32
    assert "created_at" in data
    assert "updated_at" in data


def test_create_client_duplicate(client: TestClient, db_session: Session) -> None:
    """Same user tries to create second client → 409."""
    token = register_and_verify_user(client, db_session, email="dup@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "First Client"},
    )
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Client"},
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_ensure_client_for_user_returns_existing_on_conflict(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_client_for_user should stay idempotent if create races with another request."""
    from backend.clients import service as clients_service
    from backend.core.security import hash_password
    from backend.models import User

    user = User(
        email="ensure-client@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    existing_client = clients_service.create_client(user.id, "Existing Client", db_session)
    lookup_calls = 0

    def fake_create_client(user_id, name, db):
        raise HTTPException(status_code=409, detail="Client already exists for this user")

    def fake_get_client_by_user(user_id, db):
        nonlocal lookup_calls
        lookup_calls += 1
        if user_id != user.id:
            return None
        return None if lookup_calls == 1 else existing_client

    monkeypatch.setattr(clients_service, "create_client", fake_create_client)
    monkeypatch.setattr(clients_service, "get_client_by_user", fake_get_client_by_user)

    resolved = ensure_client_for_user(user.id, db_session)
    assert resolved.id == existing_client.id
    assert lookup_calls == 2


def test_create_client_unauthenticated(client: TestClient) -> None:
    """No JWT → 401."""
    response = client.post(
        "/clients",
        json={"name": "My Client"},
    )
    assert response.status_code == 401


def test_get_my_client_success(client: TestClient, db_session: Session) -> None:
    """Get own client after creation."""
    token = register_and_verify_user(client, db_session, email="me@example.com")
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Client"},
    )
    client_id = create_resp.json()["id"]
    response = client.get(
        "/clients/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == client_id
    assert data["name"] == "My Client"
    assert "api_key" in data


def test_get_my_client_not_found(client: TestClient, db_session: Session) -> None:
    """Get client before creating one → 404."""
    token = register_and_verify_user(client, db_session, email="noclient@example.com")
    response = client.get(
        "/clients/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_get_client_by_id_success(client: TestClient, db_session: Session) -> None:
    """Get client by UUID."""
    token = register_and_verify_user(client, db_session, email="byid@example.com")
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Client"},
    )
    client_id = create_resp.json()["id"]
    response = client.get(
        f"/clients/{client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == client_id
    assert response.json()["name"] == "Test Client"


def test_get_client_by_id_wrong_user(client: TestClient, db_session: Session) -> None:
    """User B tries to get user A's client → 404."""
    token_a = register_and_verify_user(client, db_session, email="userA@example.com")
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    client_id = create_resp.json()["id"]

    token_b = register_and_verify_user(client, db_session, email="userB@example.com")

    response = client.get(
        f"/clients/{client_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_delete_client_success(client: TestClient, db_session: Session) -> None:
    """Delete client → 204, verify gone."""
    token = register_and_verify_user(client, db_session, email="del@example.com")
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "To Delete"},
    )
    client_id = create_resp.json()["id"]

    response = client.delete(
        f"/clients/{client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 204

    get_resp = client.get(
        "/clients/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 404


def test_delete_client_wrong_user(client: TestClient, db_session: Session) -> None:
    """User B tries to delete user A's client → 404."""
    token_a = register_and_verify_user(client, db_session, email="delA@example.com")
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    client_id = create_resp.json()["id"]

    token_b = register_and_verify_user(client, db_session, email="delB@example.com")

    response = client.delete(
        f"/clients/{client_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_validate_api_key_valid(client: TestClient, db_session: Session) -> None:
    """Valid api_key → returns client_id and name."""
    token = register_and_verify_user(client, db_session, email="val@example.com")
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Validate Client"},
    )
    api_key = create_resp.json()["api_key"]
    client_id = create_resp.json()["id"]

    response = client.get(f"/clients/validate/{api_key}")
    assert response.status_code == 200
    data = response.json()
    assert data["client_id"] == str(client_id)
    assert data["name"] == "Validate Client"


def test_validate_api_key_invalid(client: TestClient) -> None:
    """Wrong key → 404."""
    response = client.get("/clients/validate/invalid-key-12345")
    assert response.status_code == 404


def test_api_key_is_32_chars(client: TestClient, db_session: Session) -> None:
    """Verify api_key length is 32 characters."""
    token = register_and_verify_user(client, db_session, email="len@example.com")
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Len Check"},
    )
    assert response.status_code == 201
    assert len(response.json()["api_key"]) == 32


def test_support_settings_default_falls_back_to_owner_email(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="owner-support@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Client"},
    )

    response = client.get(
        "/clients/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "fallback_email": "owner-support@example.com",
    }


def test_support_settings_put_and_get(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="support-put@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Put"},
    )

    response = client.put(
        "/clients/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "L2@Example.com"},
    )
    assert response.status_code == 200
    assert response.json()["l2_email"] == "l2@example.com"
    assert response.json()["fallback_email"] == "support-put@example.com"

    response = client.get(
        "/clients/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["l2_email"] == "l2@example.com"


def test_support_settings_reject_invalid_email(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="support-bad@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Support Bad"},
    )

    response = client.put(
        "/clients/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "not-an-email"},
    )
    assert response.status_code == 422
