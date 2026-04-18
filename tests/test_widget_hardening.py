from __future__ import annotations

import importlib
import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import ChatTurnOutcome
from backend.models import Client
from backend.widget.service import apply_identity_context_patch, sanitize_locale
from tests.conftest import register_and_verify_user, set_client_openai_key
from tests.test_widget import _seed_rag_chunk


def _create_widget_client(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> dict[str, str]:
    token = register_and_verify_user(client, db_session, email=email)
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201
    set_client_openai_key(client, token)
    return response.json()


def _post_widget_chat(
    client: TestClient,
    public_id: str,
    *,
    message: str | None = None,
    query_message: str | None = None,
    locale: str | None = None,
    session_id: str | None = None,
    test_ip: str | None = None,
):
    query = f"/widget/chat?client_id={public_id}"
    if query_message is not None:
        from urllib.parse import quote

        query += f"&message={quote(query_message)}"
    if locale is not None:
        from urllib.parse import quote

        query += f"&locale={quote(locale)}"
    if session_id is not None:
        query += f"&session_id={session_id}"

    headers = {}
    if test_ip is not None:
        headers["x-test-ip"] = test_ip

    json = None if message is None and locale is None else {"message": message, "locale": locale}
    return client.post(query, json=json, headers=headers)


def test_widget_chat_rejects_oversized_message(
    client: TestClient,
    db_session: Session,
) -> None:
    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-too-long@example.com",
        name="Widget Hardening Too Long",
    )

    response = _post_widget_chat(client, body["public_id"], message="x" * 5000)

    assert response.status_code == 413
    assert response.json()["detail"] == {
        "code": "message_too_long",
        "max_chars": 4000,
    }


def test_widget_chat_accepts_4000_chars_exactly(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-4000@example.com",
        name="Widget Hardening 4000",
    )
    _seed_rag_chunk(db_session, uuid.UUID(body["id"]))

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=3)

    response = _post_widget_chat(client, body["public_id"], message="x" * 4000)

    assert response.status_code == 200


def test_widget_chat_accepts_body_and_query_fallback(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-fallback@example.com",
        name="Widget Hardening Fallback",
    )
    _seed_rag_chunk(db_session, uuid.UUID(body["id"]))

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=3)

    body_response = _post_widget_chat(client, body["public_id"], message="hello from body")
    query_response = _post_widget_chat(
        client,
        body["public_id"],
        query_message="hello from query",
    )

    assert body_response.status_code == 200
    assert query_response.status_code == 200
    assert "widget_chat_legacy_query_params" in caplog.text


def test_widget_chat_rejects_empty_message(
    client: TestClient,
    db_session: Session,
) -> None:
    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-empty@example.com",
        name="Widget Hardening Empty",
    )

    response = _post_widget_chat(client, body["public_id"], message="")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "message_required"


def test_per_client_ip_rate_limit(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-client-ip@example.com",
        name="Widget Hardening Client IP",
    )
    _seed_rag_chunk(db_session, uuid.UUID(body["id"]))

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=2)
    monkeypatch.setattr(
        "backend.routes.widget.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="ok",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        ),
    )

    set_widget_public_rate_limit_key_override(
        lambda request: request.headers.get("x-test-ip", "127.0.0.1")
    )
    try:
        for _ in range(30):
            response = _post_widget_chat(
                client,
                body["public_id"],
                message="hello",
                test_ip="198.51.100.1",
            )
            assert response.status_code == 200

        limited = _post_widget_chat(
            client,
            body["public_id"],
            message="hello",
            test_ip="198.51.100.1",
        )

        assert limited.status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_global_per_client_rate_limit_with_rotating_ip(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-global-client@example.com",
        name="Widget Hardening Global Client",
    )
    _seed_rag_chunk(db_session, uuid.UUID(body["id"]))

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=2)
    monkeypatch.setattr(
        "backend.routes.widget.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="ok",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        ),
    )

    set_widget_public_rate_limit_key_override(
        lambda request: request.headers.get("x-test-ip", "127.0.0.1")
    )
    try:
        for i in range(120):
            response = _post_widget_chat(
                client,
                body["public_id"],
                message="hello",
                test_ip=f"198.51.100.{i}",
            )
            assert response.status_code == 200

        limited = _post_widget_chat(
            client,
            body["public_id"],
            message="hello",
            test_ip="198.51.100.250",
        )

        assert limited.status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_per_ip_independence_across_clients(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    first = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-client-a@example.com",
        name="Widget Hardening Client A",
    )
    second = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-client-b@example.com",
        name="Widget Hardening Client B",
    )
    _seed_rag_chunk(db_session, uuid.UUID(first["id"]))
    _seed_rag_chunk(db_session, uuid.UUID(second["id"]))

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=2)
    monkeypatch.setattr(
        "backend.routes.widget.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="ok",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        ),
    )

    set_widget_public_rate_limit_key_override(
        lambda request: request.headers.get("x-test-ip", "127.0.0.1")
    )
    try:
        for _ in range(30):
            response_a = _post_widget_chat(
                client,
                first["public_id"],
                message="hello",
                test_ip="203.0.113.10",
            )
            response_b = _post_widget_chat(
                client,
                second["public_id"],
                message="hello",
                test_ip="203.0.113.10",
            )
            assert response_a.status_code == 200
            assert response_b.status_code == 200
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_session_init_rate_limit_lower(
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-init-rate@example.com",
        name="Widget Hardening Init Rate",
    )

    set_widget_public_rate_limit_key_override(
        lambda request: request.headers.get("x-test-ip", "127.0.0.1")
    )
    try:
        for _ in range(10):
            response = client.post(
                "/widget/session/init",
                json={"api_key": body["api_key"]},
                headers={"x-test-ip": "192.0.2.11"},
            )
            assert response.status_code == 200

        limited = client.post(
            "/widget/session/init",
            json={"api_key": body["api_key"]},
            headers={"x-test-ip": "192.0.2.11"},
        )
        assert limited.status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_apply_patch_strips_unknown_keys() -> None:
    result = apply_identity_context_patch(
        {"user_id": "u1", "malicious": "x", "xss": "<script>"},
        {},
    )

    assert result == {"user_id": "u1"}


def test_apply_patch_caps_long_email() -> None:
    result = apply_identity_context_patch(
        {"user_id": "u1"},
        {"email": f"{'a' * 490}@example.com"},
    )

    assert len(result["email"]) == 320


def test_apply_patch_ignores_empty_fresh_fields() -> None:
    result = apply_identity_context_patch(
        {"user_id": "u1", "email": "kept@example.com"},
        {"email": "   "},
    )

    assert result["email"] == "kept@example.com"


def test_apply_patch_preserves_canonical_user_id() -> None:
    result = apply_identity_context_patch(
        {"user_id": "u1"},
        {"user_id": "u2", "name": "Alice"},
    )

    assert result["user_id"] == "u1"
    assert result["name"] == "Alice"


def test_apply_patch_sanitizes_browser_locale() -> None:
    accepted = apply_identity_context_patch(
        {"user_id": "u1"},
        {},
        browser_locale=sanitize_locale("en-US"),
    )
    rejected = apply_identity_context_patch(
        {"user_id": "u1"},
        {},
        browser_locale=sanitize_locale("not_a_locale; DROP TABLE"),
    )

    assert accepted["browser_locale"] == "en-US"
    assert "browser_locale" not in rejected


@pytest.mark.parametrize("value", ["en", "en-US", "zh-Hant-CN", "pt-BR"])
def test_sanitize_locale_accepts_valid_tags(value: str) -> None:
    assert sanitize_locale(value) == value


@pytest.mark.parametrize(
    "value",
    [
        'en"; ignore previous',
        "en; system=evil",
        "../../",
        "select * from users",
        "x" * 100,
    ],
)
def test_sanitize_locale_rejects_injection(value: str) -> None:
    assert sanitize_locale(value) is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_sanitize_locale_none_for_empty_or_none(value: str | None) -> None:
    assert sanitize_locale(value) is None


def test_session_init_404_for_invalid_key(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    response = client.post("/widget/session/init", json={"api_key": "ch_invalid"})

    assert response.status_code == 404
    assert "widget_session_init_rejected" in caplog.text
    assert any(
        record.message == "widget_session_init_rejected"
        and getattr(record, "reason", None) == "not_found"
        for record in caplog.records
    )


def test_session_init_404_for_inactive_client(
    client: TestClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    body = _create_widget_client(
        client,
        db_session,
        email="widget-hardening-inactive@example.com",
        name="Widget Hardening Inactive",
    )
    client_row = db_session.query(Client).filter(Client.id == uuid.UUID(body["id"])).first()
    assert client_row is not None
    client_row.is_active = False
    db_session.commit()

    response = client.post("/widget/session/init", json={"api_key": body["api_key"]})

    assert response.status_code == 404
    assert "widget_session_init_rejected" in caplog.text
    assert any(
        record.message == "widget_session_init_rejected"
        and getattr(record, "reason", None) == "inactive"
        for record in caplog.records
    )


def test_trusted_host_rejects_bad_host(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.core.config as config_module
    import backend.main as main_module

    default_client = TestClient(main_module.app)
    assert default_client.get("/health", headers={"host": "evil.com"}).status_code == 200

    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    importlib.reload(config_module)
    reloaded_main = importlib.reload(main_module)
    restricted_client = TestClient(reloaded_main.app)

    bad = restricted_client.get("/health", headers={"host": "evil.com"})
    ok = restricted_client.get("/health", headers={"host": "api.example.com"})

    assert bad.status_code == 400
    assert ok.status_code == 200

    monkeypatch.setenv("ALLOWED_HOSTS", "*")
    importlib.reload(config_module)
    importlib.reload(main_module)
