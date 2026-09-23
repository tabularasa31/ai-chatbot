"""Tests for authentication API."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.core.config import settings
from backend.core.security import ALGORITHM


@pytest.mark.parametrize(
    "payload,expected_status",
    [
        pytest.param(
            {"email": "not-an-email", "password": "SecurePass1!"}, 422, id="invalid_email"
        ),
        pytest.param(
            {"email": "user@example.com", "password": "password"}, 422, id="weak_password"
        ),
    ],
)
def test_register_rejects(tenant: TestClient, payload: dict, expected_status: int) -> None:
    response = tenant.post("/auth/register", json=payload)
    assert response.status_code == expected_status


def test_register_duplicate_email(tenant: TestClient) -> None:
    """Register rejects duplicate email with 409."""
    payload = {"email": "dup@example.com", "password": "SecurePass1!"}
    tenant.post("/auth/register", json=payload)
    response = tenant.post("/auth/register", json=payload)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"].lower()


def test_auth_journey_register_verify_login_me(tenant: TestClient, db_session) -> None:
    """register -> unverified login rejected -> verify -> login (JWT + cookie,
    claims, expiry) -> /auth/me via header and via cookie -> logout clears cookie.
    """
    from backend.models import User

    email = "journey@example.com"
    register_resp = tenant.post(
        "/auth/register", json={"email": email, "password": "SecurePass1!"}
    )
    assert register_resp.status_code == 200
    assert "token" not in register_resp.json()
    assert register_resp.json()["user"]["email"] == email

    unverified_login = tenant.post(
        "/auth/login", json={"email": email, "password": "SecurePass1!"}
    )
    assert unverified_login.status_code == 403
    assert "verified" in unverified_login.json()["detail"].lower()

    user = db_session.query(User).filter(User.email == email).first()
    user.is_verified = True
    db_session.commit()

    login = tenant.post("/auth/login", json={"email": email, "password": "SecurePass1!"})
    assert login.status_code == 200
    data = login.json()
    assert data["user"]["email"] == email
    assert data["expires_in"] == 24 * 60 * 60
    assert "chat9_token" in login.cookies
    assert "httponly" in login.headers.get("set-cookie", "").lower()

    payload = jwt.decode(data["token"], settings.jwt_secret, algorithms=["HS256"])
    assert payload["email"] == email
    assert payload["sub"]
    assert payload["typ"] == "chat9_user"

    me_via_header = tenant.get("/auth/me", headers={"Authorization": f"Bearer {data['token']}"})
    assert me_via_header.status_code == 200
    assert me_via_header.json()["email"] == email

    tenant.cookies.set("chat9_token", login.cookies.get("chat9_token"))
    me_via_cookie = tenant.get("/auth/me")
    tenant.cookies.clear()
    assert me_via_cookie.status_code == 200
    assert me_via_cookie.json()["email"] == email

    logout = tenant.post("/auth/logout")
    assert logout.status_code == 200
    cookie_header = logout.headers.get("set-cookie", "")
    assert "chat9_token" in cookie_header
    assert "max-age=0" in cookie_header.lower() or "expires=" in cookie_header.lower()


@pytest.mark.parametrize(
    "credentials,expected_status",
    [
        pytest.param(
            {"email": "user@example.com", "password": "WrongPass1!"}, 401, id="wrong_password"
        ),
        pytest.param(
            {"email": "nonexistent@example.com", "password": "SecurePass1!"},
            401,
            id="user_not_found",
        ),
    ],
)
def test_login_rejects(tenant: TestClient, credentials: dict, expected_status: int) -> None:
    tenant.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "SecurePass1!"},
    )
    response = tenant.post("/auth/login", json=credentials)
    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "make_headers,expected_status,expected_detail_fragment",
    [
        pytest.param(lambda: {}, 401, "missing|invalid", id="no_token"),
        pytest.param(
            lambda: {"Authorization": "Bearer invalid-token-here"}, 401, None, id="invalid_token"
        ),
        pytest.param(
            lambda: {
                "Authorization": "Bearer "
                + jwt.encode(
                    {
                        "sub": "00000000-0000-0000-0000-000000000001",
                        "exp": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
                    },
                    settings.jwt_secret,
                    algorithm=ALGORITHM,
                )
            },
            401,
            "expired",
            id="expired_token",
        ),
    ],
)
def test_get_me_rejects(
    tenant: TestClient, make_headers, expected_status: int, expected_detail_fragment: str | None
) -> None:
    response = tenant.get("/auth/me", headers=make_headers())
    assert response.status_code == expected_status
    if expected_detail_fragment:
        detail = response.json()["detail"].lower()
        assert any(part in detail for part in expected_detail_fragment.split("|"))


def test_cookie_domain_config_applies_to_login_and_logout(
    tenant: TestClient,
    db_session,
    monkeypatch,
) -> None:
    """Production auth cookie can be scoped to the same-site parent domain,
    both for login's Set-Cookie and for logout clearing both the new
    parent-domain cookie and the old host-only one."""
    from backend.core.config import settings
    from tests.conftest import register_and_verify_user

    monkeypatch.setattr(settings, "auth_cookie_domain", ".getchat9.live")
    monkeypatch.setattr(settings, "auth_cookie_samesite", "lax")
    monkeypatch.setattr(settings, "auth_cookie_secure", True)

    register_and_verify_user(tenant, db_session, email="same-site-cookie@example.com")
    login = tenant.post(
        "/auth/login",
        json={"email": "same-site-cookie@example.com", "password": "SecurePass1!"},
    )
    assert login.status_code == 200
    login_cookie_header = login.headers.get("set-cookie", "").lower()
    assert "chat9_token=" in login_cookie_header
    assert "domain=.getchat9.live" in login_cookie_header
    assert "samesite=lax" in login_cookie_header
    assert "secure" in login_cookie_header
    assert "httponly" in login_cookie_header

    logout = tenant.post("/auth/logout")
    assert logout.status_code == 200
    cookie_headers = [header.lower() for header in logout.headers.get_list("set-cookie")]
    assert any("chat9_token=" in header and "domain=.getchat9.live" in header for header in cookie_headers)
    assert any("chat9_token=" in header and "domain=" not in header for header in cookie_headers)
    assert all("max-age=0" in header or "expires=" in header for header in cookie_headers)


@pytest.mark.smoke
@pytest.mark.auth_reset
def test_forgot_password_hides_existence_and_scopes_token(
    tenant: TestClient,
    db_session,
) -> None:
    """Same response for an existing and a missing email; a reset token is
    created only for the account that actually exists."""
    from backend.models import User

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
            json={"email": "does-not-exist@example.com"},
        )
    assert existing.status_code == 200
    assert missing.status_code == 200
    assert existing.json() == missing.json()

    existing_user = db_session.query(User).filter(User.email == "forgot-existing@example.com").first()
    assert existing_user is not None
    assert existing_user.reset_password_token is not None
    assert existing_user.reset_password_expires_at is not None
    missing_user = db_session.query(User).filter(User.email == "does-not-exist@example.com").first()
    assert missing_user is None


@pytest.mark.smoke
@pytest.mark.auth_reset
def test_reset_password_success_then_reused_token_rejected(
    tenant: TestClient,
    db_session,
) -> None:
    """A valid reset token updates the password, verifies the user, clears
    verification state, allows login with the new password — and cannot be
    reused for a second reset (400)."""
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

    reused = tenant.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "AnotherPass1!"},
    )
    assert reused.status_code == 400


@pytest.mark.smoke
@pytest.mark.auth_reset
@pytest.mark.parametrize("kind", ["invalid_token", "expired_token"])
def test_reset_password_rejects_invalid_or_expired_token(
    tenant: TestClient, db_session, kind: str
) -> None:
    if kind == "invalid_token":
        response = tenant.post(
            "/auth/reset-password",
            json={"token": "not-valid-token", "new_password": "NewSecurePass1!"},
        )
        assert response.status_code == 400
        return

    from backend.models import User

    with patch("backend.auth.routes.send_email"):
        tenant.post(
            "/auth/register",
            json={"email": "reset-expired@example.com", "password": "SecurePass1!"},
        )
    with patch("backend.auth.routes.send_email"):
        tenant.post("/auth/forgot-password", json={"email": "reset-expired@example.com"})
    user = db_session.query(User).filter(User.email == "reset-expired@example.com").first()
    user.reset_password_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    db_session.add(user)
    db_session.commit()

    response = tenant.post(
        "/auth/reset-password",
        json={"token": user.reset_password_token, "new_password": "NewSecurePass1!"},
    )
    assert response.status_code == 400


def test_health(tenant: TestClient) -> None:
    """Health check endpoint returns ok and reports Redis status."""
    response = tenant.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["redis"] in {"ok", "unavailable", "disabled"}


@pytest.mark.parametrize("kind", ["expired_token", "unknown_token"])
def test_verify_email_rejects_expired_or_unknown_token(
    tenant: TestClient, db_session, kind: str
) -> None:
    import datetime as dt

    from backend.models import User

    if kind == "expired_token":
        resp = tenant.post(
            "/auth/register",
            json={"email": "verify-expired@example.com", "password": "SecurePass1!"},
        )
        assert resp.status_code == 200
        user = db_session.query(User).filter(User.email == "verify-expired@example.com").one()
        user.verification_expires_at = dt.datetime.utcnow() - dt.timedelta(hours=1)
        db_session.commit()
        token = user.verification_token
    else:
        token = "nonexistent-token-12345"
    resp = tenant.post("/auth/verify-email", json={"token": token})
    assert resp.status_code == 400
