"""Tests for escalation flow: awaiting email, followup, manual escalate."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.escalation.service import HumanRequestResult
from backend.models import Chat, ContactSession
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import (
    get_default_bot_public_id,
    post_chat_message,
    register_and_verify_user,
    set_client_openai_key,
)


def _async_esc_stub(result):
    """Wrap a canned EscalationLlmResult-like Mock as an async stub for the
    now-async ``complete_escalation_openai_turn``."""

    async def _stub(**kwargs):
        return result

    return _stub


def _human_request_sequence(*results: HumanRequestResult):
    """Async stub for ``detect_human_request``: returns one result per call, in
    order — one entry per driven turn."""
    calls = iter(results)

    async def _stub(*_args: object, **_kwargs: object) -> HumanRequestResult:
        return next(calls)

    return _stub


def _register_tenant_with_key(
    tenant: TestClient, db_session: Session, *, email: str, name: str
) -> tuple[str, uuid.UUID]:
    """Register a user, create their tenant, and set a client OpenAI key.

    Collapses the "register → create tenant → set_client_openai_key"
    boilerplate repeated across this file. Returns ``(bot_public_id, tenant_id)``.
    """
    token = register_and_verify_user(tenant, db_session, email=email)
    resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    set_client_openai_key(tenant, token)
    return get_default_bot_public_id(tenant, token), uuid.UUID(resp.json()["id"])


def _make_chat(db_session: Session, tenant_id: uuid.UUID, **kwargs: object):
    from backend.models import Chat

    kwargs.setdefault("session_id", uuid.uuid4())
    kwargs.setdefault("user_context", {})
    chat = Chat(tenant_id=tenant_id, **kwargs)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return chat


def _make_open_ticket(db_session: Session, tenant_id: uuid.UUID, chat, **kwargs: object):
    from backend.models import EscalationStatus, EscalationTicket, EscalationTrigger

    kwargs.setdefault("ticket_number", f"ESC-{uuid.uuid4().hex[:8]}")
    kwargs.setdefault("primary_question", "Need support")
    kwargs.setdefault("trigger", EscalationTrigger.user_request)
    kwargs.setdefault("status", EscalationStatus.open)
    ticket = EscalationTicket(
        tenant_id=tenant_id,
        chat_id=chat.id,
        session_id=chat.session_id,
        **kwargs,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _latest_chat_for_session(db_session: Session, session_id: uuid.UUID) -> Chat:
    return (
        db_session.query(Chat)
        .filter(Chat.session_id == session_id)
        .order_by(Chat.created_at.desc(), Chat.id.desc())
        .first()
    )


def drive(
    tenant: TestClient, bot_public_id: str, session_id: uuid.UUID, *questions: str
) -> list[dict]:
    """POST each question through ``/widget/chat`` in turn; return the parsed
    JSON responses in order. Asserts every turn succeeds (200) as it goes."""
    responses = []
    for question in questions:
        resp = post_chat_message(
            tenant, bot_public_id=bot_public_id, question=question, session_id=str(session_id)
        )
        assert resp.status_code == 200, resp.text
        responses.append(resp.json())
    return responses


def _seed_rag_answer(
    mock_openai_client: Mock, db_session: Session, tenant_id: uuid.UUID, *, answer: str
) -> None:
    """Seed one document/embedding and stub the raw OpenAI client so RagHandler
    answers with ``answer`` for a turn that falls through to it."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    doc = Document(
        tenant_id=tenant_id,
        filename="seed.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text=answer,
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db_session.commit()
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(answer)


@pytest.mark.escalation
def test_chat_awaiting_email_valid_email_transitions_to_followup(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="await-valid@example.com", name="Await Valid Tenant"
    )
    chat = _make_chat(db_session, tenant_id, user_context={"user_id": "u-await"})
    ticket = _make_open_ticket(db_session, tenant_id, chat, primary_question="Need human support")

    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    [reply] = drive(tenant, api_key, chat.session_id, "reach me at user@example.com")

    db_session.refresh(chat)
    db_session.refresh(ticket)
    assert ticket.user_email == "user@example.com"
    assert reply["ticket_number"] == ticket.ticket_number
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is True


@pytest.mark.escalation
def test_chat_awaiting_email_invalid_keeps_waiting_ticket(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="await-invalid@example.com", name="Await Invalid Tenant"
    )
    chat = _make_chat(db_session, tenant_id)
    ticket = _make_open_ticket(db_session, tenant_id, chat)

    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    drive(tenant, api_key, chat.session_id, "my email is not provided")

    db_session.refresh(chat)
    db_session.refresh(ticket)
    assert chat.escalation_awaiting_ticket_id == ticket.id
    assert ticket.user_email is None


@pytest.mark.escalation
def test_chat_followup_no_keeps_chat_open(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"No, that's all" is a goodbye, not a close: the next question gets an
    ordinary answer in the same conversation."""
    from backend.models import (
        Chat,
        Document,
        DocumentStatus,
        DocumentType,
        Embedding,
        EscalationStatus,
        EscalationTicket,
        EscalationTrigger,
    )

    token = register_and_verify_user(tenant, db_session, email="follow-no@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow No Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    bot_public_id = get_default_bot_public_id(tenant, token)

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
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(
                message_to_user="Glad to help. Write here anytime.",
                followup_decision="no",
                tokens_used=3,
            )
        ),
    )

    response = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="no thanks", session_id=str(chat.session_id)
    )
    assert response.status_code == 200
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False

    doc = Document(
        tenant_id=tenant_id,
        filename="wildcards.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    db_session.add(
        Embedding(
            document_id=doc.id,
            chunk_text="Wildcard domains are supported on all plans",
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db_session.commit()
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "Yes, wildcard domains are supported."
    )

    async def _fail_escalation_turn(**kwargs):
        raise AssertionError("a question after the goodbye must reach RAG, not the escalation FSM")

    monkeypatch.setattr(
        "backend.chat.handlers.escalation.complete_escalation_openai_turn", _fail_escalation_turn
    )

    followup = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="do you support wildcard domains?", session_id=str(chat.session_id)
    )
    assert followup.status_code == 200
    assert followup.json()["text"] == "Yes, wildcard domains are supported."


@pytest.mark.escalation
def test_chat_followup_no_keeps_active_user_session_open(
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
    bot_public_id = get_default_bot_public_id(tenant, token)

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
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(
                message_to_user="Glad to help. Write here anytime.",
                followup_decision="no",
                tokens_used=3,
            )
        ),
    )

    response = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="no thanks", session_id=str(chat.session_id)
    )
    assert response.status_code == 200

    db_session.refresh(row)
    assert row.conversation_turns == 1
    assert row.session_ended_at is None


@pytest.mark.escalation
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
    bot_public_id = get_default_bot_public_id(tenant, token)

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
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(
                message_to_user="Understood, we will continue.",
                followup_decision="yes",
                tokens_used=3,
            )
        ),
    )

    response = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="yes please continue", session_id=str(chat.session_id)
    )
    assert response.status_code == 200

    db_session.refresh(chat)
    db_session.refresh(row)
    assert chat.escalation_followup_pending is False
    assert row.conversation_turns == 1
    assert row.session_ended_at is None


@pytest.mark.escalation
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
    bot_public_id = get_default_bot_public_id(tenant, token)

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
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(
                message_to_user="Could you clarify?",
                followup_decision="unclear",
                tokens_used=2,
            )
        ),
    )

    r1 = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="maybe", session_id=str(chat.session_id)
    )
    assert r1.status_code == 200
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is True
    assert (chat.user_context or {}).get("escalation_followup_clarify") is True

    r2 = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="still not sure", session_id=str(chat.session_id)
    )
    assert r2.status_code == 200
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert (chat.user_context or {}).get("escalation_followup_clarify") is None


# ---------------------------------------------------------------------------
# Escalation-FSM scenarios migrated from tests/test_chat_handlers_escalation.py
# (see the test-audit notes): each drives /chat end-to-end instead of calling
# EscalationStateMachine directly, stubbing only the classifier/LLM boundary
# functions the FSM already awaits (detect_human_request,
# classify_pre_confirm_reply, complete_escalation_openai_turn).
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.escalation
def test_explicit_human_request_with_content_bypasses_pre_confirm_and_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit "connect me to a human" that also states the problem must
    create a ticket immediately: the request itself is the confirmation, so
    the pre_confirm gate is skipped (unlike a bot-initiated low_similarity
    offer, which still asks for consent)."""
    from backend.models import EscalationTicket, EscalationTrigger

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="explicit-with-content@example.com", name="Explicit With Content"
    )
    chat = _make_chat(db_session, tenant_id)
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=True,
                human_request_explicit=True,
            )
        ),
    )

    [resp] = drive(
        tenant, api_key, chat.session_id, "my billing is broken, connect me to a human please"
    )

    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.user_request
    assert ticket.primary_question == "my billing is broken, connect me to a human please"
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False


@pytest.mark.escalation
def test_implied_human_request_over_real_question_falls_through_to_rag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the prod case where "I can't change the settings" — a
    stated problem the classifier read as an inferred plea for help — minted a
    ticket on the spot. An *inferred* request over a real question must be
    answered by RAG instead; only an outright ask escalates immediately."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="implied-with-content@example.com", name="Implied With Content"
    )
    chat = _make_chat(db_session, tenant_id)
    _seed_rag_answer(
        mock_openai_client, db_session, tenant_id, answer="Here's how to change your settings."
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=True,
                human_request_explicit=False,
            )
        ),
    )

    [resp] = drive(tenant, api_key, chat.session_id, "I can't change the settings")

    assert resp["text"] == "Here's how to change your settings."
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is False
    assert chat.escalation_pre_confirm_pending is False
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


@pytest.mark.escalation
def test_implied_human_request_after_prior_substantive_content_falls_through_to_rag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sticky "chat already carries substantive content" flag does not
    resurrect the escalation branch for an *inferred* request either — only an
    outright ask may use it to skip elicitation."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="implied-sticky@example.com", name="Implied Sticky"
    )
    chat = _make_chat(db_session, tenant_id)
    _seed_rag_answer(
        mock_openai_client, db_session, tenant_id, answer="Your invoice is on the billing page."
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(human_request=False, message_has_request_content=True),
            HumanRequestResult(
                human_request=True,
                message_has_request_content=False,
                human_request_explicit=False,
            ),
        ),
    )

    drive(tenant, api_key, chat.session_id, "where do I find my invoice", "please help me")

    db_session.refresh(chat)
    assert chat.has_substantive_content is True
    assert chat.escalation_awaiting_request is False
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


@pytest.mark.escalation
def test_explicit_human_request_after_prior_substantive_content_escalates_immediately(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="explicit-sticky@example.com", name="Explicit Sticky"
    )
    chat = _make_chat(db_session, tenant_id)
    _seed_rag_answer(
        mock_openai_client, db_session, tenant_id, answer="Your invoice is on the billing page."
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(human_request=False, message_has_request_content=True),
            HumanRequestResult(
                human_request=True,
                message_has_request_content=False,
                human_request_explicit=True,
            ),
        ),
    )

    drive(tenant, api_key, chat.session_id, "where do I find my invoice", "connect me to a human")

    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is False
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 1
    )


@pytest.mark.escalation
@pytest.mark.parametrize(
    ("follow_ups", "reports_result", "tickets_per_turn"),
    [
        pytest.param(("just have support write to me",) * 2, False, (0, 1), id="bare_forward"),
        pytest.param(("did both, still 502, please forward it",), True, (1,), id="with_result"),
    ],
)
def test_checklist_reply_holds_handoff_until_user_reports_result(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    follow_ups: tuple[str, ...],
    reports_result: bool,
    tickets_per_turn: tuple[int, ...],
) -> None:
    """After a checklist reply, a bare request to forward the conversation gets
    one re-ask for the result and only a second request creates the ticket; a
    request that reports what the checks showed escalates at once."""
    from backend.chat.handlers.escalation import _CHECKLIST_REASK_CANONICAL_TEXT
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email=f"checklist-{reports_result}@example.com", name="Checklist"
    )
    chat = _make_chat(db_session, tenant_id)
    checklist = "1. Set the origin port to 80.\n2. Turn off HTTPS to origin.\nWhat happened?"
    _seed_rag_answer(mock_openai_client, db_session, tenant_id, answer=f"{checklist} <checklist/>")
    forward_request = HumanRequestResult(
        human_request=True,
        message_has_request_content=reports_result,
        human_request_explicit=True,
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(human_request=False, message_has_request_content=True),
            *(forward_request,) * len(follow_ups),
        ),
    )

    [r1] = drive(tenant, api_key, chat.session_id, "https gives 502")
    assert r1["text"] == checklist
    for follow_up, expected_tickets in zip(follow_ups, tickets_per_turn):
        [reply] = drive(tenant, api_key, chat.session_id, follow_up)
        tickets = (
            db_session.query(EscalationTicket)
            .filter(EscalationTicket.tenant_id == tenant_id)
            .count()
        )
        assert tickets == expected_tickets
        if expected_tickets == 0:
            assert reply["text"] == _CHECKLIST_REASK_CANONICAL_TEXT


@pytest.mark.escalation
def test_implied_human_request_without_content_on_fresh_chat_falls_through_to_rag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="implied-fresh@example.com", name="Implied Fresh"
    )
    chat = _make_chat(db_session, tenant_id)
    _seed_rag_answer(mock_openai_client, db_session, tenant_id, answer="How can I help?")
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=False,
                human_request_explicit=False,
            ),
        ),
    )

    [reply] = drive(tenant, api_key, chat.session_id, "please help me")

    assert reply["text"] == "How can I help?"
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is False
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


@pytest.mark.escalation
def test_pre_confirm_null_reply_with_explicit_human_request_still_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for PR #694 review (gemini medium): when the reply that
    clears the pre_confirm gate is *also* an explicit human request, the turn
    must still escalate instead of silently falling through to RAG."""
    from backend.models import EscalationTicket, EscalationTrigger

    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email="pre-confirm-null-explicit@example.com",
        name="Pre Confirm Null Explicit",
    )
    chat = _make_chat(
        db_session,
        tenant_id,
        escalation_pre_confirm_pending=True,
        escalation_pre_confirm_context={
            "trigger": "low_similarity",
            "primary_question": "my widget won't render",
            "best_similarity_score": 0.31,
            "retrieved_chunks": None,
        },
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.classify_pre_confirm_reply", _async_esc_stub((None, 0))
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=True,
                human_request_explicit=True,
            )
        ),
    )

    [resp] = drive(
        tenant, api_key, chat.session_id, "still broken, just connect me to a human already"
    )

    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.user_request
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False


@pytest.mark.escalation
def test_stale_followup_falls_through_to_rag_after_session_ended(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up prompt left pending across an inactivity gap must not eat a
    genuine new question. Once the sweeper reports the session ended the next
    turn rotates to a fresh chat, so the new question gets a RAG answer and
    never reaches the yes/no classifier; the handler's own stale-gate branch
    is a second line of defence that rotation makes unreachable here."""
    from datetime import UTC

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="stale-followup@example.com", name="Stale Followup"
    )
    chat = _make_chat(
        db_session,
        tenant_id,
        escalation_followup_pending=True,
        session_ended_event_at=datetime.now(UTC),
        user_context={"escalation_followup_clarify": True},
    )
    _make_open_ticket(db_session, tenant_id, chat)
    _seed_rag_answer(mock_openai_client, db_session, tenant_id, answer="The A record was added.")

    async def _fail_if_classified(**_kwargs: object) -> None:
        raise AssertionError(
            "stale follow-up must fall through to RAG, not run the escalation LLM"
        )

    monkeypatch.setattr("backend.chat.handlers.escalation.complete_escalation_openai_turn", _fail_if_classified)

    [resp] = drive(
        tenant, api_key, chat.session_id, "why was the A www record not added to the list?"
    )

    assert resp["text"] == "The A record was added."
    served = _latest_chat_for_session(db_session, chat.session_id)
    assert served.id != chat.id, "ended session must rotate to a fresh chat"
    assert served.escalation_followup_pending is False
    assert (served.user_context or {}).get("escalation_followup_clarify") is None


@pytest.mark.escalation
def test_stale_followup_with_explicit_human_request_still_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale follow-up must not swallow an explicit "connect me to a human":
    it still escalates immediately rather than falling through to RagHandler."""
    from datetime import UTC

    from backend.models import EscalationTicket, EscalationTrigger

    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email="stale-followup-explicit@example.com",
        name="Stale Followup Explicit",
    )
    chat = _make_chat(
        db_session,
        tenant_id,
        escalation_followup_pending=True,
        session_ended_event_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=True,
                human_request_explicit=True,
            )
        ),
    )

    [resp] = drive(
        tenant, api_key, chat.session_id, "the import still fails — just connect me to a human already"
    )

    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.user_request
    served = _latest_chat_for_session(db_session, chat.session_id)
    assert served.id != chat.id, "ended session must rotate to a fresh chat"
    assert ticket.chat_id == served.id
    assert served.escalation_followup_pending is False


@pytest.mark.smoke
@pytest.mark.escalation
def test_pre_confirm_journey_unclear_twice_then_yes_then_followup_new_question(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-turn pre_confirm -> followup journey from a low_similarity offer:

    - regression for 86exn3x7c: a second consecutive "unclear" reply to the
      pre_confirm offer re-asks, never gets silently promoted to "yes"
    - an explicit "yes" actually creates the ticket, hands off, and flips the
      chat into followup_pending
    - regression for prod session 0a730bc1-0db6-4e0b-84b6-bd0eccfbbda1: the
      very next new question is answered by RAG in the same turn, not the
      canned followup handoff reply
    """
    from backend.models import EscalationTicket, EscalationTrigger

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="pre-confirm-journey@example.com", name="Pre Confirm Journey"
    )
    chat = _make_chat(
        db_session,
        tenant_id,
        escalation_pre_confirm_pending=True,
        escalation_pre_confirm_context={
            "trigger": "low_similarity",
            "primary_question": "my widget won't render",
            "best_similarity_score": 0.31,
            "retrieved_chunks": None,
        },
        # A known contact email means the ticket goes straight to
        # followup_pending on creation instead of parking in awaiting_ticket.
        user_context={"email": "user@example.com"},
    )
    pre_confirm_replies = iter([("unclear", 0), ("unclear", 0), ("yes", 5)])

    async def _pre_confirm_stub(**kwargs):
        return next(pre_confirm_replies)

    monkeypatch.setattr("backend.chat.handlers.escalation.classify_pre_confirm_reply", _pre_confirm_stub)

    r1, r2 = drive(
        tenant,
        api_key,
        chat.session_id,
        "wait, what do you mean by forwarding?",
        "still not sure what you mean",
    )
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )

    [r3] = drive(tenant, api_key, chat.session_id, "yes please")
    assert r3.get("ticket_number")
    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.low_similarity
    assert ticket.primary_question == "my widget won't render"
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_followup_pending is True

    _seed_rag_answer(
        mock_openai_client, db_session, tenant_id, answer="Yes, wildcard domains are supported."
    )

    async def _gate_new_question(**kwargs):
        return ("new_question", 7)

    monkeypatch.setattr("backend.chat.handlers.escalation.classify_followup_reply", _gate_new_question)

    async def _fail_full_turn(**kwargs):
        raise AssertionError(
            "new-question follow-up must be answered by RAG, not the full-turn escalation LLM"
        )

    monkeypatch.setattr("backend.chat.handlers.escalation.complete_escalation_openai_turn", _fail_full_turn)

    db_session.refresh(chat)
    tokens_before = chat.tokens_used

    [r4] = drive(tenant, api_key, chat.session_id, "do you support wildcard domain names?")
    assert r4["text"] == "Yes, wildcard domains are supported."

    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    # Gate-classifier tokens carried into the RAG turn (completion mocked at 0).
    assert chat.tokens_used - tokens_before == 7


@pytest.mark.smoke
@pytest.mark.escalation
@pytest.mark.parametrize(
    "status, seed_operator_reply, expect_requested_again",
    [
        pytest.param(None, False, False, id="open_unanswered_keeps_wait"),
        pytest.param("in_progress", False, False, id="in_progress_unanswered_keeps_wait"),
        pytest.param("in_progress", True, True, id="in_progress_after_operator_reply_requeues"),
    ],
)
def test_repeat_explicit_request_threads_onto_open_ticket(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    status: str | None,
    seed_operator_reply: bool,
    expect_requested_again: bool,
) -> None:
    """Repeating an explicit human request must thread onto the chat's
    existing open ticket instead of minting a fresh one (regression: one
    conversation produced six ESC numbers in 79 seconds), and must only reset
    the visitor's wait (``requested_again_at``) once an operator has actually
    answered — never while the ticket is still unanswered, open or
    in_progress."""
    from backend.models import EscalationStatus, EscalationTicket, Message, MessageRole

    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email=f"repeat-explicit-{status}-{seed_operator_reply}@example.com",
        name="Repeat Explicit",
    )
    chat = _make_chat(db_session, tenant_id)
    ticket_kwargs: dict[str, object] = {"user_email": "user@example.com"}
    if status is not None:
        ticket_kwargs["status"] = EscalationStatus(status)
    existing = _make_open_ticket(db_session, tenant_id, chat, **ticket_kwargs)
    if seed_operator_reply:
        db_session.add(Message(chat_id=chat.id, role=MessageRole.operator, content="Which form?"))
        db_session.commit()
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=True,
                human_request_explicit=True,
            )
        ),
    )

    [resp] = drive(tenant, api_key, chat.session_id, "I need a person again")

    assert resp["ticket_number"] == existing.ticket_number
    tickets = (
        db_session.query(EscalationTicket).filter(EscalationTicket.chat_id == chat.id).all()
    )
    assert len(tickets) == 1
    db_session.refresh(existing)
    if expect_requested_again:
        assert existing.requested_again_at is not None
        assert existing.requested_again_at >= existing.created_at
    else:
        assert existing.requested_again_at is None


@pytest.mark.escalation
def test_human_request_after_greeting_only_elicits_not_escalates(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for PR #722 review (gemini high): a prior *greeting* turn
    (no substantive content) must not leak through as forwardable context — a
    bare "connect me to a human" right after it still elicits the question."""
    from backend.chat.handlers.escalation import _AWAITING_REQUEST_CANONICAL_TEXT
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email="greeting-then-request@example.com",
        name="Greeting Then Request",
    )
    chat = _make_chat(db_session, tenant_id)
    _seed_rag_answer(mock_openai_client, db_session, tenant_id, answer="Hi there!")
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(human_request=False, message_has_request_content=False),
            HumanRequestResult(
                human_request=True,
                message_has_request_content=False,
                human_request_explicit=True,
            ),
        ),
    )

    responses = drive(tenant, api_key, chat.session_id, "hi", "connect me to a human")

    assert responses[1]["text"] == _AWAITING_REQUEST_CANONICAL_TEXT
    db_session.refresh(chat)
    assert chat.has_substantive_content is False
    assert chat.escalation_awaiting_request is True
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


@pytest.mark.escalation
def test_awaiting_request_journey_elicit_then_reping_then_substantive_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-turn awaiting-request journey:

    - a bare "connect me to a human" with nothing to forward opens
      awaiting-request elicitation instead of minting an empty ticket
    - a repeated bare ping while parked re-elicits and stays parked, never
      mints a ticket
    - once the user supplies the concrete question, the parked state
      escalates with that content and clears the flag
    """
    from backend.chat.handlers.escalation import _AWAITING_REQUEST_CANONICAL_TEXT
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="awaiting-journey@example.com", name="Awaiting Journey"
    )
    chat = _make_chat(db_session, tenant_id)
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True, message_has_request_content=False, human_request_explicit=True
            ),
            HumanRequestResult(
                human_request=True, message_has_request_content=False, human_request_explicit=True
            ),
            HumanRequestResult(human_request=False, message_has_request_content=True),
        ),
    )

    r1, r2 = drive(tenant, api_key, chat.session_id, "connect me to a human", "is anyone there??")
    assert r1["text"] == _AWAITING_REQUEST_CANONICAL_TEXT
    assert r2["text"] == _AWAITING_REQUEST_CANONICAL_TEXT
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is True
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )

    [r3] = drive(tenant, api_key, chat.session_id, "my invoice shows the wrong amount")
    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.primary_question == "my invoice shows the wrong amount"
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is False


@pytest.mark.escalation
def test_awaiting_request_unrelated_message_falls_through_to_rag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While parked in awaiting-request, a reply that is neither a human
    request nor a stated problem clears the flag and yields to RAG."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="awaiting-unrelated@example.com", name="Awaiting Unrelated"
    )
    chat = _make_chat(db_session, tenant_id, escalation_awaiting_request=True)
    _seed_rag_answer(mock_openai_client, db_session, tenant_id, answer="You're welcome!")
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(human_request=False, message_has_request_content=False)
        ),
    )

    [resp] = drive(tenant, api_key, chat.session_id, "ok thanks")

    assert resp["text"] == "You're welcome!"
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is False
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


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
    bot_public_id = get_default_bot_public_id(tenant, token)
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

    response = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="What is the answer?"
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
    bot_public_id = get_default_bot_public_id(tenant, token)

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
        "backend.chat.persistence.record_user_session_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tracking failed")),
    )

    response = post_chat_message(
        tenant, bot_public_id=bot_public_id, question="What is the answer?", session_id=str(chat.session_id)
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Tracked answer"

    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 2


@pytest.mark.escalation
def test_widget_manual_escalate_unknown_bot_returns_404(tenant: TestClient) -> None:
    response = tenant.post(
        f"/widget/escalate?bot_id=unknown-bot&session_id={uuid.uuid4()}",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 404


@pytest.mark.escalation
def test_widget_manual_escalate_without_openai_key_returns_400(
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
    bot_public_id = get_default_bot_public_id(tenant, token)

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    response = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 400


@pytest.mark.escalation
def test_widget_manual_escalate_missing_session_returns_404(
    tenant: TestClient,
    db_session: Session,
) -> None:
    bot_public_id, _tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-404@example.com", name="Manual 404"
    )

    response = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={uuid.uuid4()}",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 404


@pytest.mark.escalation
def test_widget_manual_escalate_openai_error_returns_503(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``complete_escalation_openai_turn`` — the only OpenAI call on this path
    — catches every exception internally and always returns a fail-safe
    result (never raises), so an ``APIError`` genuinely cannot reach this
    route from the real OpenAI-client boundary today. The route's ``except
    APIError`` branch is exercised here by patching ``perform_manual_
    escalation`` directly, one level in from the HTTP boundary, since there
    is no reachable network-boundary stub that would trigger it."""
    from backend.models import Chat
    from openai import APIError

    bot_public_id, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-503@example.com", name="Manual 503"
    )
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    async def _raise_api_error(*args, **kwargs):
        raise APIError("Service unavailable", request=Mock(), body=None)

    monkeypatch.setattr("backend.widget.routes.perform_manual_escalation", _raise_api_error)

    response = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 503


@pytest.mark.escalation
def test_widget_manual_escalate_success_for_both_triggers(
    tenant: TestClient,
    db_session: Session,
    escalation_openai_override,
) -> None:
    from backend.models import Chat, EscalationTicket

    bot_public_id, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-success@example.com", name="Manual Success"
    )
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    escalation_openai_override(message_to_user="Escalated.")

    r1 = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "user_request"},
    )
    assert r1.status_code == 200
    assert r1.json()["message"] == "Escalated."
    ticket_number = r1.json()["ticket_number"]
    assert ticket_number

    r2 = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "answer_rejected"},
    )
    assert r2.status_code == 200
    # Second press threads onto the same open ticket rather than minting a new one.
    assert r2.json()["ticket_number"] == ticket_number
    tickets = (
        db_session.query(EscalationTicket).filter(EscalationTicket.chat_id == chat.id).all()
    )
    assert len(tickets) == 1


# ---------------------------------------------------------------------------
# Tenant-notification emails, migrated from tests/test_escalation.py (audit
# rows 25-58): each drives a real escalating conversation through /chat (or
# the manual-escalate route) and stubs only `send_email` where it is imported
# in backend/escalation/service.py, plus PostHog `capture_event` where noted.
# ---------------------------------------------------------------------------


@pytest.mark.escalation
@pytest.mark.parametrize(
    "send_email_kwargs, expected_reason",
    [
        ({"return_value": None}, "brevo_refused"),
        ({"side_effect": RuntimeError("boom")}, "send_exception"),
    ],
    ids=["brevo_refused", "send_exception"],
)
def test_new_ticket_notify_failure_reports_metric_and_leaves_ticket_retryable(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    send_email_kwargs: dict,
    expected_reason: str,
) -> None:
    """A Brevo refusal (``send_email`` returns ``None``) or a raised exception
    must both surface an internal metric and leave the notify markers untouched
    so the send stays retryable — never propagate to the chat turn."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email=f"notify-fail-{expected_reason}@example.com", name="Notify Fail Tenant"
    )
    chat = _make_chat(db_session, tenant_id, user_context={"email": "enduser@example.com"})
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True, message_has_request_content=True, human_request_explicit=True
            )
        ),
    )
    with (
        patch("backend.escalation.service.send_email", **send_email_kwargs),
        patch("backend.escalation.service.capture_event") as capture_mock,
    ):
        drive(tenant, api_key, chat.session_id, "billing is broken, connect me to a human")

    capture_mock.assert_called_once()
    assert capture_mock.call_args.args[0] == "escalation.email_send_failed"
    assert capture_mock.call_args.kwargs["properties"]["reason"] == expected_reason
    assert capture_mock.call_args.kwargs["properties"]["stage"] == "initial"

    ticket = db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    assert ticket.notification_message_id is None


@pytest.mark.smoke
@pytest.mark.escalation
def test_new_ticket_notify_no_l2_routes_to_owner_pii_safe_and_subject_hides_priority(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an L2 support address configured, a new-ticket notification
    goes to the account owner's email. The support email is a human-facing
    surface: the user's own literal contact details and the conversation
    transcript render verbatim in the body (redaction only guards the model
    boundary), while tenant-internal classification data (plan tier, user id,
    audience tag, KYC extras, priority, trigger) moves to X-Chat9-* headers
    instead of leaking into a body a support agent might quote back to the
    end user. The Re:-able subject line never leaks the internal priority
    tier or the "Chat9" brand, and the body omits the user-note section when
    there was none — this chat-driven trigger never populates one. Also
    asserts the initial ``send_email`` return value is captured as the
    threading anchor."""
    from backend.models import EscalationTicket

    owner_email = "pii-safe-owner@example.com"
    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email=owner_email, name="PII Safe Tenant"
    )
    chat = _make_chat(
        db_session,
        tenant_id,
        user_context={
            "email": "enduser@acme.io",
            "name": "Ivan Petrov",
            "plan_tier": "pro",
            "user_id": "u_18422",
            "audience_tag": "paying_b2b",
            "company": "ACME",
            "role_in_company": "ops_lead",
        },
    )
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True, message_has_request_content=True, human_request_explicit=True
            )
        ),
    )
    with patch("backend.escalation.service.send_email") as send_email_mock:
        send_email_mock.return_value = "<pii-safe@brevo>"
        drive(
            tenant,
            api_key,
            chat.session_id,
            "reach me at real@user.com or +1 202 555 0143, IP 198.51.100.9, connect me to a human",
        )

    send_email_mock.assert_called_once()
    args, kwargs = send_email_mock.call_args
    recipient, subject, body = args[0], args[1], args[2]
    headers = kwargs.get("extra_headers") or {}

    assert recipient == owner_email
    assert kwargs.get("reply_to") == "enduser@acme.io"
    assert "pro" not in body
    assert "u_18422" not in body
    assert "paying_b2b" not in body
    assert "ACME" not in body
    assert "Priority:" not in body
    assert "Trigger:" not in body

    assert "real@user.com" in body
    assert "+1 202 555 0143" in body
    assert "198.51.100.9" in body

    assert headers.get("X-Chat9-Plan") == "pro"
    assert headers.get("X-Chat9-User-Id") == "u_18422"
    assert headers.get("X-Chat9-Audience") == "paying_b2b"
    assert headers.get("X-Chat9-Trigger") == "user_request"

    for forbidden in ("CRITICAL", "Critical", "HIGH", "High", "Chat9"):
        assert forbidden not in subject
    assert "USER'S NOTE" not in body

    ticket = db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    assert ticket.notification_message_id == "<pii-safe@brevo>"


@pytest.mark.escalation
@pytest.mark.parametrize(
    "user_context", [{}, {"email": "not an email"}], ids=["missing_email", "malformed_email"]
)
def test_new_ticket_notify_skipped_without_valid_recipient_email(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    user_context: dict,
) -> None:
    """No notification can be sent — and none must be attempted — when the
    ticket has no known email, or when the widget supplied garbage that
    Brevo would reject outright."""
    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email=f"skip-notify-{len(user_context)}@example.com",
        name="Skip Notify Tenant",
    )
    chat = _make_chat(db_session, tenant_id, user_context=user_context)
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True, message_has_request_content=True, human_request_explicit=True
            )
        ),
    )
    with patch("backend.escalation.service.send_email") as send_email_mock:
        drive(tenant, api_key, chat.session_id, "connect me to a human, my account is broken")

    send_email_mock.assert_not_called()


@pytest.mark.escalation
def test_apply_collected_email_fires_deferred_notification(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """An anonymous escalation defers its notification until the visitor
    provides an email; once they do, the deferred notify fires with that
    address as the reply-to."""
    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="deferred-notify@example.com", name="Deferred Notify Tenant"
    )
    chat = _make_chat(db_session, tenant_id, user_context={"user_id": "u-late"})
    ticket = _make_open_ticket(
        db_session, tenant_id, chat, primary_question="please connect me to support"
    )
    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    with patch("backend.escalation.service.send_email") as send_email_mock:
        drive(tenant, api_key, chat.session_id, "reach me at late@example.com")

    send_email_mock.assert_called_once()
    args, kwargs = send_email_mock.call_args
    assert kwargs.get("reply_to") == "late@example.com"
    assert ticket.ticket_number in args[1]
    assert "late@example.com" in args[2]


@pytest.mark.smoke
@pytest.mark.escalation
def test_ticket_notify_journey_l2_recipient_then_threaded_update_then_failure_metric(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-turn notification journey with an L2 support address configured:

    - the new-ticket notification routes to the tenant's L2 address instead
      of the account owner once one is configured
    - a follow-up turn classified ``unclear`` (real context, not a bare
      yes/no) is forwarded to support as a threaded reply under the initial
      notification, carrying only the turns not already sent
    - a refused follow-up send reports the same internal metric as an
      initial-notify failure, tagged with the follow-up stage, and must not
      advance ``last_notified_message_id`` so the delta is retried later
    """
    from backend.models import EscalationTicket

    token = register_and_verify_user(tenant, db_session, email="l2-journey-owner@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "L2 Journey Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    bot_public_id = get_default_bot_public_id(tenant, token)
    support_resp = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "l2@example.com"},
    )
    assert support_resp.status_code == 200

    chat = _make_chat(db_session, tenant_id, user_context={"email": "enduser@example.com"})
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True, message_has_request_content=True, human_request_explicit=True
            ),
            # Later turns land in the followup branch, which does not act on
            # this result — kept benign so the (session-scoped) stub doesn't
            # run out of canned answers.
            HumanRequestResult(human_request=False, message_has_request_content=False),
            HumanRequestResult(human_request=False, message_has_request_content=False),
        ),
    )
    with patch("backend.escalation.service.send_email") as send_email_mock:
        send_email_mock.return_value = "<initial-abc@brevo>"
        drive(tenant, bot_public_id, chat.session_id, "billing is broken, connect me to a human")
    send_email_mock.assert_called_once()
    assert send_email_mock.call_args.args[0] == "l2@example.com"

    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.notification_message_id == "<initial-abc@brevo>"

    # Step outside the follow-up notify debounce window so the next turn's
    # threaded update is not skipped as "too soon after the last send".
    from datetime import UTC, timedelta

    from backend.escalation.service import _FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS

    ticket.last_notified_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS + 5
    )
    db_session.add(ticket)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(
                message_to_user="Noted, forwarding to support.",
                followup_decision="unclear",
                tokens_used=4,
            )
        ),
    )
    with patch("backend.escalation.service.send_email") as send_email_mock2:
        send_email_mock2.return_value = "<update-1@brevo>"
        drive(tenant, bot_public_id, chat.session_id, "BRAND NEW context about a billing error")

    send_email_mock2.assert_called_once()
    subject = send_email_mock2.call_args.args[1]
    body = send_email_mock2.call_args.args[2]
    headers = send_email_mock2.call_args.kwargs["extra_headers"]
    assert subject.startswith(f"Re: [{ticket.ticket_number}]")
    assert headers["In-Reply-To"] == "<initial-abc@brevo>"
    assert "BRAND NEW context about a billing error" in body
    assert "billing is broken, connect me to a human" not in body

    db_session.refresh(ticket)
    pre_marker = ticket.last_notified_message_id
    ticket.last_notified_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS + 5
    )
    db_session.add(ticket)
    db_session.commit()

    # The previous "unclear" turn set the followup clarify flag; a second
    # consecutive "unclear" reply would be promoted to "yes" (a deliberate,
    # separately-tested FSM rule) and never reach the notify path at all.
    # Clear it so this turn is read as a fresh "unclear" that does notify.
    db_session.refresh(chat)
    chat.user_context = {**(chat.user_context or {}), "escalation_followup_clarify": None}
    db_session.add(chat)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(message_to_user="Noted.", followup_decision="unclear", tokens_used=2)
        ),
    )
    with (
        patch("backend.escalation.service.send_email", return_value=None),
        patch("backend.escalation.service.capture_event") as capture_mock,
    ):
        drive(tenant, bot_public_id, chat.session_id, "context that fails to send")

    capture_mock.assert_called_once()
    assert capture_mock.call_args.args[0] == "escalation.email_send_failed"
    assert capture_mock.call_args.kwargs["properties"]["reason"] == "brevo_refused"
    assert capture_mock.call_args.kwargs["properties"]["stage"] == "followup"
    db_session.refresh(ticket)
    assert ticket.last_notified_message_id == pre_marker


@pytest.mark.escalation
def test_ticket_update_notify_skipped_for_administrative_reply_or_resolved_ticket(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No update email is sent for a bare yes/no follow-up (administrative,
    handled by the bot only) nor once the ticket has already been resolved
    (support closed the loop; further chatter shouldn't reopen the thread)."""
    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="ticket-skip@example.com", name="Ticket Skip Tenant"
    )

    chat = _make_chat(db_session, tenant_id, escalation_followup_pending=True)
    _make_open_ticket(db_session, tenant_id, chat, notification_message_id="<anchor-1@brevo>")
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(message_to_user="Glad to help.", followup_decision="no", tokens_used=2)
        ),
    )
    with patch("backend.escalation.service.send_email") as send_email_mock:
        drive(tenant, api_key, chat.session_id, "no thanks")
    send_email_mock.assert_not_called()

    from backend.models import EscalationStatus

    chat2 = _make_chat(db_session, tenant_id, escalation_followup_pending=True)
    ticket2 = _make_open_ticket(
        db_session,
        tenant_id,
        chat2,
        notification_message_id="<anchor-2@brevo>",
        status=EscalationStatus.resolved,
        user_email="enduser@example.com",
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _async_esc_stub(
            Mock(
                message_to_user="Noted.",
                followup_decision="unclear",
                tokens_used=3,
            )
        ),
    )
    with patch("backend.escalation.service.send_email") as send_email_mock2:
        drive(tenant, api_key, chat2.session_id, "one more detail on the resolved issue")
    send_email_mock2.assert_not_called()
    db_session.refresh(ticket2)
    assert ticket2.status == EscalationStatus.resolved


# ---------------------------------------------------------------------------
# perform_manual_escalation: ticket state, reuse and priority, migrated from
# tests/test_escalation.py (audit rows 41-46). Ticket-reuse-on-repeat and
# requested_again_at are covered by test_repeat_explicit_request_threads_
# onto_open_ticket above (stage 1) — not duplicated here.
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.escalation
def test_manual_escalate_sets_awaiting_ticket_when_email_missing_else_followup(
    tenant: TestClient,
    db_session: Session,
    escalation_openai_override,
) -> None:
    """The manual-escalate route parks the chat in ``awaiting_ticket`` when no
    contact email is known yet (so the next reply is read as the address),
    and goes straight to ``followup_pending`` when the email is already known."""
    from backend.models import Chat, EscalationTicket

    bot_public_id, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-state@example.com", name="Manual State Tenant"
    )
    escalation_openai_override(message_to_user="A support ticket was created for you.")

    missing = Chat(tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={"email": None})
    db_session.add(missing)
    db_session.commit()
    resp = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={missing.session_id}",
        json={"trigger": "user_request", "user_note": "please escalate"},
    )
    assert resp.status_code == 200
    db_session.refresh(missing)
    assert missing.escalation_awaiting_ticket_id is not None
    assert missing.escalation_followup_pending is False

    known = Chat(
        tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={"email": "known@example.com"}
    )
    db_session.add(known)
    db_session.commit()
    resp2 = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={known.session_id}",
        json={"trigger": "answer_rejected", "user_note": "answer rejected"},
    )
    assert resp2.status_code == 200
    db_session.refresh(known)
    assert known.escalation_awaiting_ticket_id is None
    assert known.escalation_followup_pending is True

    tickets = (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.chat_id.in_([missing.id, known.id]))
        .all()
    )
    assert {t.trigger.value for t in tickets} == {"user_request", "answer_rejected"}


@pytest.mark.escalation
def test_manual_escalate_mints_new_ticket_once_previous_resolved(
    tenant: TestClient,
    db_session: Session,
    escalation_openai_override,
) -> None:
    """Reuse is scoped to open tickets: once support resolves one, a fresh
    manual-escalate request on the same chat mints its own new ticket."""
    from backend.models import Chat, EscalationStatus, EscalationTicket

    bot_public_id, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-resolved-app@example.com", name="Manual Resolved Tenant"
    )
    escalation_openai_override(message_to_user="Escalated.")
    chat = Chat(
        tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={"email": "known@example.com"}
    )
    db_session.add(chat)
    db_session.commit()

    r1 = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "user_request", "user_note": "first problem"},
    )
    assert r1.status_code == 200
    first_number = r1.json()["ticket_number"]

    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.ticket_number == first_number).one()
    )
    ticket.status = EscalationStatus.resolved
    db_session.add(ticket)
    db_session.commit()

    r2 = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "user_request", "user_note": "unrelated second problem"},
    )
    assert r2.status_code == 200
    second_number = r2.json()["ticket_number"]
    assert second_number != first_number

    tickets = db_session.query(EscalationTicket).filter(EscalationTicket.chat_id == chat.id).all()
    assert len(tickets) == 2


@pytest.mark.escalation
def test_manual_escalate_emits_chat_escalated_event(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    escalation_openai_override,
) -> None:
    """A manual escalation emits ``chat_escalated`` on the PostHog boundary
    with the trigger recorded as both reason and trigger."""
    from backend.models import Chat

    captured: list[dict] = []

    def fake_capture(event, **kwargs):
        captured.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.observability.metrics.capture_event", fake_capture)

    bot_public_id, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-event@example.com", name="Manual Event Tenant"
    )
    escalation_openai_override(message_to_user="Escalated.")
    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"email": "user@example.com", "plan_tier": "pro"},
    )
    db_session.add(chat)
    db_session.commit()

    resp = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={"trigger": "user_request", "user_note": "I need help"},
    )
    assert resp.status_code == 200

    escalated_events = [e for e in captured if e["event"] == "chat_escalated"]
    assert len(escalated_events) == 1
    props = escalated_events[0]["properties"]
    assert props["escalation_reason"] == "user_request"
    assert props["escalation_trigger"] == "user_request"
