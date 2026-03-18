"""Tests for admin metrics API."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Chat, Client, Document, DocumentStatus, DocumentType, Embedding, Message, MessageRole, User

from tests.conftest import register_and_verify_user


def test_admin_metrics_summary_requires_admin(client: TestClient, db_session: Session) -> None:
    """Non-admin JWT → 403. Admin JWT → 200."""
    non_admin = client.post(
        "/auth/register",
        json={"email": "nonadmin@example.com", "password": "SecurePass1!"},
    )
    token = non_admin.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Non Admin Client"},
    )

    resp = client.get(
        "/admin/metrics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    user = db_session.query(User).filter(User.email == "nonadmin@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = client.get(
        "/admin/metrics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_admin_metrics_clients_requires_admin(client: TestClient, db_session: Session) -> None:
    """Non-admin JWT → 403. Admin JWT → 200."""
    non_admin = client.post(
        "/auth/register",
        json={"email": "nonadmin2@example.com", "password": "SecurePass1!"},
    )
    token = non_admin.json()["token"]
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Non Admin Client 2"},
    )

    resp = client.get(
        "/admin/metrics/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    user = db_session.query(User).filter(User.email == "nonadmin2@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = client.get(
        "/admin/metrics/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_admin_metrics_summary_values(client: TestClient, db_session: Session) -> None:
    """Summary counts match created fixtures."""
    token = register_and_verify_user(client, db_session, email="admin@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Admin Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])

    user = db_session.query(User).filter(User.email == "admin@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    doc = Document(
        client_id=client_id,
        filename="test.md",
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
        metadata_json={},
    )
    db_session.add(emb)
    db_session.commit()

    sess_id = uuid.uuid4()
    chat = Chat(client_id=client_id, session_id=sess_id)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    chat.tokens_used = 250
    db_session.commit()

    msg1 = Message(chat_id=chat.id, role=MessageRole.user, content="Hi")
    msg2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="Hello")
    db_session.add_all([msg1, msg2])
    db_session.commit()

    resp = client.get(
        "/admin/metrics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_users"] >= 1
    assert data["total_clients"] >= 1
    assert data["active_clients"] >= 1
    assert data["total_documents"] >= 1
    assert data["total_chat_sessions"] >= 1
    assert data["total_messages_user"] >= 1
    assert data["total_messages_assistant"] >= 1
    assert data["total_tokens_chat"] == 250


def test_admin_metrics_clients_values(client: TestClient, db_session: Session) -> None:
    """Per-client row reflects correct counts."""
    token = register_and_verify_user(client, db_session, email="admin2@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Metrics Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])

    user = db_session.query(User).filter(User.email == "admin2@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    doc = Document(
        client_id=client_id,
        filename="test.md",
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
        metadata_json={},
    )
    db_session.add(emb)
    db_session.commit()

    sess_id = uuid.uuid4()
    chat = Chat(client_id=client_id, session_id=sess_id)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    chat.tokens_used = 180
    db_session.commit()

    msg1 = Message(chat_id=chat.id, role=MessageRole.user, content="Q")
    msg2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="A")
    db_session.add_all([msg1, msg2])
    db_session.commit()

    resp = client.get(
        "/admin/metrics/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    items = data["items"]
    our = next((i for i in items if i["client_id"] == str(client_id)), None)
    assert our is not None
    assert our["name"] == "Metrics Client"
    assert our["users_count"] >= 1
    assert our["documents_count"] >= 1
    assert our["embedded_documents_count"] >= 1
    assert our["chat_sessions_count"] >= 1
    assert our["messages_user_count"] >= 1
    assert our["messages_assistant_count"] >= 1
    assert our["tokens_used_chat"] == 180


def test_admin_metrics_clients_users_count_by_client_id(
    client: TestClient, db_session: Session
) -> None:
    """users_count reflects users with client_id, not just owner (user_id)."""
    token = register_and_verify_user(client, db_session, email="admin3@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Multi-User Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])

    user = db_session.query(User).filter(User.email == "admin3@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    # Owner has client_id set by create_client. Add a second user with same client_id.
    from backend.auth.service import hash_password

    user2 = User(
        email="member@example.com",
        password_hash=hash_password("SecurePass1!"),
        client_id=client_id,
    )
    db_session.add(user2)
    db_session.commit()

    resp = client.get(
        "/admin/metrics/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    our = next((i for i in items if i["client_id"] == str(client_id)), None)
    assert our is not None
    assert our["users_count"] == 2


def test_clients_me_includes_is_admin(client: TestClient, db_session: Session) -> None:
    """GET /clients/me returns is_admin from user."""
    token = register_and_verify_user(client, db_session, email="meadmin@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Me Admin Client"},
    )

    resp = client.get("/clients/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "is_admin" in data
    assert data["is_admin"] is False

    user = db_session.query(User).filter(User.email == "meadmin@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = client.get("/clients/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is True
