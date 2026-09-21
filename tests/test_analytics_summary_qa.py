"""QA supplement for ``GET /analytics/summary``.

Covers acceptance criteria not already exercised by
``tests/test_analytics_summary.py``:
  - 7d/90d window exactness and tight boundary exclusion
  - operator-role messages excluded from messages/answered_rate/filtered
  - deflection edge case: a ticket in-window with no in-window messages

Does not duplicate: 30d default, invalid period 400, empty tenant, cross
tenant isolation, unauthenticated 401 — see test_analytics_summary.py.
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


@pytest.mark.parametrize("period,days", [("7d", 7), ("90d", 90)])
def test_analytics_summary_window_exact_and_boundary(
    tenant: TestClient, db_session: Session, period: str, days: int
) -> None:
    """Rows just inside the window count; rows just outside are excluded."""
    client = tenant
    ws = _workspace(client, db_session, email=f"win-{period}@example.com", name=f"Win {period}")

    now = _utcnow()
    just_inside = now - timedelta(days=days) + timedelta(minutes=5)
    just_outside = now - timedelta(days=days) - timedelta(minutes=5)

    chat_in = _chat(db_session, ws.tenant_id)
    _say(db_session, chat_in, MessageRole.user, created_at=just_inside)
    _say(
        db_session,
        chat_in,
        MessageRole.assistant,
        turn_outcome=TurnOutcome.answered.value,
        created_at=just_inside,
    )

    chat_out = _chat(db_session, ws.tenant_id)
    _say(db_session, chat_out, MessageRole.user, created_at=just_outside)
    _say(
        db_session,
        chat_out,
        MessageRole.assistant,
        turn_outcome=TurnOutcome.answered.value,
        created_at=just_outside,
    )

    resp = client.get("/analytics/summary", headers=ws.auth, params={"period": period})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["period"] == period
    assert body["messages"] == 1
    assert body["conversations"] == 1
    assert body["answered_rate"] == pytest.approx(1.0)


def test_analytics_summary_operator_messages_excluded(
    tenant: TestClient, db_session: Session
) -> None:
    """Operator-authored turns must not count toward messages/answered_rate/filtered."""
    client = tenant
    ws = _workspace(client, db_session, email="operator-owner@example.com", name="Op Co")

    now = _utcnow()
    recent = now - timedelta(hours=1)

    chat = _chat(db_session, ws.tenant_id)
    _say(db_session, chat, MessageRole.user, created_at=recent)
    _say(
        db_session,
        chat,
        MessageRole.assistant,
        turn_outcome=TurnOutcome.answered.value,
        created_at=recent,
    )
    # An operator reply in the same window: must be invisible to every metric.
    _say(db_session, chat, MessageRole.operator, created_at=recent)
    _say(db_session, chat, MessageRole.user, created_at=recent)

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["messages"] == 2, "operator turn must not count as a user message"
    assert body["answered_rate"] == pytest.approx(1.0), (
        "operator turn must not appear in the answered_rate numerator or denominator"
    )
    assert body["filtered"] == 0


def test_analytics_summary_ticket_without_in_window_messages(
    tenant: TestClient, db_session: Session
) -> None:
    """Deflection edge case: a ticket whose session has no in-window message
    is excluded from both the conversation count and the escalated count."""
    client = tenant
    ws = _workspace(client, db_session, email="deflect-edge@example.com", name="Deflect Co")

    now = _utcnow()
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=40)

    chat = _chat(db_session, ws.tenant_id)
    _say(db_session, chat, MessageRole.user, created_at=old)
    _say(
        db_session,
        chat,
        MessageRole.assistant,
        turn_outcome=TurnOutcome.answered.value,
        created_at=old,
    )
    _ticket(db_session, chat, created_at=recent)

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The session contributes 0 to both `conversations` and the escalated
    # count, so the service short-circuits deflection_rate to None rather
    # than dividing.
    assert body["conversations"] == 0
    assert body["deflection_rate"] is None
