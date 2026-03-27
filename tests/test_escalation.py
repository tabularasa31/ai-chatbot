"""FI-ESC: escalation helper unit tests."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.orm import Session

from backend.escalation.service import (
    _clear_escalation_clarify_flag,
    _escalation_clarify_already_asked,
    _set_escalation_clarify_flag,
    apply_collected_contact_email,
    compute_priority,
    create_escalation_ticket,
    detect_human_request,
    generate_ticket_number,
    parse_contact_email,
    perform_manual_escalation,
    should_escalate,
)
from backend.models import (
    Chat,
    Client,
    EscalationPriority,
    EscalationTicket,
    EscalationTrigger,
    EscalationStatus,
    Message,
    MessageRole,
    UserSession,
    User,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


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


def test_should_not_escalate_when_answer_is_valid() -> None:
    esc, trig = should_escalate(
        0.03,
        2,
        validation={"is_valid": True, "confidence": 0.98, "reason": "grounded"},
    )
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


def test_generate_ticket_number_concurrent_reads_return_same(
    client: TestClient,
    db_session: Session,
) -> None:
    """Two reads before any commit both return ESC-0001.

    This documents the race condition: generate_ticket_number is not atomic
    on its own. The retry loop in create_escalation_ticket is responsible for
    handling the resulting IntegrityError.
    """
    token = register_and_verify_user(client, db_session, email="esc-concurrent@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Concurrent Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    first = generate_ticket_number(client_id, db_session)
    second = generate_ticket_number(client_id, db_session)
    assert first == "ESC-0001"
    assert second == "ESC-0001"


def test_create_escalation_ticket_retries_on_integrity_error(
    client: TestClient,
    db_session: Session,
) -> None:
    """create_escalation_ticket retries once when the first commit raises IntegrityError."""
    token = register_and_verify_user(client, db_session, email="esc-retry@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Retry Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    real_commit = db_session.commit
    call_count = [0]

    def commit_once_then_succeed():
        call_count[0] += 1
        if call_count[0] == 1:
            raise SAIntegrityError("stmt", {}, Exception("unique constraint violation"))
        return real_commit()

    with patch.object(db_session, "commit", side_effect=commit_once_then_succeed):
        ticket = create_escalation_ticket(
            client_id,
            "test retry question",
            EscalationTrigger.low_similarity,
            db_session,
        )

    assert ticket.ticket_number.startswith("ESC-")
    assert call_count[0] == 2


def test_create_escalation_ticket_stores_redacted_and_encrypted_question(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="esc-redact@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Redaction Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        client_id,
        "my email is user@example.com",
        EscalationTrigger.low_similarity,
        db_session,
    )

    assert ticket.primary_question == "my email is [EMAIL]"
    assert ticket.primary_question_redacted == "my email is [EMAIL]"
    assert ticket.primary_question_original_encrypted is not None


def test_create_escalation_ticket_raises_after_max_retries(
    client: TestClient,
    db_session: Session,
) -> None:
    """After 3 failed commit attempts create_escalation_ticket re-raises IntegrityError."""
    token = register_and_verify_user(client, db_session, email="esc-maxretry@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Max Retry Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    def always_integrity_error():
        raise SAIntegrityError("stmt", {}, Exception("unique constraint violation"))

    with patch.object(db_session, "commit", side_effect=always_integrity_error):
        with pytest.raises(SAIntegrityError):
            create_escalation_ticket(
                client_id,
                "test max retry question",
                EscalationTrigger.low_similarity,
                db_session,
            )


def test_escalation_clarify_flags_roundtrip(db_session: Session) -> None:
    from backend.core.security import hash_password

    user = User(email="clarify@example.com", password_hash=hash_password("SecurePass1!"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    cl = Client(user_id=user.id, name="Clarify Client", api_key="clarify-key")
    db_session.add(cl)
    db_session.commit()
    db_session.refresh(cl)

    chat = Chat(client_id=cl.id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    assert _escalation_clarify_already_asked(chat) is False
    _set_escalation_clarify_flag(chat)
    assert _escalation_clarify_already_asked(chat) is True
    _clear_escalation_clarify_flag(chat)
    assert _escalation_clarify_already_asked(chat) is False


def test_apply_collected_contact_email_updates_chat_ticket_and_user_session(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="apply-email@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Apply Email Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    cl = db_session.query(Client).filter(Client.id == client_id).first()
    assert cl is not None

    chat = Chat(
        client_id=client_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-123", "email": None},
        escalation_followup_pending=False,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        client_id=client_id,
        ticket_number="ESC-0001",
        primary_question="need support",
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

    row = UserSession(client_id=client_id, user_id="u-123", email=None)
    db_session.add(row)
    db_session.commit()

    apply_collected_contact_email(ticket.id, chat.id, "user@example.com", db_session)

    db_session.refresh(ticket)
    db_session.refresh(chat)
    db_session.refresh(row)
    assert ticket.user_email == "user@example.com"
    assert chat.user_context.get("email") == "user@example.com"
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is True
    assert row.email == "user@example.com"


def test_perform_manual_escalation_sets_awaiting_ticket_when_email_missing(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="manual-missing@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual Missing Email"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(client, token)

    chat = Chat(client_id=client_id, session_id=uuid.uuid4(), user_context={"email": None})
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cl = db_session.query(Client).filter(Client.id == client_id).first()
    assert cl is not None
    msg, tnum = perform_manual_escalation(
        db_session,
        cl,
        chat.session_id,
        api_key="sk-test",
        user_note="please escalate",
        trigger=EscalationTrigger.user_request,
    )

    assert tnum.startswith("ESC-")
    assert isinstance(msg, str) and msg != ""
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is not None
    assert chat.escalation_followup_pending is False

    ticket = db_session.query(EscalationTicket).filter(EscalationTicket.chat_id == chat.id).first()
    assert ticket is not None
    assert ticket.trigger == EscalationTrigger.user_request
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 1
    assert messages[0].role == MessageRole.assistant


def test_perform_manual_escalation_sets_followup_when_email_known(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="manual-known@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual Known Email"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(client, token)

    chat = Chat(
        client_id=client_id,
        session_id=uuid.uuid4(),
        user_context={"email": "known@example.com"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cl = db_session.query(Client).filter(Client.id == client_id).first()
    assert cl is not None
    _msg, tnum = perform_manual_escalation(
        db_session,
        cl,
        chat.session_id,
        api_key="sk-test",
        user_note="answer rejected",
        trigger=EscalationTrigger.answer_rejected,
    )
    assert tnum.startswith("ESC-")
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is True
    ticket = db_session.query(EscalationTicket).filter(EscalationTicket.chat_id == chat.id).first()
    assert ticket is not None
    assert ticket.trigger == EscalationTrigger.answer_rejected


def test_escalation_api_returns_safe_question_by_default(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="esc-api@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc API Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        client_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )

    resp = client.get(
        f"/escalations/{ticket.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["primary_question"] == "contact me at [EMAIL]"
    assert data["primary_question_original"] is None
    assert data["primary_question_original_available"] is True


def test_escalation_api_can_include_original_question(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="esc-api-orig@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc API Orig Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        client_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )
    user = db_session.query(User).filter(User.email == "esc-api-orig@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = client.get(
        f"/escalations/{ticket.id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["primary_question"] == "contact me at [EMAIL]"
    assert data["primary_question_original"] == "contact me at user@example.com"


def test_escalation_api_include_original_requires_admin(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="esc-api-no-admin@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc API No Admin Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        client_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )

    resp = client.get(
        f"/escalations/{ticket.id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_delete_escalation_original_requires_admin_and_removes_original(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="esc-delete@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc Delete Client"},
    )
    assert cl_resp.status_code == 201
    client_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        client_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )

    denied = client.post(
        f"/escalations/{ticket.id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403

    user = db_session.query(User).filter(User.email == "esc-delete@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = client.post(
        f"/escalations/{ticket.id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 1

    db_session.refresh(ticket)
    assert ticket.primary_question_original_encrypted is None
    assert ticket.primary_question == "contact me at [EMAIL]"
