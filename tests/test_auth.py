"""Tests for authentication API."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.core.security import ALGORITHM


def test_register_success(tenant: TestClient) -> None:
    """Register creates user — no JWT yet (email must be verified first)."""
    response = tenant.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" not in data
    assert data["user"]["email"] == "user@example.com"
    assert "id" in data["user"]
    assert "created_at" in data["user"]


def test_register_invalid_email(tenant: TestClient) -> None:
    """Register rejects invalid email format."""
    response = tenant.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "SecurePass1!"},
    )
    assert response.status_code == 422


def test_register_weak_password(tenant: TestClient) -> None:
    """Register rejects weak password (no uppercase, number, special char)."""
    response = tenant.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "password"},
    )
    assert response.status_code == 422


def test_register_duplicate_email(tenant: TestClient) -> None:
    """Register rejects duplicate email with 409."""
    payload = {"email": "dup@example.com", "password": "SecurePass1!"}
    tenant.post("/auth/register", json=payload)
    response = tenant.post("/auth/register", json=payload)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"].lower()


def test_login_success(tenant: TestClient, db_session) -> None:
    """Login with correct credentials and verified email returns token."""
    from tests.conftest import register_and_verify_user

    register_and_verify_user(tenant, db_session, email="login@example.com")
    response = tenant.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["user"]["email"] == "login@example.com"


def test_login_wrong_password(tenant: TestClient) -> None:
    """Login rejects wrong password with 401."""
    tenant.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    response = tenant.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "WrongPass1!"},
    )
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower()


def test_login_user_not_found(tenant: TestClient) -> None:
    """Login rejects non-existent email with 401."""
    response = tenant.post(
        "/auth/login",
        json={"email": "nonexistent@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 401


def test_login_unverified_user_returns_403(tenant: TestClient) -> None:
    """Login with registered but unverified email returns 403."""
    tenant.post(
        "/auth/register",
        json={"email": "unverified@example.com", "password": "SecurePass1!"},
    )
    response = tenant.post(
        "/auth/login",
        json={"email": "unverified@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 403
    assert "verified" in response.json()["detail"].lower()


def test_get_me_authenticated(tenant: TestClient, db_session) -> None:
    """Protected route returns user when valid token provided."""
    from tests.conftest import register_and_verify_user

    token = register_and_verify_user(tenant, db_session, email="me@example.com")
    response = tenant.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_get_me_no_token(tenant: TestClient) -> None:
    """Protected route returns 401 when no Authorization header."""
    response = tenant.get("/auth/me")
    assert response.status_code == 401
    assert "missing" in response.json()["detail"].lower() or "invalid" in response.json()["detail"].lower()


def test_get_me_invalid_token(tenant: TestClient) -> None:
    """Protected route returns 401 when token is corrupted."""
    response = tenant.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token-here"},
    )
    assert response.status_code == 401


def test_get_me_expired_token(tenant: TestClient) -> None:
    """Protected route returns 401 when token is expired."""
    expired_token = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000001",
            "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    response = tenant.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_token_expiration(tenant: TestClient, db_session) -> None:
    """Token expires after 24 hours (expires_in is 86400 seconds)."""
    from tests.conftest import register_and_verify_user
    register_and_verify_user(tenant, db_session, email="exp@example.com")
    from backend.models import User

    user = db_session.query(User).filter(User.email == "exp@example.com").first()
    assert user is not None
    response = tenant.post(
        "/auth/login",
        json={"email": "exp@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 24 * 60 * 60


def test_health(tenant: TestClient) -> None:
    """Health check endpoint returns ok."""
    response = tenant.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_forgot_password_returns_same_message_for_existing_and_missing_email(
    tenant: TestClient,
) -> None:
    with patch("backend.auth.routes.send_email"):
        tenant.post(
            "/auth/register",
            json={"email": "forgot-existing@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        existing = tenant.post(
            "/auth/forgot-password",
            json={"email": "forgot-existing@example.com"},
        )
        missing = tenant.post(
            "/auth/forgot-password",
            json={"email": "forgot-missing@example.com"},
        )
    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json() == missing.json()


def test_forgot_password_creates_token_only_for_existing_user(
    tenant: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        tenant.post(
            "/auth/register",
            json={"email": "forgot-token@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        tenant.post("/auth/forgot-password", json={"email": "forgot-token@example.com"})
        tenant.post("/auth/forgot-password", json={"email": "does-not-exist@example.com"})

    existing_user = db_session.query(User).filter(User.email == "forgot-token@example.com").first()
    assert existing_user is not None
    assert existing_user.reset_password_token is not None
    assert existing_user.reset_password_expires_at is not None
    missing_user = db_session.query(User).filter(User.email == "does-not-exist@example.com").first()
    assert missing_user is None


def test_reset_password_success_updates_password_and_verifies_user(
    tenant: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        tenant.post(
            "/auth/register",
            json={"email": "reset-success@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        forgot = tenant.post("/auth/forgot-password", json={"email": "reset-success@example.com"})
    assert forgot.status_code == 200
    user = db_session.query(User).filter(User.email == "reset-success@example.com").first()
    assert user is not None
    token = user.reset_password_token
    assert token is not None

    reset = tenant.post(
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

    login = tenant.post(
        "/auth/login",
        json={"email": "reset-success@example.com", "password": "NewSecurePass1!"},
    )
    assert login.status_code == 200


def test_login_sets_httponly_cookie(tenant: TestClient, db_session) -> None:
    """Login sets an httpOnly chat9_token cookie alongside the JSON token."""
    from tests.conftest import register_and_verify_user

    register_and_verify_user(tenant, db_session, email="cookie@example.com")
    response = tenant.post(
        "/auth/login",
        json={"email": "cookie@example.com", "password": "SecurePass1!"},
    )
    assert response.status_code == 200
    assert "chat9_token" in response.cookies
    cookie_header = response.headers.get("set-cookie", "")
    assert "httponly" in cookie_header.lower()


def test_login_cookie_uses_configured_same_site_domain(
    tenant: TestClient,
    db_session,
    monkeypatch,
) -> None:
    """Production auth cookie can be scoped to the same-site parent domain."""
    from backend.core.config import settings
    from tests.conftest import register_and_verify_user

    monkeypatch.setattr(settings, "auth_cookie_domain", ".getchat9.live")
    monkeypatch.setattr(settings, "auth_cookie_samesite", "lax")
    monkeypatch.setattr(settings, "auth_cookie_secure", True)

    register_and_verify_user(tenant, db_session, email="same-site-cookie@example.com")
    response = tenant.post(
        "/auth/login",
        json={"email": "same-site-cookie@example.com", "password": "SecurePass1!"},
    )

    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "").lower()
    assert "chat9_token=" in cookie_header
    assert "domain=.getchat9.live" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "secure" in cookie_header
    assert "httponly" in cookie_header


def test_get_me_with_cookie_auth(tenant: TestClient, db_session) -> None:
    """Protected route accepts auth via httpOnly cookie instead of Authorization header."""
    from tests.conftest import register_and_verify_user

    register_and_verify_user(tenant, db_session, email="cookie-me@example.com")
    login = tenant.post(
        "/auth/login",
        json={"email": "cookie-me@example.com", "password": "SecurePass1!"},
    )
    assert login.status_code == 200
    cookie_token = login.cookies.get("chat9_token")
    assert cookie_token

    tenant.cookies.set("chat9_token", cookie_token)
    response = tenant.get("/auth/me")
    tenant.cookies.clear()

    assert response.status_code == 200
    assert response.json()["email"] == "cookie-me@example.com"


def test_logout_clears_cookie(tenant: TestClient) -> None:
    """POST /auth/logout returns 200 and clears the chat9_token cookie."""
    response = tenant.post("/auth/logout")
    assert response.status_code == 200
    cookie_header = response.headers.get("set-cookie", "")
    assert "chat9_token" in cookie_header
    assert "max-age=0" in cookie_header.lower() or 'expires=' in cookie_header.lower()


def test_logout_clears_configured_domain_and_host_cookie(
    tenant: TestClient,
    monkeypatch,
) -> None:
    """Logout clears both new parent-domain cookies and old host-only cookies."""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "auth_cookie_domain", ".getchat9.live")
    monkeypatch.setattr(settings, "auth_cookie_samesite", "lax")
    monkeypatch.setattr(settings, "auth_cookie_secure", True)

    response = tenant.post("/auth/logout")

    assert response.status_code == 200
    cookie_headers = [header.lower() for header in response.headers.get_list("set-cookie")]
    assert any("chat9_token=" in header and "domain=.getchat9.live" in header for header in cookie_headers)
    assert any("chat9_token=" in header and "domain=" not in header for header in cookie_headers)
    assert all("max-age=0" in header or "expires=" in header for header in cookie_headers)


def test_reset_password_invalid_token_returns_400(tenant: TestClient) -> None:
    response = tenant.post(
        "/auth/reset-password",
        json={"token": "not-valid-token", "new_password": "NewSecurePass1!"},
    )
    assert response.status_code == 400


def test_reset_password_expired_token_returns_400(
    tenant: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        tenant.post(
            "/auth/register",
            json={"email": "reset-expired@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        tenant.post("/auth/forgot-password", json={"email": "reset-expired@example.com"})
    user = db_session.query(User).filter(User.email == "reset-expired@example.com").first()
    assert user is not None
    assert user.reset_password_token is not None
    user.reset_password_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    db_session.add(user)
    db_session.commit()

    response = tenant.post(
        "/auth/reset-password",
        json={"token": user.reset_password_token, "new_password": "NewSecurePass1!"},
    )
    assert response.status_code == 400


def test_reset_password_token_cannot_be_reused(
    tenant: TestClient,
    db_session,
) -> None:
    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        tenant.post(
            "/auth/register",
            json={"email": "reset-reuse@example.com", "password": "SecurePass1!"},
        )

    with patch("backend.auth.routes.send_email"):
        tenant.post("/auth/forgot-password", json={"email": "reset-reuse@example.com"})
    user = db_session.query(User).filter(User.email == "reset-reuse@example.com").first()
    assert user is not None
    token = user.reset_password_token
    assert token is not None

    r1 = tenant.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "NewSecurePass1!"},
    )
    assert r1.status_code == 200

    r2 = tenant.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "AnotherPass1!"},
    )
    assert r2.status_code == 400
