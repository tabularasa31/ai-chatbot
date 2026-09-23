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
    """Default period is 30d; messages/conversations/filtered/answered_rate/
    deflection_rate all match hand-computed values, tickets and messages
    outside the window or belonging to another tenant never count."""
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


def test_analytics_summary_social_excluded_from_answered_rate(
    tenant: TestClient, db_session: Session
) -> None:
    client = tenant
    ws = _workspace(client, db_session, email="social-owner@example.com", name="Social Co")

    now = _utcnow()
    recent = now - timedelta(hours=1)

    chat = _chat(db_session, ws.tenant_id)
    _say(db_session, chat, MessageRole.user, created_at=recent)
    _say(db_session, chat, MessageRole.assistant, turn_outcome=TurnOutcome.answered.value, created_at=recent)
    for _ in range(3):
        _say(db_session, chat, MessageRole.assistant, turn_outcome=TurnOutcome.social.value, created_at=recent)

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # social rows must not inflate the denominator nor be counted answered.
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


def test_analytics_summary_deflection_rate_stays_within_bounds(
    tenant: TestClient, db_session: Session
) -> None:
    """Tickets whose session has no in-window message must not count toward
    deflection — otherwise the numerator can exceed the conversation-set
    denominator and push the rate outside [0, 1]."""
    client = tenant
    ws = _workspace(client, db_session, email="edge-owner@example.com", name="Edge Co")

    now = _utcnow()
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=40)

    # One real conversation in the window, no ticket.
    conversation_chat = _chat(db_session, ws.tenant_id)
    _say(db_session, conversation_chat, MessageRole.user, created_at=recent)

    # Two sessions with only old messages (outside the conversation set) but
    # tickets created inside the window.
    for _ in range(2):
        stale_chat = _chat(db_session, ws.tenant_id)
        _say(db_session, stale_chat, MessageRole.user, created_at=old)
        _ticket(db_session, stale_chat, created_at=recent)

    resp = client.get("/analytics/summary", headers=ws.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["conversations"] == 1
    assert body["deflection_rate"] is not None
    assert 0.0 <= body["deflection_rate"] <= 1.0
    assert body["deflection_rate"] == pytest.approx(1.0)


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


@pytest.mark.parametrize(
    ("params", "use_auth", "expected_status"),
    [
        pytest.param({"period": "14d"}, True, 400, id="invalid_period"),
        pytest.param({}, False, 401, id="unauthenticated"),
    ],
)
def test_analytics_summary_rejects_bad_requests(
    tenant: TestClient,
    db_session: Session,
    params: dict[str, str],
    use_auth: bool,
    expected_status: int,
) -> None:
    client = tenant
    headers = {}
    if use_auth:
        ws = _workspace(client, db_session, email="bad-request-owner@example.com", name="Bad Co")
        headers = ws.auth

    resp = client.get("/analytics/summary", headers=headers, params=params)
    assert resp.status_code == expected_status
