"""Tests for email verification flow."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.auth import routes as auth_routes
from backend.models import User


def test_signup_sets_verification_token(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register creates user with is_verified=False and verification token."""
    calls: list[tuple[str, str, str]] = []

    def fake_send_email(to: str, subject: str, body: str) -> None:
        calls.append((to, subject, body))

    monkeypatch.setattr(auth_routes, "send_email", fake_send_email)

    response = client.post(
        "/auth/register",
        json={"email": "verify@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == "verify@example.com"

    user = db_session.query(User).filter(User.email == "verify@example.com").first()
    assert user is not None
    assert user.is_verified is False
    assert user.verification_token is not None
    assert user.verification_expires_at is not None
    assert user.verification_expires_at > dt.datetime.utcnow()


def test_verify_email_success(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify email with valid token sets is_verified and clears token."""
    from backend.core.security import hash_password

    monkeypatch.setattr(auth_routes, "send_email", lambda *a, **k: None)

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


def test_verify_email_expired_token(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify email with expired token returns 400."""
    from backend.core.security import hash_password

    monkeypatch.setattr(auth_routes, "send_email", lambda *a, **k: None)

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
