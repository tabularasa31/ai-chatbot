"""Tests for client management API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_create_client_success(client: TestClient) -> None:
    """Create client returns 201 and 32-char api_key."""
    reg = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
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


def test_create_client_duplicate(client: TestClient) -> None:
    """Same user tries to create second client → 409."""
    reg = client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
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


def test_create_client_unauthenticated(client: TestClient) -> None:
    """No JWT → 401."""
    response = client.post(
        "/clients",
        json={"name": "My Client"},
    )
    assert response.status_code == 401


def test_get_my_client_success(client: TestClient) -> None:
    """Get own client after creation."""
    reg = client.post(
        "/auth/register",
        json={"email": "me@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
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


def test_get_my_client_not_found(client: TestClient) -> None:
    """Get client before creating one → 404."""
    reg = client.post(
        "/auth/register",
        json={"email": "noclient@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    response = client.get(
        "/clients/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_get_client_by_id_success(client: TestClient) -> None:
    """Get client by UUID."""
    reg = client.post(
        "/auth/register",
        json={"email": "byid@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
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


def test_get_client_by_id_wrong_user(client: TestClient) -> None:
    """User B tries to get user A's client → 404."""
    reg_a = client.post(
        "/auth/register",
        json={"email": "userA@example.com", "password": "SecurePass1!"},
    )
    token_a = reg_a.json()["token"]
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    client_id = create_resp.json()["id"]

    reg_b = client.post(
        "/auth/register",
        json={"email": "userB@example.com", "password": "SecurePass1!"},
    )
    token_b = reg_b.json()["token"]

    response = client.get(
        f"/clients/{client_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_delete_client_success(client: TestClient) -> None:
    """Delete client → 204, verify gone."""
    reg = client.post(
        "/auth/register",
        json={"email": "del@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
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


def test_delete_client_wrong_user(client: TestClient) -> None:
    """User B tries to delete user A's client → 404."""
    reg_a = client.post(
        "/auth/register",
        json={"email": "delA@example.com", "password": "SecurePass1!"},
    )
    token_a = reg_a.json()["token"]
    create_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "User A Client"},
    )
    client_id = create_resp.json()["id"]

    reg_b = client.post(
        "/auth/register",
        json={"email": "delB@example.com", "password": "SecurePass1!"},
    )
    token_b = reg_b.json()["token"]

    response = client.delete(
        f"/clients/{client_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


def test_validate_api_key_valid(client: TestClient) -> None:
    """Valid api_key → returns client_id and name."""
    reg = client.post(
        "/auth/register",
        json={"email": "val@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
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


def test_api_key_is_32_chars(client: TestClient) -> None:
    """Verify api_key length is 32 characters."""
    reg = client.post(
        "/auth/register",
        json={"email": "len@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Len Check"},
    )
    assert response.status_code == 201
    assert len(response.json()["api_key"]) == 32
