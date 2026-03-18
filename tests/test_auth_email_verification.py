"""Tests for email verification flow."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import User


@patch("backend.auth.routes.send_email")
def test_signup_sets_verification_token(
    mock_send_email: object,
    client: TestClient,
    db_session: Session,
) -> None:
    """Register creates user with is_verified=False and verification token."""
    response = client.post(
        "/auth/register",
        json={"email": "verify@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    mock_send_email.assert_called_once()

    user = db_session.query(User).filter(User.email == "verify@example.com").first()
    assert user is not None
    assert user.is_verified is False
    assert user.verification_token is not None
    assert user.verification_expires_at is not None
    assert user.verification_expires_at > dt.datetime.utcnow()


@patch("backend.auth.routes.send_email")
def test_verify_email_success(
    mock_send_email: object,
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify email with valid token sets is_verified and clears token."""
    from backend.core.security import hash_password

    token = "abc123validtoken"
    user = User(
        email="toverify@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
        verification_token=token,
        verification_expires_at=dt.datetime.utcnow() + dt.timedelta(days=1),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.post(
        "/auth/verify-email",
        json={"token": token},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    db_session.refresh(user)
    assert user.is_verified is True
    assert user.verification_token is None
    assert user.verification_expires_at is None


def test_verify_email_invalid_token(client: TestClient) -> None:
    """Verify email with non-existent token returns 400."""
    response = client.post(
        "/auth/verify-email",
        json={"token": "nonexistent-token-12345"},
    )
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower() or "expired" in response.json()["detail"].lower()


@patch("backend.auth.routes.send_email")
def test_verify_email_expired_token(
    mock_send_email: object,
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify email with expired token returns 400."""
    from backend.core.security import hash_password

    token = "expiredtoken123"
    user = User(
        email="expired@example.com",
        password_hash=hash_password("SecurePass1!"),
        is_verified=False,
        verification_token=token,
        verification_expires_at=dt.datetime.utcnow() - dt.timedelta(hours=1),
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/auth/verify-email",
        json={"token": token},
    )
    assert response.status_code == 400
    assert "invalid" in response.json()["detail"].lower() or "expired" in response.json()["detail"].lower()

    db_session.refresh(user)
    assert user.is_verified is False
    assert user.verification_token == token
