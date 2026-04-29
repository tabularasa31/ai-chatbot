"""Tests for public widget routes (/widget/*)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import ChatTurnOutcome
from backend.models import Bot, Chat, ContactSession, Document, DocumentStatus, DocumentType, Embedding
from tests.conftest import register_and_verify_user, set_client_openai_key


def _chat_completion_response(content: str, *, total_tokens: int = 0) -> Mock:
    response = Mock()
    response.choices = [Mock(message=Mock(content=content))]
    response.usage = Mock(total_tokens=total_tokens)
    return response


def _valid_validation_response() -> Mock:
    return _chat_completion_response('{"is_valid": true, "confidence": 0.95, "reason": "grounded"}')


def _chat_stream_response(content: str, *, total_tokens: int = 0) -> list[Mock]:
    return [
        Mock(choices=[Mock(delta=Mock(content=content), finish_reason=None)], usage=None),
        Mock(choices=[], usage=Mock(total_tokens=total_tokens, prompt_tokens=0, completion_tokens=0)),
    ]


def _chat_completion_side_effect(answer: str, *, total_tokens: int = 0):
    def _side_effect(*args, **kwargs):
        messages = kwargs.get("messages") or []
        combined_prompt = "\n".join(str(message.get("content", "")) for message in messages if isinstance(message, dict))
        if "relevance classifier" in combined_prompt:
            return _chat_completion_response('{"relevant": true, "reason": "test"}')
        if "You are a fact-checker for a support chatbot." in combined_prompt:
            return _valid_validation_response()
        if kwargs.get("stream") is True:
            return _chat_stream_response(answer, total_tokens=total_tokens)
        return _chat_completion_response(answer, total_tokens=total_tokens)

    return _side_effect


def _create_bot(client: TestClient, token: str) -> str:
    """Create a default bot for the current user's tenant; return bot public_id."""
    resp = client.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Bot"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["public_id"]


def _widget_url(bot_public_id: str, *, locale: str | None = None) -> str:
    url = f"/widget/chat?bot_id={bot_public_id}"
    if locale:
        from urllib.parse import quote
        url += f"&locale={quote(locale)}"
    return url


def _parse_sse_response(raw_body: str) -> dict:
    """Collapse SSE frames from /widget/chat into a legacy-style JSON payload."""
    import json as _json

    chunks: list[str] = []
    payload: dict = {}
    for frame in raw_body.split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        data_line = "\n".join(
            line[len("data:"):].strip()
            for line in frame.splitlines()
            if line.startswith("data:")
        )
        if not data_line:
            continue
        try:
            event = _json.loads(data_line)
        except _json.JSONDecodeError:
            continue
        if event.get("type") == "chunk" and isinstance(event.get("text"), str):
            chunks.append(event["text"])
        elif event.get("type") == "done":
            payload["session_id"] = event.get("session_id")
            payload["chat_ended"] = event.get("chat_ended")
            text = event.get("text")
            payload["text"] = text if isinstance(text, str) else "".join(chunks)
        elif event.get("type") == "error":
            payload["detail"] = event.get("message")
    if "text" not in payload and chunks:
        payload["text"] = "".join(chunks)
    return payload


class _SSEResponse:
    """Thin wrapper letting tests call `.json()` on a streamed widget response."""

    def __init__(self, response) -> None:
        self._response = response
        self._decoded = _parse_sse_response(response.text) if response.status_code < 400 else None

    def __getattr__(self, item):
        return getattr(self._response, item)

    def json(self):
        if self._decoded is not None:
            return self._decoded
        return self._response.json()


def _post_widget_chat(
    tenant: TestClient,
    bot_public_id: str,
    *,
    message: str,
    session_id: str | None = None,
    locale: str | None = None,
) -> object:
    query = f"/widget/chat?bot_id={bot_public_id}"
    if session_id:
        query += f"&session_id={session_id}"
    resp = tenant.post(query, json={"message": message, "locale": locale})
    return _SSEResponse(resp)


def _seed_rag_chunk(db_session: Session, client_uuid: uuid.UUID) -> None:
    """One ready document + embedding so RAG returns context (SQLite test path)."""
    doc = Document(
        tenant_id=client_uuid,
        filename="widget.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="widget support content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="widget support content",
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db_session.commit()


def test_widget_chat_success(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Happy path: public widget chat returns answer and session_id."""
    token = register_and_verify_user(tenant, db_session, email="widget-ok@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Ok Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    body = cl_resp.json()
    client_uuid = uuid.UUID(body["id"])
    bot_public_id = _create_bot(tenant, token)
    _seed_rag_chunk(db_session, client_uuid)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "Widget says hi",
        total_tokens=5,
    )

    r = _post_widget_chat(tenant, bot_public_id, message="widget support")
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Widget says hi"
    assert "session_id" in data
    assert data.get("chat_ended") is False


def test_widget_config_returns_link_safety_settings(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-config@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Config Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    bot = db_session.query(Bot).filter(Bot.public_id == bot_public_id).one()
    bot.link_safety_enabled = True
    bot.allowed_domains = ["example.com"]
    db_session.commit()

    r = tenant.get(f"/widget/config?bot_id={bot_public_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["link_safety_enabled"] is True
    assert data["allowed_domains"] == ["example.com"]
    assert data["link_safety_labels"]["body"] == "You are going to {hostname}. Continue?"


def test_widget_config_skips_localization_when_link_safety_disabled(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-config-disabled@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Config Disabled Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    localize = Mock(side_effect=AssertionError("localization should not run when link safety is disabled"))
    monkeypatch.setattr("backend.widget.routes.localize_text_to_language_result", localize)

    r = tenant.get(f"/widget/config?bot_id={bot_public_id}&locale=ru-RU")
    assert r.status_code == 200
    data = r.json()
    assert data["link_safety_enabled"] is False
    assert data["link_safety_labels"]["title"] == "Open external link?"
    localize.assert_not_called()


def test_widget_chat_empty_message_returns_422(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-greeting@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Greeting Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    client_uuid = uuid.UUID(cl_resp.json()["id"])
    bot_public_id = _create_bot(tenant, token)
    existing_chat = Chat(
        tenant_id=client_uuid,
        session_id=uuid.uuid4(),
        user_context={},
    )
    db_session.add(existing_chat)
    db_session.commit()

    r = _post_widget_chat(
        tenant,
        bot_public_id,
        message="",
        session_id=str(existing_chat.session_id),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "message_required"


def test_widget_chat_empty_message_bootstraps_new_session(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-bootstrap@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Bootstrap Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    monkeypatch.setattr(
        "backend.widget.routes.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="Hello from bootstrap",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        ),
    )

    r = _post_widget_chat(tenant, bot_public_id, message="")
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Hello from bootstrap"
    assert data["session_id"]


def test_widget_chat_rate_limit_429_after_30_requests_same_client_and_ip(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """
    With a fixed rate-limit key, request 31 in the same window returns 429.
    """
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    token = register_and_verify_user(tenant, db_session, email="widget-rl@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget RL Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    body = cl_resp.json()
    client_uuid = uuid.UUID(body["id"])
    bot_public_id = _create_bot(tenant, token)
    _seed_rag_chunk(db_session, client_uuid)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=2)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "backend.widget.routes.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="ok",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        ),
    )

    set_widget_public_rate_limit_key_override(lambda _r: "test-widget-rate-limit-ip")
    try:
        for i in range(30):
            r = tenant.post(
                _widget_url(bot_public_id),
                json={"message": f"widget support {i}"},
            )
            assert r.status_code == 200, f"request {i + 1}: {r.status_code} {r.text}"

        r31 = tenant.post(
            _widget_url(bot_public_id),
            json={"message": "widget support over-limit"},
        )
        assert r31.status_code == 429
    finally:
        monkeypatch.undo()
        set_widget_public_rate_limit_key_override(None)


def test_widget_chat_unknown_bot_id_404(tenant: TestClient) -> None:
    r = tenant.post("/widget/chat?bot_id=doesnotexist00000000", json={"message": "hi"})
    assert r.status_code == 404


def test_widget_chat_invalid_session_id_returns_controlled_error(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-invalid-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Invalid Session Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}&session_id=not-a-uuid",
        json={"message": "hello"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "session_invalid"


def test_widget_chat_missing_session_returns_controlled_error(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-missing-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Missing Session Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}&session_id={uuid.uuid4()}",
        json={"message": "hello"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_not_found"


def test_widget_chat_foreign_session_id_returns_not_found(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token_a = register_and_verify_user(tenant, db_session, email="widget-foreign-a@example.com")
    token_b = register_and_verify_user(tenant, db_session, email="widget-foreign-b@example.com")
    cl_resp_a = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Widget Foreign A"},
    )
    cl_resp_b = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Widget Foreign B"},
    )
    assert cl_resp_a.status_code == 201
    assert cl_resp_b.status_code == 201

    set_client_openai_key(tenant, token_a)
    set_client_openai_key(tenant, token_b)

    client_a_uuid = uuid.UUID(cl_resp_a.json()["id"])
    bot_public_id_b = _create_bot(tenant, token_b)
    foreign_chat = Chat(
        tenant_id=client_a_uuid,
        session_id=uuid.uuid4(),
        user_context={},
    )
    db_session.add(foreign_chat)
    db_session.commit()

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id_b}&session_id={foreign_chat.session_id}",
        json={"message": "hello"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_not_found"


def test_widget_chat_same_tenant_other_bot_session_returns_not_found(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-same-tenant-bots@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Same Tenant Bots"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)

    tenant_uuid = uuid.UUID(cl_resp.json()["id"])
    bot_public_id_a = _create_bot(tenant, token)
    bot_public_id_b = _create_bot(tenant, token)
    bot_a = db_session.query(Bot).filter(Bot.public_id == bot_public_id_a).first()
    assert bot_a is not None
    foreign_chat = Chat(
        tenant_id=tenant_uuid,
        bot_id=bot_a.id,
        session_id=uuid.uuid4(),
        user_context={},
    )
    db_session.add(foreign_chat)
    db_session.commit()

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id_b}&session_id={foreign_chat.session_id}",
        json={"message": "hello"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_not_found"


def test_widget_chat_closed_session_returns_controlled_error(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from datetime import datetime, timezone

    token = register_and_verify_user(tenant, db_session, email="widget-closed-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Closed Session Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)

    client_uuid = uuid.UUID(cl_resp.json()["id"])
    bot_public_id = _create_bot(tenant, token)
    closed_chat = Chat(
        tenant_id=client_uuid,
        session_id=uuid.uuid4(),
        user_context={},
        ended_at=datetime.now(timezone.utc),
    )
    db_session.add(closed_chat)
    db_session.commit()

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}&session_id={closed_chat.session_id}",
        json={"message": "hello"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_closed"


def test_widget_chat_identified_session_increments_user_session_turns(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.core.security import generate_kyc_token

    token = register_and_verify_user(tenant, db_session, email="widget-user-session-turns@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget User Session Turns Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    body = cl_resp.json()
    client_uuid = uuid.UUID(body["id"])
    api_key = body["api_key"]
    bot_public_id = _create_bot(tenant, token)
    _seed_rag_chunk(db_session, client_uuid)

    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]
    identity_token = generate_kyc_token(
        {"user_id": "ext-42", "email": "user@example.com"},
        secret_hex,
    )
    init_resp = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": identity_token},
    )
    assert init_resp.status_code == 200
    session_id = init_resp.json()["session_id"]

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Widget says hi"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    r = _post_widget_chat(
        tenant,
        bot_public_id,
        message="widget support",
        session_id=session_id,
    )
    assert r.status_code == 200

    row = (
        db_session.query(ContactSession)
        .filter(ContactSession.tenant_id == client_uuid, ContactSession.contact_id == "ext-42")
        .first()
    )
    assert row is not None
    assert row.conversation_turns == 1


def test_widget_chat_stream_sse(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stream=true returns text/event-stream with chunk + done events."""
    token = register_and_verify_user(tenant, db_session, email="widget-stream@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Stream Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    def fake_process(*, stream_callback=None, session_id=None, **kwargs):
        if stream_callback is not None:
            for piece in ("Hello", ", ", "world!"):
                stream_callback(piece)
        return ChatTurnOutcome(
            text="Hello, world!",
            document_ids=[],
            tokens_used=3,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.process_chat_message",
        fake_process,
    )

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}",
        json={"message": "hi"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = []
    for raw in r.text.split("\n\n"):
        raw = raw.strip()
        if not raw or not raw.startswith("data:"):
            continue
        import json as _json
        events.append(_json.loads(raw[len("data:"):].strip()))

    chunk_events = [e for e in events if e.get("type") == "chunk"]
    done_events = [e for e in events if e.get("type") == "done"]

    assert chunk_events, "expected at least one chunk event"
    assert "".join(e["text"] for e in chunk_events) == "Hello, world!"
    assert len(done_events) == 1
    assert done_events[0]["session_id"]
    assert done_events[0]["chat_ended"] is False


def test_widget_chat_returns_plain_answer_payload(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="widget-clarify@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Clarify Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    monkeypatch.setattr(
        "backend.widget.routes.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="Which provider are you trying to configure?",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        ),
    )

    r = _post_widget_chat(tenant, bot_public_id, message="How to connect domain?")
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Which provider are you trying to configure?"
    assert "message_type" not in data
    assert "clarification" not in data
