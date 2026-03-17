"""Tests for authentication API."""

from __future__ import annotations

import datetime as dt
import jwt
import pytest
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.core.security import ALGORITHM


def test_register_success(client: TestClient) -> None:
    """Register creates user and returns JWT token."""
    response = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["expires_in"] == 24 * 60 * 60
    assert data["user"]["email"] == "user@example.com"
    assert "id" in data["user"]
    assert "created_at" in data["user"]


def test_register_invalid_email(client: TestClient) -> None:
    """Register rejects invalid email format."""
    response = client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "SecurePass1!"},
    )
    assert response.status_code == 422


def test_register_weak_password(client: TestClient) -> None:
    """Register rejects weak password (no uppercase, number, special char)."""
    response = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "password"},
    )
    assert response.status_code == 422


def test_register_duplicate_email(client: TestClient) -> None:
    """Register rejects duplicate email with 409."""
    payload = {"email": "dup@example.com", "password": "SecurePass1!"}
    client.post("/auth/register", json=payload)
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"].lower()


def test_login_success(client: TestClient) -> None:
    """Login with correct credentials returns token."""
    client.post(
        "/auth/register",
        json={"email": "login@example.com", "password": "SecurePass1!"},
    )
    response = client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["user"]["email"] == "login@example.com"


def test_login_wrong_password(client: TestClient) -> None:
    """Login rejects wrong password with 401."""
    client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "WrongPass1!"},
    )
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower()


def test_login_user_not_found(client: TestClient) -> None:
    """Login rejects non-existent email with 401."""
    response = client.post(
        "/auth/login",
        json={"email": "nonexistent@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 401


def test_get_me_authenticated(client: TestClient) -> None:
    """Protected route returns user when valid token provided."""
    reg = client.post(
        "/auth/register",
        json={"email": "me@example.com", "password": "SecurePass1!"},
    )
    token = reg.json()["token"]
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_get_me_no_token(client: TestClient) -> None:
    """Protected route returns 401 when no Authorization header."""
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert "missing" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()


def test_get_me_invalid_token(client: TestClient) -> None:
    """Protected route returns 401 when token is corrupted."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token-here"},
    )
    assert response.status_code == 401


def test_get_me_expired_token(client: TestClient) -> None:
    """Protected route returns 401 when token is expired."""
    expired_token = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000001", "exp": dt.datetime.utcnow() - dt.timedelta(hours=1)},
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_token_expiration(client: TestClient) -> None:
    """Token expires after 24 hours (expires_in is 86400 seconds)."""
    response = client.post(
        "/auth/register",
        json={"email": "exp@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 24 * 60 * 60


def test_health(client: TestClient) -> None:
    """Health check endpoint returns ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
