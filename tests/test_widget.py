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
    monkeypatch.setattr("backend.widget.routes.async_localize_text_to_language_result", localize)

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

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="Hello from bootstrap",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message",
        _fake_async_process,
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

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="ok",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message",
        _fake_async_process,
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


def test_widget_chat_hints_session_increments_user_session_turns(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
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
    bot_public_id = _create_bot(tenant, token)
    _seed_rag_chunk(db_session, client_uuid)

    init_resp = tenant.post(
        "/widget/session/init",
        json={
            "bot_id": bot_public_id,
            "user_hints": {"user_id": "ext-42", "email": "user@example.com"},
        },
    )
    assert init_resp.status_code == 200
    assert init_resp.json()["mode"] == "hints"
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


def _setup_widget_tenant(tenant: TestClient, db_session: Session, email: str) -> str:
    """Register a verified user, create a tenant + bot, return bot public_id."""
    token = register_and_verify_user(tenant, db_session, email=email)
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Resume Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    return _create_bot(tenant, token)


def test_widget_session_init_resumes_open_session_for_identified_user(
    tenant: TestClient,
    db_session: Session,
) -> None:
    bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-resume@example.com")

    first = tenant.post(
        "/widget/session/init",
        json={"bot_id": bot_public_id, "user_hints": {"user_id": "ext-99"}},
    )
    assert first.status_code == 200
    assert first.json()["resumed"] is False
    first_session = first.json()["session_id"]

    # Same identified user, fresh "device" (no localStorage) → resume.
    second = tenant.post(
        "/widget/session/init",
        json={"bot_id": bot_public_id, "user_hints": {"user_id": "ext-99"}},
    )
    assert second.status_code == 200
    assert second.json()["resumed"] is True
    assert second.json()["session_id"] == first_session


def test_widget_session_init_new_session_when_last_ended(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from datetime import UTC, datetime

    bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-resume-ended@example.com")

    first = tenant.post(
        "/widget/session/init",
        json={"bot_id": bot_public_id, "user_hints": {"user_id": "ext-ended"}},
    )
    first_session = first.json()["session_id"]

    chat = db_session.query(Chat).filter(Chat.session_id == uuid.UUID(first_session)).one()
    chat.ended_at = datetime.now(UTC)
    db_session.commit()

    second = tenant.post(
        "/widget/session/init",
        json={"bot_id": bot_public_id, "user_hints": {"user_id": "ext-ended"}},
    )
    assert second.status_code == 200
    assert second.json()["resumed"] is False
    assert second.json()["session_id"] != first_session


def test_widget_session_init_anonymous_always_new(
    tenant: TestClient,
    db_session: Session,
) -> None:
    bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-anon@example.com")

    first = tenant.post("/widget/session/init", json={"bot_id": bot_public_id})
    second = tenant.post("/widget/session/init", json={"bot_id": bot_public_id})
    assert first.json()["mode"] == "anonymous"
    assert first.json()["resumed"] is False
    assert second.json()["resumed"] is False
    assert first.json()["session_id"] != second.json()["session_id"]


def test_widget_session_init_email_only_hint_never_resumes(
    tenant: TestClient,
    db_session: Session,
) -> None:
    # Email is too guessable to safely reattach over a public endpoint, so an
    # email-only hint always starts a fresh session even on repeat init.
    bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-resume-email@example.com")

    first = tenant.post(
        "/widget/session/init",
        json={"bot_id": bot_public_id, "user_hints": {"email": "visitor@example.com"}},
    )
    assert first.json()["resumed"] is False
    first_session = first.json()["session_id"]

    second = tenant.post(
        "/widget/session/init",
        json={"bot_id": bot_public_id, "user_hints": {"email": "visitor@example.com"}},
    )
    assert second.json()["resumed"] is False
    assert second.json()["session_id"] != first_session


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

    async def fake_process(*, stream_callback=None, session_id=None, **kwargs):
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
        "backend.widget.routes.async_process_chat_message",
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


def test_widget_stream_language_mismatch_aborts_before_client_sees_it(
    mock_openai_client,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong-language streamed answer is aborted by the language gate BEFORE
    any chunk reaches the SSE client; the forced-language retry is the only
    text streamed (task 86ey7x2p6 — no visible answer swap, no full double
    generation running to completion)."""
    import uuid as _uuid

    from backend.chat.handlers.rag import detect_language as _real_detect
    from backend.chat.language import LanguageDetectionResult
    from backend.chat.service import RetrievalContext
    from backend.search.service import build_reliability_assessment
    from tests._async_utils import as_async as _as_async

    bot_public_id = _setup_widget_tenant(
        tenant, db_session, "widget-lang-gate@example.com"
    )
    doc_id = _uuid.uuid4()

    def _fake_retrieve(*args, **kwargs) -> RetrievalContext:
        return RetrievalContext(
            chunk_texts=["Inline mode is available."],
            document_ids=[doc_id],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=1),
        )

    generate_calls: list[str | None] = []

    async def _fake_async_generate(question, context_chunks, **kwargs):
        lang = kwargs.get("response_language")
        generate_calls.append(lang)
        sc = kwargs.get("stream_callback")
        if len(generate_calls) == 1:
            # Wrong language (Dutch) — long enough to cross the gate threshold;
            # the gate raises out of this callback invocation.
            sc(
                "Ja, de inline modus is beschikbaar in de instellingen. "
                "Open het configuratiescherm en schakel de optie in."
            )
            raise AssertionError("gate must abort before the fake returns")
        sc("Sí, el modo inline está disponible.")
        return ("Sí, el modo inline está disponible.", 60, 40, 20, False)

    def _fake_detect(text: str) -> LanguageDetectionResult:
        if "beschikbaar" in text:
            return LanguageDetectionResult(
                detected_language="nl", confidence=0.95, is_reliable=True
            )
        if "disponible" in text or "¿" in text:
            return LanguageDetectionResult(
                detected_language="es", confidence=0.95, is_reliable=True
            )
        return _real_detect(text)

    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context", _as_async(_fake_retrieve)
    )
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer", _fake_async_generate
    )
    monkeypatch.setattr("backend.chat.handlers.rag.detect_language", _fake_detect)

    r = tenant.post(
        _widget_url(bot_public_id),
        json={"message": "¿Hay un modo inline disponible?"},
    )
    assert r.status_code == 200

    import json as _json

    chunk_texts: list[str] = []
    for raw in r.text.split("\n\n"):
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        event = _json.loads(raw[len("data:"):].strip())
        if event.get("type") == "chunk":
            chunk_texts.append(event["text"])

    streamed = "".join(chunk_texts)
    assert "beschikbaar" not in streamed, (
        "no wrong-language text may reach the client"
    )
    assert streamed == "Sí, el modo inline está disponible."
    assert len(generate_calls) == 2
    assert generate_calls[1] == "es", (
        f"retry must force the expected language, got {generate_calls[1]}"
    )


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

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="Which provider are you trying to configure?",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message",
        _fake_async_process,
    )

    r = _post_widget_chat(tenant, bot_public_id, message="How to connect domain?")
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Which provider are you trying to configure?"
    assert "message_type" not in data
    assert "clarification" not in data


# ---------------------------------------------------------------------------
# Conversation rotation (widget protocol)
# ---------------------------------------------------------------------------


def _setup_rotation_tenant(
    tenant: TestClient, db_session: Session, *, email: str, name: str
) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    return uuid.UUID(cl_resp.json()["id"]), bot_public_id


def _make_session_chat(
    db_session: Session,
    tenant_uuid: uuid.UUID,
    *,
    session_id: uuid.UUID,
    idle_minutes: int,
    messages: list[tuple[str, str]],
    **chat_fields,
) -> "Chat":
    from datetime import timedelta

    from backend.models.base import _utcnow
    from backend.models.enums import MessageRole

    created = _utcnow() - timedelta(minutes=idle_minutes + 5)
    last_activity = _utcnow() - timedelta(minutes=idle_minutes)
    chat = Chat(
        tenant_id=tenant_uuid,
        session_id=session_id,
        created_at=created,
        updated_at=last_activity,
        user_context={},
        **chat_fields,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    from backend.models import Message

    for offset, (role, content) in enumerate(messages):
        db_session.add(
            Message(
                chat_id=chat.id,
                role=MessageRole(role),
                content=content,
                created_at=created + timedelta(seconds=offset),
            )
        )
    db_session.commit()
    return chat


def test_widget_history_marks_conversation_boundaries(
    tenant: TestClient, db_session: Session
) -> None:
    tenant_uuid, bot_public_id = _setup_rotation_tenant(
        tenant, db_session, email="widget-rot-hist@example.com", name="Widget Rot Hist Co"
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=120,
        messages=[("user", "old question"), ("assistant", "old answer")],
    )
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=1,
        messages=[("user", "new question")],
    )

    r = tenant.get(f"/widget/history?bot_id={bot_public_id}&session_id={session_id}")

    assert r.status_code == 200
    data = r.json()
    assert [m["content"] for m in data["messages"]] == [
        "old question",
        "old answer",
        "new question",
    ]
    assert data["boundary_indices"] == [2]
    assert data["conversation_rotated"] is False


def test_widget_history_flags_pending_rotation(
    tenant: TestClient, db_session: Session
) -> None:
    tenant_uuid, bot_public_id = _setup_rotation_tenant(
        tenant, db_session, email="widget-rot-flag@example.com", name="Widget Rot Flag Co"
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=45,
        messages=[("user", "old question"), ("assistant", "old answer")],
    )

    r = tenant.get(f"/widget/history?bot_id={bot_public_id}&session_id={session_id}")

    assert r.status_code == 200
    data = r.json()
    assert data["conversation_rotated"] is True
    assert data["boundary_indices"] == []


def test_widget_history_greeting_only_idle_chat_does_not_flag_rotation(
    tenant: TestClient, db_session: Session
) -> None:
    # A conversation that only ever received the bootstrap greeting (no user
    # turn) must NOT signal rotation when idle: re-greeting it would churn
    # another empty greeting Chat + trace for a visitor who never engaged.
    tenant_uuid, bot_public_id = _setup_rotation_tenant(
        tenant,
        db_session,
        email="widget-rot-greetonly@example.com",
        name="Widget Rot GreetOnly Co",
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=45,
        messages=[("assistant", "Hi, how can I help?")],
    )

    r = tenant.get(f"/widget/history?bot_id={bot_public_id}&session_id={session_id}")

    assert r.status_code == 200
    data = r.json()
    assert data["conversation_rotated"] is False
    assert [m["content"] for m in data["messages"]] == ["Hi, how can I help?"]


def test_widget_history_rotated_closed_chat_is_not_ended(
    tenant: TestClient, db_session: Session
) -> None:
    # A closed conversation past the idle window must not lock the widget
    # input: the visitor is about to start a fresh conversation.
    from backend.models.base import _utcnow

    tenant_uuid, bot_public_id = _setup_rotation_tenant(
        tenant, db_session, email="widget-rot-closed@example.com", name="Widget Rot Closed Co"
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=45,
        messages=[("user", "old question")],
        ended_at=_utcnow(),
    )

    r = tenant.get(f"/widget/history?bot_id={bot_public_id}&session_id={session_id}")

    assert r.status_code == 200
    data = r.json()
    assert data["conversation_rotated"] is True
    assert data["chat_ended"] is False


def test_widget_chat_empty_message_allowed_when_rotation_pending(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The widget re-greets a returning visitor by POSTing an empty message
    # with the existing session; mid-conversation empty messages still 422.
    tenant_uuid, bot_public_id = _setup_rotation_tenant(
        tenant, db_session, email="widget-rot-greet@example.com", name="Widget Rot Greet Co"
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=45,
        messages=[("user", "old question")],
    )

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="Fresh greeting",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message",
        _fake_async_process,
    )

    r = _post_widget_chat(
        tenant, bot_public_id, message="", session_id=str(session_id)
    )
    assert r.status_code == 200
    assert r.json()["text"] == "Fresh greeting"


def test_widget_chat_closed_session_answers_when_rotation_pending(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Within the window a closed chat returns 409 session_closed (see
    # test_widget_chat_closed_session_returns_controlled_error); past the
    # idle threshold the same POST opens a fresh conversation instead.
    from backend.models.base import _utcnow

    tenant_uuid, bot_public_id = _setup_rotation_tenant(
        tenant, db_session, email="widget-rot-409@example.com", name="Widget Rot 409 Co"
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=45,
        messages=[("user", "old question")],
        ended_at=_utcnow(),
    )

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="Answer in a fresh conversation",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message",
        _fake_async_process,
    )

    r = _post_widget_chat(
        tenant, bot_public_id, message="hello again", session_id=str(session_id)
    )
    assert r.status_code == 200
    assert r.json()["text"] == "Answer in a fresh conversation"
