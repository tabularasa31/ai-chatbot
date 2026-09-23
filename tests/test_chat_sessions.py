"""Tests for chat session continuity, history, and session logs endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_chat_session_journey_continuity_new_session_and_history(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Guards:
    - two /chat turns with the same session_id land in the same Chat row
    - omitting session_id auto-generates a fresh UUID
    - GET /chat/history returns the persisted turns to the owning tenant
    - a different tenant's JWT cannot read this session's history (404)
    """
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(tenant, db_session, email="cont@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Cont Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    session_id = str(uuid.uuid4())

    doc = Document(
        tenant_id=tenant_id,
        filename="cont.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="chunk",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "Reply",
        total_tokens=5,
    )

    # Step 1: two turns with the same session_id land in the same Chat row.
    r1 = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "How do I get started?", "session_id": session_id},
    )
    assert r1.status_code == 200
    r2 = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q2", "session_id": session_id},
    )
    assert r2.status_code == 200

    chat = db_session.query(Chat).filter(
        Chat.session_id == uuid.UUID(session_id),
    ).first()
    assert chat is not None
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 4  # Q1, A1, Q2, A2

    # Step 2: omitting session_id auto-generates a fresh, distinct one.
    auto_resp = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hi"},
    )
    assert auto_resp.status_code == 200
    uuid.UUID(auto_resp.json()["session_id"])  # valid UUID

    # Step 3: the owning tenant reads back the session's full history.
    hist_resp = tenant.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hist_resp.status_code == 200
    data = hist_resp.json()
    assert data["session_id"] == session_id
    assert len(data["messages"]) == 4
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "How do I get started?"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Reply"

    # Step 4: a different tenant's JWT gets 404, not someone else's history.
    token_b = register_and_verify_user(tenant, db_session, email="userB@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )
    hist_resp_b = tenant.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert hist_resp_b.status_code == 404


def test_get_history_unauthenticated(tenant: TestClient) -> None:
    """No JWT → 401."""
    session_id = str(uuid.uuid4())
    response = tenant.get(f"/chat/history/{session_id}")
    assert response.status_code == 401


# --- Sessions / logs inbox endpoint tests ---


def test_get_sessions_journey_own_sessions_sorted_with_preview(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Guards:
    - GET /chat/sessions returns only sessions for the authenticated tenant
    - sessions are sorted by last_activity DESC
    - last_answer_preview is truncated to ~120 chars with "..." if longer
    """
    from backend.models import Chat, Message, MessageRole

    token_a = register_and_verify_user(
        tenant, db_session, email="sessions_a@example.com"
    )
    cl_a = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Tenant A"},
    )
    set_client_openai_key(tenant, token_a)
    client_id_a = uuid.UUID(cl_a.json()["id"])

    # chat1: oldest, with a long answer (preview-truncation case).
    chat1 = Chat(tenant_id=client_id_a, session_id=uuid.uuid4())
    # chat2: most recent (sort-order case).
    chat2 = Chat(tenant_id=client_id_a, session_id=uuid.uuid4())
    db_session.add_all([chat1, chat2])
    db_session.commit()
    db_session.refresh(chat1)
    db_session.refresh(chat2)

    long_answer = "x" * 150
    base_time = datetime.now(timezone.utc)
    m1 = Message(chat_id=chat1.id, role=MessageRole.user, content="Q1")
    m2 = Message(chat_id=chat1.id, role=MessageRole.assistant, content=long_answer)
    m3 = Message(chat_id=chat2.id, role=MessageRole.user, content="Q2")
    m4 = Message(chat_id=chat2.id, role=MessageRole.assistant, content="A2")
    db_session.add_all([m1, m2, m3, m4])
    db_session.commit()

    # Manually set created_at so chat2 is more recent.
    from sqlalchemy import update
    from backend.models import Message as MsgModel
    db_session.execute(
        update(MsgModel).where(MsgModel.id == m4.id).values(created_at=base_time + timedelta(hours=1))
    )
    db_session.execute(
        update(MsgModel).where(MsgModel.id == m2.id).values(created_at=base_time)
    )
    db_session.commit()

    # Create user B and tenant B with their own session, to prove isolation.
    token_b = register_and_verify_user(
        tenant, db_session, email="sessions_b@example.com"
    )
    cl_b = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )
    client_id_b = uuid.UUID(cl_b.json()["id"])
    chat_b = Chat(tenant_id=client_id_b, session_id=uuid.uuid4())
    db_session.add(chat_b)
    db_session.commit()

    resp = tenant.get("/chat/sessions", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert len(data["sessions"]) == 2  # excludes tenant B's session

    # chat2 (more recent) sorts first.
    assert data["sessions"][0]["session_id"] == str(chat2.session_id)
    assert data["sessions"][0]["last_question"] == "Q2"
    assert data["sessions"][0]["message_count"] == 2

    # chat1 sorts second, and its long answer is truncated.
    assert data["sessions"][1]["session_id"] == str(chat1.session_id)
    assert data["sessions"][1]["last_question"] == "Q1"
    preview = data["sessions"][1]["last_answer_preview"]
    assert preview is not None
    assert len(preview) <= 124  # 120 + "..."
    assert preview.endswith("...")


def test_get_session_logs_journey_full_unredacted_no_original_switch(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Guards:
    - GET /chat/logs/session/{id} returns the full message list, chronological,
      with no ``content_original`` field
    - the tenant reads back their own conversation exactly as written — an
      email address in the message is not redacted on this read path
      (redaction happens where text leaves the platform, not here; there is
      no separate "view originals" privilege)
    - the removed ``include_original`` query param is ignored, not honoured
    """
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(tenant, db_session, email="logs@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="email me at user@example.com")
    m2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="Hi there")
    db_session.add_all([m1, m2])
    db_session.commit()

    resp = tenant.get(
        f"/chat/logs/session/{chat.session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "email me at user@example.com"
    assert "content_original" not in data["messages"][0]
    assert data["messages"][0]["session_id"] == str(chat.session_id)
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Hi there"
    assert data["messages"][0]["created_at"] <= data["messages"][1]["created_at"]

    # Unknown query params are ignored; the response still carries no originals field.
    resp_with_param = tenant.get(
        f"/chat/logs/session/{chat.session_id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp_with_param.status_code == 200
    assert "content_original" not in resp_with_param.json()["messages"][0]


def test_delete_session_original_route_is_gone(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(tenant, db_session, email="logs-delete@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Delete Tenant"},
    )
    tenant_id = uuid.UUID(cl.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    resp = tenant.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_get_session_logs_404_wrong_client(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/logs/session/{id} returns 404 if session belongs to another tenant."""
    from backend.models import Chat, Message, MessageRole

    token_a = register_and_verify_user(tenant, db_session, email="logsa@example.com")
    cl_a = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Tenant A"},
    )
    client_id_a = uuid.UUID(cl_a.json()["id"])
    chat_a = Chat(tenant_id=client_id_a, session_id=uuid.uuid4())
    db_session.add(chat_a)
    db_session.commit()
    db_session.refresh(chat_a)
    m = Message(chat_id=chat_a.id, role=MessageRole.user, content="Secret")
    db_session.add(m)
    db_session.commit()

    token_b = register_and_verify_user(tenant, db_session, email="logsb@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )

    resp = tenant.get(
        f"/chat/logs/session/{chat_a.session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404


def test_get_session_logs_404_nonexistent(
    tenant: TestClient, db_session: Session
) -> None:
    """GET /chat/logs/session/{id} returns 404 for nonexistent session."""
    token = register_and_verify_user(tenant, db_session, email="logs404@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Tenant"},
    )
    fake_id = uuid.uuid4()
    resp = tenant.get(
        f"/chat/logs/session/{fake_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_get_sessions_requires_auth(tenant: TestClient) -> None:
    """GET /chat/sessions requires JWT."""
    resp = tenant.get("/chat/sessions")
    assert resp.status_code == 401


def test_get_session_logs_requires_auth(tenant: TestClient) -> None:
    """GET /chat/logs/session/{id} requires JWT."""
    resp = tenant.get(f"/chat/logs/session/{uuid.uuid4()}")
    assert resp.status_code == 401
