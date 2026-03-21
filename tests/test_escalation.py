"""FI-ESC: escalation helper unit tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.escalation.service import (
    compute_priority,
    detect_human_request,
    generate_ticket_number,
    parse_contact_email,
    should_escalate,
)
from backend.models import (
    EscalationPriority,
    EscalationTicket,
    EscalationTrigger,
    EscalationStatus,
)
from tests.conftest import register_and_verify_user


def test_should_escalate_low_similarity() -> None:
    esc, trig = should_escalate(0.3, 3)
    assert esc is True
    assert trig == EscalationTrigger.low_similarity


def test_should_escalate_no_documents() -> None:
    esc, trig = should_escalate(None, 0)
    assert esc is True
    assert trig == EscalationTrigger.no_documents


def test_should_escalate_ok() -> None:
    esc, trig = should_escalate(0.9, 2)
    assert esc is False
    assert trig is None


def test_detect_human_request_english() -> None:
    assert detect_human_request("I need to talk to a human please") is True
    assert detect_human_request("connect me to support, this is useless") is True


def test_detect_human_request_russian() -> None:
    assert detect_human_request("хочу поговорить с человеком") is True


def test_compute_priority_t3_enterprise() -> None:
    p = compute_priority(
        EscalationTrigger.user_request,
        "enterprise",
        {"plan_tier": "enterprise"},
    )
    assert p == EscalationPriority.critical


def test_compute_priority_t3_default() -> None:
    p = compute_priority(EscalationTrigger.user_request, None, {})
    assert p == EscalationPriority.high


def test_parse_contact_email() -> None:
    assert parse_contact_email("reach me at user@example.com thanks") == "user@example.com"
    assert parse_contact_email("no email here") is None


def test_generate_ticket_number_sequential(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="esc-seq@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc Seq"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    assert generate_ticket_number(client_id, db_session) == "ESC-0001"

    t = EscalationTicket(
        client_id=client_id,
        ticket_number="ESC-0001",
        primary_question="test",
        trigger=EscalationTrigger.low_similarity,
        status=EscalationStatus.open,
    )
    db_session.add(t)
    db_session.commit()

    assert generate_ticket_number(client_id, db_session) == "ESC-0002"
