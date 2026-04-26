"""Tests for the /chat HTTP endpoint."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import LocalizationResult
from backend.chat.service import RetrievalContext
from backend.guards.reject_response import RejectReason, build_reject_response
from tests.chat_utils import _chat_completion_side_effect
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_chat_success(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Valid api_key + question → get answer back."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="chat@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Chat Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="chat.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="The answer is 42",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "The answer is 42",
        total_tokens=50,
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "The answer is 42"
    assert "session_id" in data
    assert data["source_documents"] == [str(doc.id)]
    assert data["tokens_used"] == 50
    assert data.get("chat_ended") is False


def test_chat_creates_messages_in_db(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """After chat, messages saved to DB."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(tenant, db_session, email="msg@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Msg Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="msg.md",
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
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Reply"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=10)

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 200
    session_id = uuid.UUID(response.json()["session_id"])

    chat = db_session.query(Chat).filter(Chat.session_id == session_id).first()
    assert chat is not None
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 2
    roles = [m.role.value for m in messages]
    assert "user" in roles
    assert "assistant" in roles
    user_message = next(m for m in messages if m.role.value == "user")
    assert user_message.content == "Hello"
    assert user_message.content_original_encrypted is not None
    assert user_message.content_redacted == "Hello"


def test_chat_invalid_api_key(tenant: TestClient) -> None:
    """Wrong api_key → 401."""
    response = tenant.post(
        "/chat",
        headers={"X-API-Key": "invalid-key-12345"},
        json={"question": "Hello"},
    )
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


def test_chat_missing_api_key(tenant: TestClient) -> None:
    """No X-API-Key header → 401."""
    response = tenant.post(
        "/chat",
        json={"question": "Hello"},
    )
    assert response.status_code == 401


def test_chat_without_openai_key(tenant: TestClient, db_session: Session) -> None:
    """400 if tenant has no OpenAI API key configured."""
    token = register_and_verify_user(tenant, db_session, email="nokey@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Key Tenant"},
    )
    api_key = cl_resp.json()["api_key"]
    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 400
    assert "OpenAI API key" in response.json()["detail"]


def test_chat_empty_question_returns_default_greeting(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Empty first message returns the default greeting."""
    token = register_and_verify_user(tenant, db_session, email="empty@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": ""},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == (
        "I'm the Empty Tenant assistant and can help with documentation, "
        "product setup, integrations, and finding the right information. Ask your question."
    )
    assert data["source_documents"] == []
    assert data["chat_ended"] is False


def test_chat_empty_question_uses_browser_locale_for_greeting(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="empty-locale@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Greeting Locale Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]

    monkeypatch.setattr(
        "backend.chat.handlers.greeting.generate_greeting_in_language_result",
        lambda **kwargs: LocalizationResult(
            text="Je suis l'assistant Greeting Locale Tenant. Posez votre question.",
            tokens_used=9,
        ),
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key, "X-Browser-Locale": "fr-FR"},
        json={"question": ""},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Je suis l'assistant Greeting Locale Tenant. Posez votre question."
    assert data["tokens_used"] == 9


def test_chat_empty_followup_after_started_session_is_rejected(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="empty-followup@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Followup Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]

    first = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": ""},
    )
    assert first.status_code == 200
    session_id = first.json()["session_id"]

    second = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "", "session_id": session_id},
    )
    assert second.status_code == 422
    assert second.json()["detail"] == "Question is required"


def test_chat_no_embeddings(
    mock_openai_client: Mock, tenant: TestClient, db_session: Session
) -> None:
    """No docs uploaded → answer is 'I don't have information'."""
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    token = register_and_verify_user(tenant, db_session, email="noemb@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Emb Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "How does your product work?"},
    )
    assert response.status_code == 200
    data = response.json()
    expected_prefix = build_reject_response(reason=RejectReason.INSUFFICIENT_CONFIDENCE, profile=None)
    assert data["text"].startswith(expected_prefix)
    assert "A support ticket was created for you." in data["text"]
    assert data["ticket_number"] == "ESC-0001"
    # English response_language keeps the canonical fallback text, so only the
    # mocked escalation handoff contributes tokens.
    assert data["tokens_used"] == 15
    assert data.get("chat_ended") is False


def test_chat_uses_context(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Mock search returns chunk, verify it's in prompt."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(tenant, db_session, email="ctx@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Ctx Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    doc = Document(
        tenant_id=tenant_id,
        filename="ctx.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="Secret answer: 99",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="The secret number is 99.",
        vector=None,
        metadata_json={"vector": [0.9] + [0.0] * 1535, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.9] + [0.0] * 1535)
    ]
    mock_openai_client.chat.completions.create.side_effect = _chat_completion_side_effect(
        "99",
        total_tokens=5,
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the secret?"},
    )
    assert response.status_code == 200
    assert "99" in response.json()["text"]
    # Verify the chunk was passed to generate_answer (system + user, chunks in user message)
    call_args = next(
        call
        for call in mock_openai_client.chat.completions.create.call_args_list
        if len(call.kwargs.get("messages", [])) >= 2
        and "The secret number is 99" in call.kwargs["messages"][1]["content"]
    )
    messages = call_args.kwargs["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "The secret number is 99" in messages[1]["content"]


def test_chat_hybrid_high_vector_confidence_does_not_auto_escalate(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="hybridsafe@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Hybrid Safe Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    doc_id = uuid.uuid4()

    monkeypatch.setattr(
        "backend.chat.service.retrieve_context",
        lambda *args, **kwargs: RetrievalContext(
            chunk_texts=["Maximum 100 documents per account."],
            document_ids=[doc_id],
            scores=[0.0328],
            mode="hybrid",
            best_rank_score=0.0328,
            best_confidence_score=0.94,
            confidence_source="vector_similarity",
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.generate_answer",
        lambda *args, **kwargs: ("Максимум 100 документов можно загрузить на аккаунт.", 8),
    )
    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *args, **kwargs: {"is_valid": True, "confidence": 0.99, "reason": "grounded"},
    )

    def _unexpected_ticket(*args, **kwargs):
        raise AssertionError("create_escalation_ticket should not be called for grounded hybrid answers")

    monkeypatch.setattr("backend.chat.service.create_escalation_ticket", _unexpected_ticket)

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "сколько максимум документов можно загрузить?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Максимум 100 документов можно загрузить на аккаунт."
    assert "[[escalation_ticket:" not in data["text"]
    assert data["source_documents"] == [str(doc_id)]


def test_chat_openai_unavailable_503(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
) -> None:
    """OpenAI API error → 503."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    from openai import APIError

    token = register_and_verify_user(tenant, db_session, email="err@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Err Tenant"},
    )
    set_client_openai_key(tenant, token)
    api_key = cl_resp.json()["api_key"]
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        tenant_id=tenant_id,
        filename="err.md",
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
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    mock_openai_client.chat.completions.create.side_effect = APIError(
        "Service unavailable",
        request=Mock(),
        body=None,
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the pricing plan?"},
    )
    assert response.status_code == 503
    assert "OpenAI" in response.json()["detail"]
