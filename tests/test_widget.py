"""Tests for public widget routes (/widget/*)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import ChatTurnOutcome, ClarificationPayload, ClarificationOption
from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, UserSession
from tests.conftest import register_and_verify_user, set_client_openai_key


def _widget_url(public_id: str, message: str = "hello") -> str:
    from urllib.parse import quote

    return f"/widget/chat?message={quote(message)}&client_id={public_id}"


def _seed_rag_chunk(db_session: Session, client_uuid: uuid.UUID) -> None:
    """One ready document + embedding so RAG returns context (SQLite test path)."""
    doc = Document(
        client_id=client_uuid,
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
    client: TestClient,
    db_session: Session,
) -> None:
    """Happy path: public widget chat returns answer and session_id."""
    token = register_and_verify_user(client, db_session, email="widget-ok@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Ok Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    body = cl_resp.json()
    public_id = body["public_id"]
    client_uuid = uuid.UUID(body["id"])
    _seed_rag_chunk(db_session, client_uuid)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Widget says hi"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    r = client.post(_widget_url(public_id, message="widget support"))
    assert r.status_code == 200
    data = r.json()
    assert data["response"] == "Widget says hi"
    assert "session_id" in data
    assert data.get("chat_ended") is False


def test_widget_chat_rate_limit_429_after_20_requests_same_ip(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """
    With a fixed rate-limit key, request 21 in the same window returns 429.

    Default test `Limiter` key_func uses a fresh UUID per call, so limits never
    accumulate; widget uses `widget_public_rate_limit_key` with an override hook.
    """
    from backend.core.limiter import set_widget_public_rate_limit_key_override

    token = register_and_verify_user(client, db_session, email="widget-rl@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget RL Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    body = cl_resp.json()
    public_id = body["public_id"]
    client_uuid = uuid.UUID(body["id"])
    _seed_rag_chunk(db_session, client_uuid)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="ok"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=2)

    set_widget_public_rate_limit_key_override(lambda _r: "test-widget-rate-limit-ip")
    try:
        for i in range(20):
            r = client.post(_widget_url(public_id, message=f"widget support {i}"))
            assert r.status_code == 200, f"request {i + 1}: {r.status_code} {r.text}"

        r21 = client.post(_widget_url(public_id, message="widget support over-limit"))
        assert r21.status_code == 429
    finally:
        set_widget_public_rate_limit_key_override(None)


def test_widget_chat_unknown_public_id_404(client: TestClient) -> None:
    r = client.post("/widget/chat?message=hi&client_id=ch_doesnotexist000")
    assert r.status_code == 404


def test_widget_chat_invalid_session_id_returns_controlled_error(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="widget-invalid-session@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Invalid Session Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    public_id = cl_resp.json()["public_id"]

    r = client.post(
        f"/widget/chat?message=hello&client_id={public_id}&session_id=not-a-uuid"
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "session_invalid"


def test_widget_chat_missing_session_returns_controlled_error(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="widget-missing-session@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Missing Session Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    public_id = cl_resp.json()["public_id"]

    r = client.post(
        f"/widget/chat?message=hello&client_id={public_id}&session_id={uuid.uuid4()}"
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_not_found"


def test_widget_chat_foreign_session_returns_controlled_error(
    client: TestClient,
    db_session: Session,
) -> None:
    token_a = register_and_verify_user(client, db_session, email="widget-foreign-a@example.com")
    token_b = register_and_verify_user(client, db_session, email="widget-foreign-b@example.com")
    cl_resp_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Widget Foreign A"},
    )
    cl_resp_b = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Widget Foreign B"},
    )
    assert cl_resp_a.status_code == 201
    assert cl_resp_b.status_code == 201

    set_client_openai_key(client, token_a)
    set_client_openai_key(client, token_b)

    client_a_uuid = uuid.UUID(cl_resp_a.json()["id"])
    public_id_b = cl_resp_b.json()["public_id"]
    foreign_chat = Chat(
        client_id=client_a_uuid,
        session_id=uuid.uuid4(),
        user_context={},
    )
    db_session.add(foreign_chat)
    db_session.commit()

    r = client.post(
        f"/widget/chat?message=hello&client_id={public_id_b}&session_id={foreign_chat.session_id}"
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_forbidden"


def test_widget_chat_closed_session_returns_controlled_error(
    client: TestClient,
    db_session: Session,
) -> None:
    from datetime import datetime, timezone

    token = register_and_verify_user(client, db_session, email="widget-closed-session@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Closed Session Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)

    client_uuid = uuid.UUID(cl_resp.json()["id"])
    public_id = cl_resp.json()["public_id"]
    closed_chat = Chat(
        client_id=client_uuid,
        session_id=uuid.uuid4(),
        user_context={},
        ended_at=datetime.now(timezone.utc),
    )
    db_session.add(closed_chat)
    db_session.commit()

    r = client.post(
        f"/widget/chat?message=hello&client_id={public_id}&session_id={closed_chat.session_id}"
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_closed"


def test_widget_chat_identified_session_increments_user_session_turns(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.core.security import generate_kyc_token

    token = register_and_verify_user(client, db_session, email="widget-user-session-turns@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget User Session Turns Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    body = cl_resp.json()
    public_id = body["public_id"]
    client_uuid = uuid.UUID(body["id"])
    api_key = body["api_key"]
    _seed_rag_chunk(db_session, client_uuid)

    sk_resp = client.post(
        "/clients/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]
    identity_token = generate_kyc_token(
        {"user_id": "ext-42", "tenant_id": public_id, "email": "user@example.com"},
        secret_hex,
    )
    init_resp = client.post(
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

    r = client.post(
        f"/widget/chat?message=widget%20support&client_id={public_id}&session_id={session_id}"
    )
    assert r.status_code == 200

    row = (
        db_session.query(UserSession)
        .filter(UserSession.client_id == client_uuid, UserSession.user_id == "ext-42")
        .first()
    )
    assert row is not None
    assert row.conversation_turns == 1


def test_widget_chat_returns_structured_clarification_payload(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(client, db_session, email="widget-clarify@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Clarify Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(client, token)
    public_id = cl_resp.json()["public_id"]

    monkeypatch.setattr(
        "backend.routes.widget.process_chat_message",
        lambda *args, **kwargs: ChatTurnOutcome(
            text="Do you want to connect the domain to DNS, CDN, or SSL?",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
            message_type="clarification",
            clarification_reason="ambiguous_intent",
            clarification_type="disambiguation",
            clarification=ClarificationPayload(
                reason="ambiguous_intent",
                type="disambiguation",
                options=[
                    ClarificationOption(id="dns", label="DNS"),
                    ClarificationOption(id="cdn", label="CDN"),
                ],
                requested_fields=[],
                original_user_message="How to connect domain?",
                turn_index=1,
            ),
        ),
    )

    r = client.post(_widget_url(public_id, message="How to connect domain?"))
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "Do you want to connect the domain to DNS, CDN, or SSL?"
    assert data["response"] == data["text"]
    assert data["message_type"] == "clarification"
    assert data["clarification"]["reason"] == "ambiguous_intent"
    assert data["clarification"]["options"][0]["id"] == "dns"
