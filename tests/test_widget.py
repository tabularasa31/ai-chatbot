"""Tests for public widget routes (/widget/*)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import (
    ChatTurnOutcome,
)
from backend.models import Bot, Chat, ContactSession, Document, DocumentStatus, DocumentType, Embedding, Tenant
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


def _setup_widget_tenant(
    tenant: TestClient, db_session: Session, email: str, name: str = "Widget Co"
) -> tuple[uuid.UUID, str]:
    """Register a verified user, create a tenant + bot, return (tenant_id, bot_public_id)."""
    token = register_and_verify_user(tenant, db_session, email=email)
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    return uuid.UUID(cl_resp.json()["id"]), _create_bot(tenant, token)


def test_widget_chat_success(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Happy path: public widget chat returns answer and session_id."""
    client_uuid, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-ok@example.com")
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


def test_widget_config_link_safety_disabled_then_enabled(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/widget/config: with link safety disabled, labels use English
    defaults and localization is never invoked; once enabled, the response
    carries the tenant's allowed_domains and localized labels."""
    _, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-config@example.com")

    localize = Mock(side_effect=AssertionError("localization should not run when link safety is disabled"))
    monkeypatch.setattr("backend.widget.routes.async_localize_text_to_language_result", localize)

    disabled_resp = tenant.get(f"/widget/config?bot_id={bot_public_id}&locale=ru-RU")
    assert disabled_resp.status_code == 200
    disabled_data = disabled_resp.json()
    assert disabled_data["link_safety_enabled"] is False
    assert disabled_data["link_safety_labels"]["title"] == "Open external link?"
    localize.assert_not_called()
    monkeypatch.undo()

    bot = db_session.query(Bot).filter(Bot.public_id == bot_public_id).one()
    bot.link_safety_enabled = True
    bot.allowed_domains = ["example.com"]
    db_session.commit()

    enabled_resp = tenant.get(f"/widget/config?bot_id={bot_public_id}")
    assert enabled_resp.status_code == 200
    enabled_data = enabled_resp.json()
    assert enabled_data["link_safety_enabled"] is True
    assert enabled_data["allowed_domains"] == ["example.com"]
    assert enabled_data["link_safety_labels"]["body"] == "You are going to {hostname}. Continue?"


def test_widget_chat_empty_message_returns_422(
    tenant: TestClient,
    db_session: Session,
) -> None:
    client_uuid, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-greeting@example.com")
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
    _, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-bootstrap@example.com")

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="Hello from bootstrap",
            document_ids=[],
            tokens_used=0,
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

    client_uuid, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-rl@example.com")
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


@pytest.mark.parametrize(
    "kind,expected_status,expected_code",
    [
        pytest.param("malformed", 422, "session_invalid", id="malformed_session_id"),
        pytest.param("nonexistent", 409, "session_not_found", id="nonexistent_session"),
        pytest.param("foreign_tenant", 409, "session_not_found", id="foreign_tenant_session"),
        pytest.param("same_tenant_other_bot", 409, "session_not_found", id="same_tenant_other_bot_session"),
    ],
)
def test_widget_chat_session_id_validation(
    tenant: TestClient,
    db_session: Session,
    kind: str,
    expected_status: int,
    expected_code: str,
) -> None:
    """/widget/chat rejects a session_id that is malformed, unknown, or
    belongs to a different tenant/bot — never leaking another party's chat."""
    if kind == "malformed":
        _, bot_public_id = _setup_widget_tenant(tenant, db_session, f"widget-session-{kind}@example.com")
        session_id = "not-a-uuid"
    elif kind == "nonexistent":
        _, bot_public_id = _setup_widget_tenant(tenant, db_session, f"widget-session-{kind}@example.com")
        session_id = str(uuid.uuid4())
    elif kind == "foreign_tenant":
        _, bot_public_id = _setup_widget_tenant(tenant, db_session, f"widget-session-{kind}@example.com")
        other_tenant_uuid, _ = _setup_widget_tenant(
            tenant, db_session, f"widget-session-{kind}-owner@example.com"
        )
        foreign_chat = Chat(tenant_id=other_tenant_uuid, session_id=uuid.uuid4(), user_context={})
        db_session.add(foreign_chat)
        db_session.commit()
        session_id = str(foreign_chat.session_id)
    else:  # same_tenant_other_bot
        token = register_and_verify_user(tenant, db_session, email=f"widget-session-{kind}@example.com")
        cl_resp = tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Widget Same Tenant Bots"},
        )
        tenant_uuid = uuid.UUID(cl_resp.json()["id"])
        set_client_openai_key(tenant, token)
        bot_a_public_id = _create_bot(tenant, token)
        bot_public_id = _create_bot(tenant, token)
        bot_a = db_session.query(Bot).filter(Bot.public_id == bot_a_public_id).one()
        foreign_chat = Chat(
            tenant_id=tenant_uuid, bot_id=bot_a.id, session_id=uuid.uuid4(), user_context={}
        )
        db_session.add(foreign_chat)
        db_session.commit()
        session_id = str(foreign_chat.session_id)

    r = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}&session_id={session_id}",
        json={"message": "hello"},
    )
    assert r.status_code == expected_status
    assert r.json()["detail"]["code"] == expected_code


def test_widget_chat_hints_session_increments_user_session_turns(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    client_uuid, bot_public_id = _setup_widget_tenant(
        tenant, db_session, "widget-user-session-turns@example.com"
    )
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


@pytest.mark.parametrize(
    "hints,expected_mode,expect_resume",
    [
        pytest.param({"user_id": "ext-99"}, "hints", True, id="identified_user_resumes"),
        pytest.param(None, "anonymous", False, id="anonymous_always_new"),
        pytest.param({"email": "visitor@example.com"}, "hints", False, id="email_only_never_resumes"),
    ],
)
def test_widget_session_init_resume_modes(
    tenant: TestClient,
    db_session: Session,
    hints: dict | None,
    expected_mode: str,
    expect_resume: bool,
) -> None:
    """Session resume on repeat /widget/session/init depends on the hint
    kind: a stable user_id resumes the open session; no hints (anonymous)
    or an email-only hint (too guessable to safely reattach) always start
    a fresh one."""
    _, bot_public_id = _setup_widget_tenant(
        tenant, db_session, f"widget-resume-{expected_mode}-{expect_resume}@example.com"
    )
    payload = {"bot_id": bot_public_id}
    if hints is not None:
        payload["user_hints"] = hints

    first = tenant.post("/widget/session/init", json=payload)
    assert first.status_code == 200
    assert first.json()["mode"] == expected_mode
    assert first.json()["resumed"] is False
    first_session = first.json()["session_id"]

    second = tenant.post("/widget/session/init", json=payload)
    assert second.status_code == 200
    assert second.json()["resumed"] is expect_resume
    if expect_resume:
        assert second.json()["session_id"] == first_session
    else:
        assert second.json()["session_id"] != first_session


def test_widget_chat_stream_sse(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stream=true returns text/event-stream with chunk + done events."""
    _, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-stream@example.com")

    async def fake_process(*, stream_callback=None, session_id=None, **kwargs):
        if stream_callback is not None:
            for piece in ("Hello", ", ", "world!"):
                stream_callback(piece)
        return ChatTurnOutcome(
            text="Hello, world!",
            document_ids=[],
            tokens_used=3,
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

    from backend.chat.language import LanguageDetectionResult
    from backend.chat.language import detect_language as _real_detect
    from backend.chat.types import RetrievalContext
    from backend.search.service import build_reliability_assessment
    from tests._async_utils import as_async as _as_async

    _, bot_public_id = _setup_widget_tenant(
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
        return ("Sí, el modo inline está disponible.", 60, 40, 20, False, False)

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
        "backend.chat.steps.retrieval.async_retrieve_context", _as_async(_fake_retrieve)
    )
    monkeypatch.setattr(
        "backend.chat.steps.generate.async_generate_answer", _fake_async_generate
    )
    monkeypatch.setattr("backend.chat.streaming.detect_language", _fake_detect)
    monkeypatch.setattr("backend.chat.steps.generate.detect_language", _fake_detect)

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
    _, bot_public_id = _setup_widget_tenant(tenant, db_session, "widget-clarify@example.com")

    async def _fake_async_process(*args, **kwargs):
        return ChatTurnOutcome(
            text="Which provider are you trying to configure?",
            document_ids=[],
            tokens_used=0,
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


@pytest.fixture(autouse=True)
def _pin_idle_timeout(monkeypatch):
    """Pin the rotation threshold to 30 min for widget tests.

    The shipped default is now 7 days (a returning visitor keeps their
    conversation within a widget session instead of being re-greeted). 1800s
    was the prior default every test here was written against, so pinning it
    is a no-op for non-rotation tests while keeping the ``conversation_rotated``
    boundary tests exercising the mechanism at a fixed threshold.
    """
    from backend.core.config import settings

    monkeypatch.setattr(settings, "conversation_idle_timeout_seconds", 1800)


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


@pytest.mark.parametrize(
    "chats,expected",
    [
        pytest.param(
            [
                (120, [("user", "old question"), ("assistant", "old answer")], {}),
                (1, [("user", "new question")], {}),
            ],
            {
                "messages": ["old question", "old answer", "new question"],
                "boundary_indices": [2],
                "conversation_rotated": False,
            },
            id="recent_followup_no_rotation",
        ),
        pytest.param(
            [(45, [("user", "old question"), ("assistant", "old answer")], {})],
            {"conversation_rotated": True, "boundary_indices": []},
            id="idle_past_threshold_flags_rotation",
        ),
        pytest.param(
            [(45, [("assistant", "Hi, how can I help?")], {})],
            {
                "conversation_rotated": False,
                "messages": ["Hi, how can I help?"],
            },
            id="greeting_only_idle_does_not_flag_rotation",
        ),
    ],
)
def test_widget_history_rotation_flags(
    tenant: TestClient, db_session: Session, chats: list, expected: dict
) -> None:
    """/widget/history computes conversation_rotated / boundary_indices from
    idle time and message content, across the failure modes that decide
    whether a returning visitor is re-greeted."""
    tenant_uuid, bot_public_id = _setup_widget_tenant(
        tenant, db_session, f"widget-rot-hist-{expected.get('conversation_rotated')}@example.com"
    )
    session_id = uuid.uuid4()
    for idle_minutes, messages, extra_fields in chats:
        _make_session_chat(
            db_session,
            tenant_uuid,
            session_id=session_id,
            idle_minutes=idle_minutes,
            messages=messages,
            **extra_fields,
        )

    r = tenant.get(f"/widget/history?bot_id={bot_public_id}&session_id={session_id}")

    assert r.status_code == 200
    data = r.json()
    if "messages" in expected:
        assert [m["content"] for m in data["messages"]] == expected["messages"]
    assert data["conversation_rotated"] is expected["conversation_rotated"]
    if "boundary_indices" in expected:
        assert data["boundary_indices"] == expected["boundary_indices"]


@pytest.mark.parametrize(
    "mutate_tenant,expected_status",
    [
        pytest.param(
            lambda t: setattr(t, "openai_api_key", None),
            200,
            id="no_openai_key_still_returns_history",
        ),
        pytest.param(
            lambda t: setattr(t, "is_active", False),
            403,
            id="inactive_tenant_rejected",
        ),
    ],
)
def test_widget_history_uses_session_gate(
    tenant: TestClient,
    db_session: Session,
    mutate_tenant,
    expected_status: int,
) -> None:
    """History works without an OpenAI key, and rejects an inactive tenant with 403."""
    tenant_uuid, bot_public_id = _setup_widget_tenant(
        tenant, db_session, f"widget-hist-gate-{expected_status}@example.com"
    )
    session_id = uuid.uuid4()
    _make_session_chat(
        db_session,
        tenant_uuid,
        session_id=session_id,
        idle_minutes=1,
        messages=[("user", "hi")],
    )
    tenant_row = db_session.get(Tenant, tenant_uuid)
    mutate_tenant(tenant_row)
    db_session.commit()

    r = tenant.get(f"/widget/history?bot_id={bot_public_id}&session_id={session_id}")

    assert r.status_code == expected_status


def test_widget_chat_empty_message_allowed_when_rotation_pending(
    tenant: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The widget re-greets a returning visitor by POSTing an empty message
    # with the existing session; mid-conversation empty messages still 422.
    tenant_uuid, bot_public_id = _setup_widget_tenant(
        tenant, db_session, "widget-rot-greet@example.com"
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
