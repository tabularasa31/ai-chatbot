"""``GET /analytics/summary`` — the tenant-scoped rolling-window numbers.

Every value is computed on the fly from ``chats``/``messages``/
``escalation_tickets``; there is nothing persisted to fixture around besides
those rows, so each test seeds exactly the rows its assertions depend on.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import (
    Chat,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    TurnOutcome,
    User,
)
from backend.models.base import _utcnow
from tests.conftest import register_and_verify_user


class _Workspace:
    def __init__(self, token: str, tenant_id: uuid.UUID) -> None:
        self.token = token
        self.tenant_id = tenant_id

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _workspace(client: TestClient, db: Session, *, email: str, name: str) -> _Workspace:
    token = register_and_verify_user(client, db, email=email)
    resp = client.post("/tenants", headers={"Authorization": f"Bearer {token}"}, json={"name": name})
    assert resp.status_code == 201, resp.text
    return _Workspace(token, uuid.UUID(resp.json()["id"]))


def _chat(db: Session, tenant_id: uuid.UUID, **kwargs) -> Chat:
    chat = Chat(tenant_id=tenant_id, session_id=kwargs.pop("session_id", uuid.uuid4()), **kwargs)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _say(db: Session, chat: Chat, role: MessageRole, **kwargs) -> Message:
    message = Message(chat_id=chat.id, role=role, content="hi", **kwargs)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _ticket(db: Session, chat: Chat, *, created_at) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=chat.tenant_id,
        ticket_number=f"ESC-{uuid.uuid4().hex[:8]}",
        primary_question="Need a human",
        trigger=EscalationTrigger.user_request,
        chat_id=chat.id,
        session_id=chat.session_id,
        created_at=created_at,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def test_analytics_summary_exact_values(tenant: TestClient, db_session: Session) -> None:
    client = tenant
    ws = _workspace(client, db_session, email="owner@example.com", name="Acme")
    other = _workspace(client, db_session, email="other-owner@example.com", name="Other Co")

    now = _utcnow()
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=40)  # outside the default 30d window

    # Session A: 2 filtered + 1 answered assistant turns.
    chat_a = _chat(db_session, ws.tenant_id)
    for _ in range(3):
        _say(db_session, chat_a, MessageRole.user, created_at=recent)
    _say(db_session, chat_a, MessageRole.assistant, turn_outcome=TurnOutcome.filtered.value, created_at=recent)
    _say(db_session, chat_a, MessageRole.assistant, turn_outcome=TurnOutcome.filtered.value, created_at=recent)
    _say(db_session, chat_a, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=recent)
    _ticket(db_session, chat_a, created_at=recent)

    # Session B: 2 answered + 1 unanswered assistant turns.
    chat_b = _chat(db_session, ws.tenant_id)
    for _ in range(3):
        _say(db_session, chat_b, MessageRole.user, created_at=recent)
    _say(db_session, chat_b, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=recent)
    _say(db_session, chat_b, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=recent)
    _say(db_session, chat_b, MessageRole.assistant, turn_outcome=TurnOutcome.unanswered.value, created_at=recent)
    # A ticket outside the window must not count toward deflection.
    _ticket(db_session, chat_b, created_at=old)

    # Session C: 1 answered assistant turn.
    chat_c = _chat(db_session, ws.tenant_id)
    _say(db_session, chat_c, MessageRole.user, created_at=recent)
    _say(db_session, chat_c, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=recent)

    # Session D: entirely outside the window — must not count at all.
    chat_d = _chat(db_session, ws.tenant_id)
    _say(db_session, chat_d, MessageRole.user, created_at=old)
    _say(db_session, chat_d, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=old)

    # Another tenant's data — must never be counted.
    other_chat = _chat(db_session, other.tenant_id)
    _say(db_session, other_chat, MessageRole.user, created_at=recent)
    _say(db_session, other_chat, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=recent)
    _ticket(db_session, other_chat, created_at=recent)

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["period"] == "30d"
    assert body["messages"] == 7
    assert body["conversations"] == 3
    assert body["filtered"] == 2
    # answered = 4 of the 5 non-filtered assistant turns (7 - 2 filtered).
    assert body["answered_rate"] == pytest.approx(4 / 5)
    # 1 of the 3 conversations (session A) has an in-window ticket.
    assert body["deflection_rate"] == pytest.approx(1 - 1 / 3)


def test_analytics_summary_empty_tenant(tenant: TestClient, db_session: Session) -> None:
    client = tenant
    ws = _workspace(client, db_session, email="empty-owner@example.com", name="Empty Co")

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["messages"] == 0
    assert body["conversations"] == 0
    assert body["filtered"] == 0
    assert body["deflection_rate"] is None
    assert body["answered_rate"] is None


def test_analytics_summary_default_period_is_30d(tenant: TestClient, db_session: Session) -> None:
    client = tenant
    ws = _workspace(client, db_session, email="default-owner@example.com", name="Default Co")

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["period"] == "30d"


def test_analytics_summary_invalid_period_rejected(tenant: TestClient, db_session: Session) -> None:
    client = tenant
    ws = _workspace(client, db_session, email="invalid-owner@example.com", name="Invalid Co")

    resp = client.get("/analytics/summary", headers=ws.auth, params={"period": "14d"})
    assert resp.status_code == 400


def test_analytics_summary_unauthenticated_rejected(tenant: TestClient) -> None:
    client = tenant
    resp = client.get("/analytics/summary")
    assert resp.status_code == 401
