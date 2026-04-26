"""Tests for /chat/messages/{id}/feedback and /chat/bad-answers endpoints."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user


def test_set_message_feedback_success_up(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Can set feedback=up on assistant message."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(tenant, db_session, email="fbup@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="Answer")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = tenant.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "up"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback"] == "up"
    assert data["ideal_answer"] is None
    assert data["id"] == str(msg.id)


def test_set_message_feedback_success_down(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Can set feedback=down with ideal_answer on assistant message."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(tenant, db_session, email="fbdown@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Down Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="Bad answer")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = tenant.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down", "ideal_answer": "This is the ideal answer."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback"] == "down"
    assert data["ideal_answer"] == "This is the ideal answer."


def test_set_message_feedback_survives_gap_analyzer_sync_failure(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary feedback save should survive best-effort Gap Analyzer failure."""
    from backend.models import Chat, Message, MessageFeedback, MessageRole

    token = register_and_verify_user(tenant, db_session, email="fb-gap-fail@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Gap Fail Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="Bad answer")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    monkeypatch.setattr(
        "backend.chat.routes.record_gap_feedback_for_message",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("gap sync failed")),
    )

    resp = tenant.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down", "ideal_answer": "This is the ideal answer."},
    )
    assert resp.status_code == 200

    db_session.refresh(msg)
    assert msg.feedback == MessageFeedback.down
    assert msg.ideal_answer == "This is the ideal answer."


def test_set_message_feedback_rejects_user_message(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """400 if trying to set feedback on user message."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(tenant, db_session, email="fbuser@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb User Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.user, content="Question")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = tenant.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down"},
    )
    assert resp.status_code == 400
    assert "assistant" in resp.json()["detail"].lower()


def test_set_message_feedback_requires_auth(
    tenant: TestClient, db_session: Session
) -> None:
    """401 without JWT."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(tenant, db_session, email="fbauth@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Auth Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="A")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = tenant.post(
        f"/chat/messages/{msg.id}/feedback",
        json={"feedback": "up"},
    )
    assert resp.status_code == 401


def test_set_message_feedback_wrong_client(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """404 if trying to set feedback for message from another tenant."""
    from backend.models import Chat, Message, MessageRole

    token_a = register_and_verify_user(tenant, db_session, email="fbwca@example.com")
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
    msg = Message(chat_id=chat_a.id, role=MessageRole.assistant, content="A")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    token_b = register_and_verify_user(tenant, db_session, email="fbwcb@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )

    resp = tenant.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"feedback": "down"},
    )
    assert resp.status_code == 404


def test_list_bad_answers_empty(
    tenant: TestClient, db_session: Session
) -> None:
    """Return empty items for new tenant."""
    token = register_and_verify_user(tenant, db_session, email="badempty@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Tenant"},
    )
    resp = tenant.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 0


def test_list_bad_answers_returns_items_for_client(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Create chat with user & assistant messages, mark some as down, ensure /chat/bad-answers returns them."""
    from backend.models import Chat, Message, MessageFeedback, MessageRole

    token = register_and_verify_user(tenant, db_session, email="baditems@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bad Items Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="What is X?")
    m2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="Wrong answer", feedback=MessageFeedback.down)
    db_session.add_all([m1, m2])
    db_session.commit()
    db_session.refresh(m2)

    resp = tenant.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["message_id"] == str(m2.id)
    assert item["session_id"] == str(chat.session_id)
    assert item["question"] == "What is X?"
    assert item["answer"] == "Wrong answer"
    assert item["ideal_answer"] is None

    # Set ideal_answer
    tenant.post(
        f"/chat/messages/{m2.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down", "ideal_answer": "Correct answer."},
    )
    resp2 = tenant.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200
    assert resp2.json()["items"][0]["ideal_answer"] == "Correct answer."


def test_list_bad_answers_respects_client_isolation(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Messages from other tenants are not returned."""
    from backend.models import Chat, Message, MessageFeedback, MessageRole

    token_a = register_and_verify_user(tenant, db_session, email="badisoa@example.com")
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
    msg_a = Message(
        chat_id=chat_a.id,
        role=MessageRole.assistant,
        content="Bad A",
        feedback=MessageFeedback.down,
    )
    db_session.add(msg_a)
    db_session.commit()

    token_b = register_and_verify_user(tenant, db_session, email="badisob@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Tenant B"},
    )

    resp = tenant.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 0
