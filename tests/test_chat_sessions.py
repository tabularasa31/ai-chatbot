"""Tests for chat session continuity, history, and session logs endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_chat_session_continuity(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Two messages with same session_id → same chat in DB."""
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
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A1"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    r1 = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q1", "session_id": session_id},
    )
    assert r1.status_code == 200

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A2"))
    ]
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


def test_chat_new_session_auto_generated(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """No session_id → auto-generated UUID returned."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="auto@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Auto Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="auto.md",
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
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Hi"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=3)

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hi"},
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    uuid.UUID(session_id)  # valid UUID


def test_get_history_success(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Get chat history after conversation."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="hist@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Hist Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="hist.md",
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

    chat_resp = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "How do I get started?"},
    )
    session_id = chat_resp.json()["session_id"]

    hist_resp = tenant.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hist_resp.status_code == 200
    data = hist_resp.json()
    assert data["session_id"] == session_id
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "How do I get started?"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Reply"


def test_get_history_wrong_user(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """User B tries to get user A's session → 404."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token_a = register_and_verify_user(tenant, db_session, email="userA@example.com")
    cl_a = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Tenant A"},
    )
    set_client_openai_key(tenant, token_a)
    api_key_a = cl_a.json()["api_key"]
    client_id_a = uuid.UUID(cl_a.json()["id"])
    session_id = str(uuid.uuid4())

    doc = Document(
        tenant_id=client_id_a,
        filename="a.md",
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
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=1)

    tenant.post(
        "/chat",
        headers={"X-API-Key": api_key_a},
        json={"question": "Hi", "session_id": session_id},
    )

    token_b = register_and_verify_user(tenant, db_session, email="userB@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )

    hist_resp = tenant.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert hist_resp.status_code == 404


def test_get_history_unauthenticated(tenant: TestClient) -> None:
    """No JWT → 401."""
    session_id = str(uuid.uuid4())
    response = tenant.get(f"/chat/history/{session_id}")
    assert response.status_code == 401


# --- Sessions / logs inbox endpoint tests ---


def test_get_sessions_returns_only_own_client_sessions(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/sessions returns only sessions for the authenticated tenant."""
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

    # Create chat + messages for tenant A
    chat_a = Chat(tenant_id=client_id_a, session_id=uuid.uuid4())
    db_session.add(chat_a)
    db_session.commit()
    db_session.refresh(chat_a)
    msg1 = Message(chat_id=chat_a.id, role=MessageRole.user, content="Q1")
    msg2 = Message(chat_id=chat_a.id, role=MessageRole.assistant, content="A1")
    db_session.add_all([msg1, msg2])
    db_session.commit()

    # Create user B and tenant B with their own session
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
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == str(chat_a.session_id)
    assert data["sessions"][0]["message_count"] == 2
    assert data["sessions"][0]["last_question"] == "Q1"
    assert data["sessions"][0]["last_answer_preview"] == "A1"


def test_get_sessions_sorted_by_last_activity_desc(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/sessions returns sessions sorted by last_activity DESC."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(
        tenant, db_session, email="sessions_sort@example.com"
    )
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Sort Tenant"},
    )
    tenant_id = uuid.UUID(cl.json()["id"])

    base_time = datetime.now(timezone.utc)
    chat1 = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    chat2 = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add_all([chat1, chat2])
    db_session.commit()
    db_session.refresh(chat1)
    db_session.refresh(chat2)

    m1 = Message(chat_id=chat1.id, role=MessageRole.user, content="Q1")
    m2 = Message(chat_id=chat1.id, role=MessageRole.assistant, content="A1")
    m3 = Message(chat_id=chat2.id, role=MessageRole.user, content="Q2")
    m4 = Message(chat_id=chat2.id, role=MessageRole.assistant, content="A2")
    db_session.add_all([m1, m2, m3, m4])
    db_session.commit()

    # Manually set created_at so chat2 is more recent
    from sqlalchemy import update
    from backend.models import Message as MsgModel
    db_session.execute(
        update(MsgModel).where(MsgModel.id == m4.id).values(created_at=base_time + timedelta(hours=1))
    )
    db_session.execute(
        update(MsgModel).where(MsgModel.id == m2.id).values(created_at=base_time)
    )
    db_session.commit()

    resp = tenant.get("/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 2
    # chat2 (more recent) should be first
    assert data["sessions"][0]["session_id"] == str(chat2.session_id)
    assert data["sessions"][0]["last_question"] == "Q2"
    assert data["sessions"][1]["session_id"] == str(chat1.session_id)
    assert data["sessions"][1]["last_question"] == "Q1"


def test_get_sessions_last_answer_preview_truncated(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """last_answer_preview is truncated to ~120 chars with ... if longer."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(
        tenant, db_session, email="sessions_preview@example.com"
    )
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Preview Tenant"},
    )
    tenant_id = uuid.UUID(cl.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    long_answer = "x" * 150
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="Q")
    m2 = Message(chat_id=chat.id, role=MessageRole.assistant, content=long_answer)
    db_session.add_all([m1, m2])
    db_session.commit()

    resp = tenant.get("/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    preview = data["sessions"][0]["last_answer_preview"]
    assert preview is not None
    assert len(preview) <= 124  # 120 + "..."
    assert preview.endswith("...")


def test_get_session_logs_success(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/logs/session/{id} returns full message list for valid session."""
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
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="Hello")
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
    assert data["messages"][0]["content"] == "Hello"
    assert data["messages"][0]["content_original"] is None
    assert data["messages"][0]["content_original_available"] is False
    assert data["messages"][0]["session_id"] == str(chat.session_id)
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Hi there"
    assert data["messages"][0]["created_at"] <= data["messages"][1]["created_at"]


def test_get_session_logs_can_include_original_for_authenticated_owner(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.chat.pii import redact
    from backend.core.crypto import encrypt_value
    from backend.models import Chat, Message, MessageRole, User

    token = register_and_verify_user(tenant, db_session, email="logs-original@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Original Tenant"},
    )
    tenant_id = uuid.UUID(cl.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    m1 = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content="email me at user@example.com",
        content_original_encrypted=encrypt_value("email me at user@example.com"),
        content_redacted=redact("email me at user@example.com").redacted_text,
    )
    db_session.add(m1)
    db_session.commit()
    user = db_session.query(User).filter_by(email="logs-original@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = tenant.get(
        f"/chat/logs/session/{chat.session_id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"][0]["content"] == "email me at [EMAIL]"
    assert data["messages"][0]["content_original"] == "email me at user@example.com"
    assert data["messages"][0]["content_original_available"] is True


def test_get_session_logs_include_original_requires_admin(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(tenant, db_session, email="logs-no-admin@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs No Admin Tenant"},
    )
    tenant_id = uuid.UUID(cl.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    db_session.add(Message(chat_id=chat.id, role=MessageRole.user, content="Hello"))
    db_session.commit()

    resp = tenant.get(
        f"/chat/logs/session/{chat.session_id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_delete_session_original_requires_admin_and_removes_original(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.chat.pii import redact
    from backend.core.crypto import encrypt_value
    from backend.models import Chat, Message, MessageRole, User

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
    msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content="[EMAIL]",
        content_original_encrypted=encrypt_value("user@example.com"),
        content_redacted=redact("user@example.com").redacted_text,
    )
    db_session.add(msg)
    db_session.commit()

    denied = tenant.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403

    user = db_session.query(User).filter_by(email="logs-delete@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = tenant.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 1

    db_session.refresh(msg)
    assert msg.content_original_encrypted is None
    assert msg.content == msg.content_redacted


def test_delete_session_original_clears_legacy_plaintext_when_redacted_missing(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.core.crypto import encrypt_value
    from backend.models import Chat, Message, MessageRole, User

    token = register_and_verify_user(tenant, db_session, email="logs-delete-empty@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Delete Empty Tenant"},
    )
    tenant_id = uuid.UUID(cl.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content="plaintext@example.com",
        content_original_encrypted=encrypt_value("plaintext@example.com"),
        content_redacted=None,
    )
    db_session.add(msg)
    db_session.commit()

    user = db_session.query(User).filter_by(email="logs-delete-empty@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = tenant.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db_session.refresh(msg)
    assert msg.content_original_encrypted is None
    assert msg.content == ""


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
