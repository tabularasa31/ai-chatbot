"""Tests for authentication API."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.core.security import ALGORITHM


def test_register_success(client: TestClient) -> None:
    """Register creates user — no JWT yet (email must be verified first)."""
    response = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" not in data
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


def test_login_success(client: TestClient, db_session) -> None:
    """Login with correct credentials and verified email returns token."""
    from tests.conftest import register_and_verify_user

    token = register_and_verify_user(client, db_session, email="login@example.com")
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


def test_get_me_authenticated(client: TestClient, db_session) -> None:
    """Protected route returns user when valid token provided."""
    from tests.conftest import register_and_verify_user

    token = register_and_verify_user(client, db_session, email="me@example.com")
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
        {
            "sub": "00000000-0000-0000-0000-000000000001",
            "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_token_expiration(client: TestClient, db_session) -> None:
    """Token expires after 24 hours (expires_in is 86400 seconds)."""
    from tests.conftest import register_and_verify_user
    register_and_verify_user(client, db_session, email="exp@example.com")
    from backend.models import User

    user = db_session.query(User).filter(User.email == "exp@example.com").first()
    assert user is not None
    response = client.post(
        "/auth/login",
        json={"email": "exp@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 24 * 60 * 60


def test_health(client: TestClient) -> None:
    """Health check endpoint returns ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_forgot_password_returns_same_message_for_existing_and_missing_email(
    client: TestClient,
) -> None:
    with patch("backend.auth.routes.send_email"):
        client.post(
            "/auth/register",
            json={"email": "forgot-existing@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        existing = client.post(
            "/auth/forgot-password",
            json={"email": "forgot-existing@example.com"},
        )
        missing = client.post(
            "/auth/forgot-password",
            json={"email": "forgot-missing@example.com"},
        )
    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json() == missing.json()


def test_forgot_password_creates_token_only_for_existing_user(
    client: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        client.post(
            "/auth/register",
            json={"email": "forgot-token@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        client.post("/auth/forgot-password", json={"email": "forgot-token@example.com"})
        client.post("/auth/forgot-password", json={"email": "does-not-exist@example.com"})

    existing_user = db_session.query(User).filter(User.email == "forgot-token@example.com").first()
    assert existing_user is not None
    assert existing_user.reset_password_token is not None
    assert existing_user.reset_password_expires_at is not None
    missing_user = db_session.query(User).filter(User.email == "does-not-exist@example.com").first()
    assert missing_user is None


def test_reset_password_success_updates_password_and_verifies_user(
    client: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        client.post(
            "/auth/register",
            json={"email": "reset-success@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        forgot = client.post("/auth/forgot-password", json={"email": "reset-success@example.com"})
    assert forgot.status_code == 200
    user = db_session.query(User).filter(User.email == "reset-success@example.com").first()
    assert user is not None
    token = user.reset_password_token
    assert token is not None

    reset = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "NewSecurePass1!"},
    )
    assert reset.status_code == 200

    db_session.refresh(user)
    assert user.reset_password_token is None
    assert user.reset_password_expires_at is None
    assert user.is_verified is True
    assert user.verification_token is None
    assert user.verification_expires_at is None

    login = client.post(
        "/auth/login",
        json={"email": "reset-success@example.com", "password": "NewSecurePass1!"},
    )
    assert login.status_code == 200


def test_reset_password_invalid_token_returns_400(client: TestClient) -> None:
    response = client.post(
        "/auth/reset-password",
        json={"token": "not-valid-token", "new_password": "NewSecurePass1!"},
    )
    assert response.status_code == 400


def test_reset_password_expired_token_returns_400(
    client: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        client.post(
            "/auth/register",
            json={"email": "reset-expired@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        client.post("/auth/forgot-password", json={"email": "reset-expired@example.com"})
    user = db_session.query(User).filter(User.email == "reset-expired@example.com").first()
    assert user is not None
    assert user.reset_password_token is not None
    user.reset_password_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/auth/reset-password",
        json={"token": user.reset_password_token, "new_password": "NewSecurePass1!"},
    )
    assert response.status_code == 400


def test_reset_password_token_cannot_be_reused(
    client: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        client.post(
            "/auth/register",
            json={"email": "reset-reuse@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        client.post("/auth/forgot-password", json={"email": "reset-reuse@example.com"})
    user = db_session.query(User).filter(User.email == "reset-reuse@example.com").first()
    assert user is not None
    token = user.reset_password_token
    assert token is not None

    r1 = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "NewSecurePass1!"},
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "AnotherPass1!"},
    )
    assert r2.status_code == 400
