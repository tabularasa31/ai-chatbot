"""Tests for escalation flow: awaiting email, followup, manual escalate."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.escalation.service import HumanRequestResult
from backend.models import ContactSession
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key


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
    boilerplate repeated across this file. Returns ``(api_key, tenant_id)``.
    """
    token = register_and_verify_user(tenant, db_session, email=email)
    resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    set_client_openai_key(tenant, token)
    return resp.json()["api_key"], uuid.UUID(resp.json()["id"])


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


def drive(
    tenant: TestClient, api_key: str, session_id: uuid.UUID, *questions: str
) -> list[dict]:
    """POST each question through ``/chat`` in turn; return the parsed JSON
    responses in order. Asserts every turn succeeds (200) as it goes."""
    responses = []
    for question in questions:
        resp = tenant.post(
            "/chat",
            headers={"X-API-Key": api_key},
            json={"session_id": str(session_id), "question": question},
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

    drive(tenant, api_key, chat.session_id, "reach me at user@example.com")

    db_session.refresh(chat)
    db_session.refresh(ticket)
    assert ticket.user_email == "user@example.com"
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
        _async_esc_stub(
            Mock(
                message_to_user="Glad to help. Write here anytime.",
                followup_decision="no",
                tokens_used=3,
            )
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "no thanks"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is False
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert chat.ended_at is None

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
        "backend.chat.service.complete_escalation_openai_turn", _fail_escalation_turn
    )

    followup = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "do you support wildcard domains?"},
    )
    assert followup.status_code == 200
    assert followup.json()["text"] == "Yes, wildcard domains are supported."
    assert followup.json()["chat_ended"] is False


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
        _async_esc_stub(
            Mock(
                message_to_user="Glad to help. Write here anytime.",
                followup_decision="no",
                tokens_used=3,
            )
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "no thanks"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is False

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
        _async_esc_stub(
            Mock(
                message_to_user="Understood, we will continue.",
                followup_decision="yes",
                tokens_used=3,
            )
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
        _async_esc_stub(
            Mock(
                message_to_user="Could you clarify?",
                followup_decision="unclear",
                tokens_used=2,
            )
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


@pytest.mark.escalation
def test_chat_followup_new_question_gets_rag_answer_same_turn(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for prod session 0a730bc1-0db6-4e0b-84b6-bd0eccfbbda1:
    a new question during ``escalation_followup_pending`` must get a real
    RAG answer this same turn, not the canned handoff reply."""
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

    token = register_and_verify_user(tenant, db_session, email="follow-newq@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow NewQ Tenant"},
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
    emb = Embedding(
        document_id=doc.id,
        chunk_text="Wildcard domains are supported on all plans",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]
    mock_openai_client.chat.completions.create.side_effect = (
        _chat_completion_side_effect("Yes, wildcard domains are supported.")
    )

    async def _gate_new_question(**kwargs):
        return ("new_question", 7)

    monkeypatch.setattr(
        "backend.chat.service.classify_followup_reply", _gate_new_question
    )

    async def _fail_full_turn(**kwargs):
        raise AssertionError(
            "new-question follow-up must be answered by RAG, not the "
            "full-turn escalation LLM"
        )

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn", _fail_full_turn
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={
            "session_id": str(chat.session_id),
            "question": "do you support wildcard domain names?",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Yes, wildcard domains are supported."
    assert data.get("chat_ended") is False
    # Gate-classifier tokens carried into the RAG turn (completion mocked at 0).
    assert data["tokens_used"] == 7

    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert chat.ended_at is None


@pytest.mark.escalation
def test_chat_legacy_ended_at_chat_is_answered_normally(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows closed before the closed-chat state was removed behave like open
    conversations: no "already closed" reply, the question reaches RAG."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding

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

    doc = Document(
        tenant_id=tenant_id,
        filename="legacy.md",
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
            chunk_text="Legacy answer",
            vector=None,
            metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
        )
    )
    db_session.commit()
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "Legacy answer"
    )

    async def _fail_escalation_turn(**kwargs):
        raise AssertionError("a legacy ended_at chat must not enter the escalation FSM")

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn", _fail_escalation_turn
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "hello again"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is False
    assert response.json()["text"] == "Legacy answer"


# ---------------------------------------------------------------------------
# Escalation-FSM scenarios migrated from tests/test_chat_handlers_escalation.py
# (see the test-audit notes): each drives /chat end-to-end instead of calling
# EscalationStateMachine directly, stubbing only the classifier/LLM boundary
# functions the FSM already awaits (detect_human_request,
# classify_pre_confirm_reply, complete_escalation_openai_turn).
# ---------------------------------------------------------------------------


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

    assert resp["chat_ended"] is False
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
def test_explicit_request_without_content_opens_awaiting_request_elicitation(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare "connect me to a human" with nothing to forward yet asks for the
    actual question instead of minting an empty ticket."""
    from backend.chat.handlers.escalation import _AWAITING_REQUEST_CANONICAL_TEXT
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="explicit-no-content@example.com", name="Explicit No Content"
    )
    chat = _make_chat(db_session, tenant_id)
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=False,
                human_request_explicit=True,
            )
        ),
    )

    [resp] = drive(tenant, api_key, chat.session_id, "connect me to a human")

    assert resp["text"] == _AWAITING_REQUEST_CANONICAL_TEXT
    assert resp["chat_ended"] is False
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is True
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


@pytest.mark.escalation
def test_pre_confirm_repeated_unclear_never_auto_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for 86exn3x7c: a second consecutive "unclear" reply to the
    pre_confirm offer must re-ask, never get silently promoted to a "yes" and
    mint a ticket."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email="pre-confirm-unclear-twice@example.com",
        name="Pre Confirm Unclear Twice",
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
        user_context={"escalation_followup_clarify": True},
    )
    monkeypatch.setattr(
        "backend.chat.service.classify_pre_confirm_reply", _async_esc_stub(("unclear", 0))
    )

    [resp] = drive(tenant, api_key, chat.session_id, "wait, what do you mean by forwarding?")

    assert resp["chat_ended"] is False
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is True
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
        "backend.chat.service.classify_pre_confirm_reply", _async_esc_stub((None, 0))
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

    assert resp["chat_ended"] is False
    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.user_request
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False


@pytest.mark.escalation
@pytest.mark.xfail(
    strict=True,
    reason=(
        "should_rotate() (backend/chat/rotation.py) unconditionally rotates a "
        "chat once session_ended_event_at is set, exempting only "
        "escalation_awaiting_ticket_id — not escalation_followup_pending — so "
        "the next turn lands on a brand-new Chat row before the FSM's "
        "stale-followup branch in _handle_followup_yes_no ever runs; that "
        "branch is dead code through the app today (real gap, not a test bug)"
    ),
)
def test_stale_followup_falls_through_to_rag_after_session_ended(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up prompt left pending across an inactivity gap must not eat a
    genuine new question: once the sweeper reports the session ended, the gate
    clears and the new question gets a fresh RAG answer, not the yes/no
    classifier."""
    from datetime import UTC, datetime

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

    monkeypatch.setattr("backend.chat.service.complete_escalation_openai_turn", _fail_if_classified)

    [resp] = drive(
        tenant, api_key, chat.session_id, "why was the A www record not added to the list?"
    )

    assert resp["text"] == "The A record was added."
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert (chat.user_context or {}).get("escalation_followup_clarify") is None


@pytest.mark.escalation
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Same rotation gap as test_stale_followup_falls_through_to_rag_after_"
        "session_ended: should_rotate() rotates away from this chat before "
        "the FSM runs, so the ticket lands on a fresh Chat row rather than "
        "clearing escalation_followup_pending on this one — dead code path "
        "through the app, not a test bug"
    ),
)
def test_stale_followup_with_explicit_human_request_still_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale follow-up must not swallow an explicit "connect me to a human":
    it still escalates immediately rather than falling through to RagHandler."""
    from datetime import UTC, datetime

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

    assert resp["chat_ended"] is False
    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.user_request
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False


@pytest.mark.smoke
@pytest.mark.escalation
def test_pre_confirm_yes_creates_ticket(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit "yes" on the pre_confirm offer must actually create the
    ticket and hand off — the one branch of the pre_confirm gate this file did
    not exercise through the app at all before this test."""
    from backend.models import EscalationTicket, EscalationTrigger

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="pre-confirm-yes@example.com", name="Pre Confirm Yes"
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
        "backend.chat.service.classify_pre_confirm_reply", _async_esc_stub(("yes", 5))
    )

    [resp] = drive(tenant, api_key, chat.session_id, "yes please")

    assert resp["chat_ended"] is False
    assert resp.get("ticket_number")
    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.trigger == EscalationTrigger.low_similarity
    assert ticket.primary_question == "my widget won't render"
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False


@pytest.mark.smoke
@pytest.mark.escalation
def test_repeat_explicit_request_threads_onto_open_ticket_without_new_row(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the incident where one conversation produced six ESC
    numbers in 79 seconds: repeating an explicit human request must thread
    onto the chat's existing open ticket instead of minting a fresh one."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="repeat-explicit@example.com", name="Repeat Explicit"
    )
    chat = _make_chat(db_session, tenant_id)
    existing = _make_open_ticket(
        db_session,
        tenant_id,
        chat,
        primary_question="дай мне телефон или почту службы поддержки",
        user_email="user@example.com",
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
        tenant, api_key, chat.session_id, "дай мне телефон или почту службы поддержки"
    )

    assert resp["ticket_number"] == existing.ticket_number
    tickets = (
        db_session.query(EscalationTicket).filter(EscalationTicket.chat_id == chat.id).all()
    )
    assert len(tickets) == 1


@pytest.mark.escalation
def test_repeat_request_after_operator_answer_sets_requested_again_at(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answered, handed back to the bot, asked again: a repeat request after an
    operator reply must re-queue with a fresh ``requested_again_at``."""
    from backend.models import EscalationStatus, Message, MessageRole

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="repeat-after-answer@example.com", name="Repeat After Answer"
    )
    chat = _make_chat(db_session, tenant_id)
    ticket = _make_open_ticket(
        db_session,
        tenant_id,
        chat,
        status=EscalationStatus.in_progress,
        user_email="user@example.com",
    )
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

    drive(tenant, api_key, chat.session_id, "I need a person again")

    db_session.refresh(ticket)
    assert ticket.status is EscalationStatus.in_progress
    assert ticket.requested_again_at is not None
    assert ticket.requested_again_at >= ticket.created_at


@pytest.mark.escalation
def test_repeat_request_while_unanswered_keeps_original_wait(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeat request while the ticket is still unanswered must not reset the
    visitor's wait — ``requested_again_at`` stays null."""
    from backend.models import EscalationStatus

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="repeat-unanswered@example.com", name="Repeat Unanswered"
    )
    chat = _make_chat(db_session, tenant_id)
    ticket = _make_open_ticket(
        db_session,
        tenant_id,
        chat,
        status=EscalationStatus.in_progress,
        user_email="user@example.com",
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

    drive(tenant, api_key, chat.session_id, "I need a person again")

    db_session.refresh(ticket)
    assert ticket.requested_again_at is None


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
def test_awaiting_request_then_substantive_message_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the user supplies the concrete question, the parked awaiting-request
    state escalates with that content and clears the flag."""
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant, db_session, email="awaiting-then-content@example.com", name="Awaiting Then Content"
    )
    chat = _make_chat(db_session, tenant_id, escalation_awaiting_request=True)
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(human_request=False, message_has_request_content=True)
        ),
    )

    [resp] = drive(tenant, api_key, chat.session_id, "my invoice shows the wrong amount")

    assert resp["chat_ended"] is False
    ticket = (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).one()
    )
    assert ticket.primary_question == "my invoice shows the wrong amount"
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is False


@pytest.mark.escalation
def test_awaiting_request_repeated_bare_ping_re_elicits(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still no concrete question, still asking for a human: re-ask and stay
    parked, never mint a ticket."""
    from backend.chat.handlers.escalation import _AWAITING_REQUEST_CANONICAL_TEXT
    from backend.models import EscalationTicket

    api_key, tenant_id = _register_tenant_with_key(
        tenant,
        db_session,
        email="awaiting-repeated-ping@example.com",
        name="Awaiting Repeated Ping",
    )
    chat = _make_chat(db_session, tenant_id, escalation_awaiting_request=True)
    monkeypatch.setattr(
        "backend.chat.service.detect_human_request",
        _human_request_sequence(
            HumanRequestResult(
                human_request=True,
                message_has_request_content=False,
                human_request_explicit=True,
            )
        ),
    )

    [resp] = drive(tenant, api_key, chat.session_id, "is anyone there??")

    assert resp["text"] == _AWAITING_REQUEST_CANONICAL_TEXT
    db_session.refresh(chat)
    assert chat.escalation_awaiting_request is True
    assert (
        db_session.query(EscalationTicket).filter(EscalationTicket.tenant_id == tenant_id).count()
        == 0
    )


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
        "backend.chat.persistence.record_user_session_turn",
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


@pytest.mark.escalation
def test_manual_escalate_requires_api_key(tenant: TestClient) -> None:
    response = tenant.post(
        f"/chat/{uuid.uuid4()}/escalate",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 401


@pytest.mark.escalation
def test_manual_escalate_invalid_api_key(tenant: TestClient) -> None:
    response = tenant.post(
        f"/chat/{uuid.uuid4()}/escalate",
        headers={"X-API-Key": "bad-key"},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 401


@pytest.mark.escalation
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


@pytest.mark.escalation
def test_manual_escalate_missing_session_returns_404(
    tenant: TestClient,
    db_session: Session,
) -> None:
    api_key, _tenant_id = _register_tenant_with_key(
        tenant, db_session, email="manual-404@example.com", name="Manual 404"
    )

    response = tenant.post(
        f"/chat/{uuid.uuid4()}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 404


@pytest.mark.escalation
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

    async def _raise_api_error(*args, **kwargs):
        raise APIError("Service unavailable", request=Mock(), body=None)

    monkeypatch.setattr("backend.chat.routes.perform_manual_escalation", _raise_api_error)

    response = tenant.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 503


@pytest.mark.escalation
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

    async def _fake_manual_escalation(*args, **kwargs):
        return ("Escalated.", "ESC-0009")

    monkeypatch.setattr(
        "backend.chat.routes.perform_manual_escalation",
        _fake_manual_escalation,
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
