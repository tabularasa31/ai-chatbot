"""Tests for escalation flow: awaiting email, followup, manual escalate."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import ContactSession
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_chat_awaiting_email_valid_email_transitions_to_followup(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(tenant, db_session, email="await-valid@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Await Valid Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-await"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="Need human support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "reach me at user@example.com"},
    )
    assert response.status_code == 200

    db_session.refresh(chat)
    db_session.refresh(ticket)
    assert ticket.user_email == "user@example.com"
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is True


def test_chat_awaiting_email_invalid_keeps_waiting_ticket(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(tenant, db_session, email="await-invalid@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Await Invalid Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "my email is not provided"},
    )
    assert response.status_code == 200
    db_session.refresh(chat)
    db_session.refresh(ticket)
    assert chat.escalation_awaiting_ticket_id == ticket.id
    assert ticket.user_email is None


def test_chat_followup_no_ends_chat(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(tenant, db_session, email="follow-no@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow No Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: Mock(
            message_to_user="Understood, closing chat.",
            followup_decision="no",
            tokens_used=3,
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "no thanks"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is True
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert chat.ended_at is not None


def test_chat_followup_no_closes_active_user_session(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus
    from backend.contact_sessions.service import start_user_session

    token = register_and_verify_user(tenant, db_session, email="follow-no-user-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow No User Session Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-follow"},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    row = start_user_session(
        db_session,
        tenant_id=tenant_id,
        user_context={"user_id": "u-follow"},
    )
    assert row is not None
    db_session.commit()

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0002",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: Mock(
            message_to_user="Understood, closing chat.",
            followup_decision="no",
            tokens_used=3,
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "no thanks"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is True

    db_session.refresh(row)
    assert row.conversation_turns == 1
    assert row.session_ended_at is not None


def test_chat_followup_yes_keeps_user_session_open_and_increments_turns(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus
    from backend.contact_sessions.service import start_user_session

    token = register_and_verify_user(tenant, db_session, email="follow-yes-user-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow Yes User Session Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-follow-yes"},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    row = start_user_session(
        db_session,
        tenant_id=tenant_id,
        user_context={"user_id": "u-follow-yes"},
    )
    assert row is not None
    db_session.commit()

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0003",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: Mock(
            message_to_user="Understood, we will continue.",
            followup_decision="yes",
            tokens_used=3,
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "yes please continue"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is False

    db_session.refresh(chat)
    db_session.refresh(row)
    assert chat.escalation_followup_pending is False
    assert chat.ended_at is None
    assert row.conversation_turns == 1
    assert row.session_ended_at is None


def test_chat_followup_unclear_twice_falls_back_to_yes(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(tenant, db_session, email="follow-unclear@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow Unclear Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: Mock(
            message_to_user="Could you clarify?",
            followup_decision="unclear",
            tokens_used=2,
        ),
    )

    r1 = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "maybe"},
    )
    assert r1.status_code == 200
    assert r1.json()["chat_ended"] is False
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is True
    assert (chat.user_context or {}).get("escalation_followup_clarify") is True

    r2 = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "still not sure"},
    )
    assert r2.status_code == 200
    assert r2.json()["chat_ended"] is False
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert (chat.user_context or {}).get("escalation_followup_clarify") is None


def test_chat_when_already_closed_uses_closed_phase(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(tenant, db_session, email="closed@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Closed Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-closed"},
        ended_at=datetime.now(timezone.utc),
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: Mock(
            message_to_user="Chat already ended.",
            followup_decision=None,
            tokens_used=1,
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "hello again"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is True
    assert "Chat already ended" in response.json()["text"]
    rows = (
        db_session.query(ContactSession)
        .filter(ContactSession.tenant_id == tenant_id, ContactSession.contact_id == "u-closed")
        .all()
    )
    assert rows == []


def test_anonymous_chat_does_not_create_contact_sessions(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="anon-user-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Anonymous User Session Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="anon.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="Anonymous answer",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Anonymous answer"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=20)

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200

    rows = db_session.query(ContactSession).filter(ContactSession.tenant_id == tenant_id).all()
    assert rows == []


def test_chat_succeeds_when_user_session_tracking_fails(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(tenant, db_session, email="tracking-failure@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Tracking Failure Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    doc = Document(
        tenant_id=tenant_id,
        filename="tracking.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="Tracked answer",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-track-fail"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "Tracked answer",
        total_tokens=25,
    )

    monkeypatch.setattr(
        "backend.chat.service.record_user_session_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tracking failed")),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "What is the answer?"},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Tracked answer"

    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 2


def test_contact_sessions_allow_only_one_active_row_per_contact(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from sqlalchemy.exc import IntegrityError

    token = register_and_verify_user(tenant, db_session, email="unique-user-session@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Unique User Session Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    db_session.add(ContactSession(tenant_id=tenant_id, contact_id="u-unique"))
    db_session.commit()

    db_session.add(ContactSession(tenant_id=tenant_id, contact_id="u-unique"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_manual_escalate_requires_api_key(tenant: TestClient) -> None:
    response = tenant.post(
        f"/chat/{uuid.uuid4()}/escalate",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 401


def test_manual_escalate_invalid_api_key(tenant: TestClient) -> None:
    response = tenant.post(
        f"/chat/{uuid.uuid4()}/escalate",
        headers={"X-API-Key": "bad-key"},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 401


def test_manual_escalate_without_openai_key_returns_400(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(tenant, db_session, email="manual-nokey@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual NoKey"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    response = tenant.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 400


def test_manual_escalate_missing_session_returns_404(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="manual-404@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual 404"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]

    response = tenant.post(
        f"/chat/{uuid.uuid4()}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 404


def test_manual_escalate_openai_error_returns_503(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat
    from openai import APIError

    token = register_and_verify_user(tenant, db_session, email="manual-503@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual 503"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    def _raise_api_error(*args, **kwargs):
        raise APIError("Service unavailable", request=Mock(), body=None)

    monkeypatch.setattr("backend.chat.routes.perform_manual_escalation", _raise_api_error)

    response = tenant.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 503


def test_manual_escalate_success_for_both_triggers(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(tenant, db_session, email="manual-success@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual Success"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.routes.perform_manual_escalation",
        lambda *args, **kwargs: ("Escalated.", "ESC-0009"),
    )

    r1 = tenant.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert r1.status_code == 200
    assert r1.json()["ticket_number"] == "ESC-0009"

    r2 = tenant.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "answer_rejected"},
    )
    assert r2.status_code == 200
    assert r2.json()["ticket_number"] == "ESC-0009"
