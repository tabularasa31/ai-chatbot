from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import ChatTurnOutcome
from backend.models import Chat, Tenant
from backend.widget.service import apply_identity_context_patch, sanitize_locale
from tests.conftest import register_and_verify_user, set_client_openai_key
from tests.test_widget import _create_bot, _seed_rag_chunk


def _create_widget_client(
    tenant: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> dict[str, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201
    set_client_openai_key(tenant, token)
    data = response.json()
    data["bot_public_id"] = _create_bot(tenant, token)
    return data


def _post_widget_chat(
    tenant: TestClient,
    bot_public_id: str,
    *,
    message: str | None = None,
    test_ip: str | None = None,
):
    headers = {}
    if test_ip is not None:
        headers["x-test-ip"] = test_ip
    json = None if message is None else {"message": message}
    return tenant.post(f"/widget/chat?bot_id={bot_public_id}", json=json, headers=headers)


# ---------------------------------------------------------------------------
# Message validation
# ---------------------------------------------------------------------------


def test_widget_chat_rejects_oversized_message(
    tenant: TestClient,
    db_session: Session,
) -> None:
    body = _create_widget_client(
        tenant, db_session,
        email="widget-hardening-too-long@example.com",
        name="Widget Hardening Too Long",
    )
    response = _post_widget_chat(tenant, body["bot_public_id"], message="x" * 5000)
    assert response.status_code == 413
    assert response.json()["detail"] == {"code": "message_too_long", "max_chars": 1000}


def test_widget_chat_rejects_empty_message(
    tenant: TestClient,
    db_session: Session,
) -> None:
    body = _create_widget_client(
        tenant, db_session,
        email="widget-hardening-empty@example.com",
        name="Widget Hardening Empty",
    )
    existing_chat = Chat(
        tenant_id=uuid.UUID(body["id"]),
        session_id=uuid.uuid4(),
        user_context={},
    )
    db_session.add(existing_chat)
    db_session.commit()
    response = tenant.post(
        f"/widget/chat?bot_id={body['bot_public_id']}&session_id={existing_chat.session_id}",
        json={"message": ""},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "message_required"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_per_client_ip_rate_limit(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    body = _create_widget_client(
        tenant, db_session,
        email="widget-hardening-tenant-ip@example.com",
        name="Widget Hardening Tenant IP",
    )
    _seed_rag_chunk(db_session, uuid.UUID(body["id"]))
    monkeypatch.setattr(
        "backend.routes.widget.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(text="ok", document_ids=[], tokens_used=0, chat_ended=False),
    )

    set_widget_public_rate_limit_key_override(
        lambda request: request.headers.get("x-test-ip", "127.0.0.1")
    )
    try:
        for _ in range(30):
            assert _post_widget_chat(tenant, body["bot_public_id"], message="hello", test_ip="198.51.100.1").status_code == 200
        assert _post_widget_chat(tenant, body["bot_public_id"], message="hello", test_ip="198.51.100.1").status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_session_init_rate_limit_lower(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    body = _create_widget_client(
        tenant, db_session,
        email="widget-hardening-init-rate@example.com",
        name="Widget Hardening Init Rate",
    )

    set_widget_public_rate_limit_key_override(
        lambda request: request.headers.get("x-test-ip", "127.0.0.1")
    )
    try:
        for _ in range(10):
            assert tenant.post(
                "/widget/session/init",
                json={"api_key": body["api_key"]},
                headers={"x-test-ip": "192.0.2.11"},
            ).status_code == 200
        assert tenant.post(
            "/widget/session/init",
            json={"api_key": body["api_key"]},
            headers={"x-test-ip": "192.0.2.11"},
        ).status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


# ---------------------------------------------------------------------------
# Identity context patch
# ---------------------------------------------------------------------------


def test_apply_patch_strips_unknown_keys_and_caps_email() -> None:
    result = apply_identity_context_patch({"user_id": "u1", "malicious": "x"}, {})
    assert result == {"user_id": "u1"}

    result = apply_identity_context_patch({"user_id": "u1"}, {"email": f"{'a' * 490}@example.com"})
    assert len(result["email"]) == 320


def test_apply_patch_sanitizes_browser_locale() -> None:
    accepted = apply_identity_context_patch({"user_id": "u1"}, {}, browser_locale=sanitize_locale("en-US"))
    rejected = apply_identity_context_patch(
        {"user_id": "u1"}, {}, browser_locale=sanitize_locale("not_a_locale; DROP TABLE")
    )
    assert accepted["browser_locale"] == "en-US"
    assert "browser_locale" not in rejected


@pytest.mark.parametrize("value", ["en", "en-US", "zh-Hant-CN", "pt-BR"])
def test_sanitize_locale_accepts_valid_tags(value: str) -> None:
    assert sanitize_locale(value) == value


@pytest.mark.parametrize("value", ['en"; ignore previous', "en; system=evil", "x" * 100])
def test_sanitize_locale_rejects_injection(value: str) -> None:
    assert sanitize_locale(value) is None


# ---------------------------------------------------------------------------
# Session init 404
# ---------------------------------------------------------------------------


def test_session_init_404_for_invalid_and_inactive(
    tenant: TestClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    response = tenant.post("/widget/session/init", json={"api_key": "ch_invalid"})
    assert response.status_code == 404

    body = _create_widget_client(
        tenant, db_session,
        email="widget-hardening-inactive@example.com",
        name="Widget Hardening Inactive",
    )
    client_row = db_session.query(Tenant).filter(Tenant.id == uuid.UUID(body["id"])).first()
    assert client_row is not None
    client_row.is_active = False
    db_session.commit()

    response = tenant.post("/widget/session/init", json={"api_key": body["api_key"]})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Trusted host
# ---------------------------------------------------------------------------


def test_trusted_host_rejects_bad_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import backend.core.config as config_module
    import backend.main as main_module

    default_client = TestClient(main_module.app)
    assert default_client.get("/health", headers={"host": "evil.com"}).status_code == 200

    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    importlib.reload(config_module)
    reloaded_main = importlib.reload(main_module)
    restricted_client = TestClient(reloaded_main.app)

    assert restricted_client.get("/health", headers={"host": "evil.com"}).status_code == 400
    assert restricted_client.get("/health", headers={"host": "api.example.com"}).status_code == 200

    monkeypatch.setenv("ALLOWED_HOSTS", "*")
    importlib.reload(config_module)
    importlib.reload(main_module)
