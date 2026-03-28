"""Tests for chat API (RAG pipeline)."""

from __future__ import annotations

import uuid
from unittest.mock import Mock
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key
from backend.chat.service import (
    FALLBACK_LOW_CONFIDENCE_ANSWER,
    RetrievalContext,
    build_rag_messages,
    build_rag_prompt,
    generate_answer,
    retrieve_context,
    process_chat_message,
    validate_answer,
)


# --- Unit tests ---


def test_build_rag_prompt() -> None:
    """build_rag_prompt produces correct format with chunks."""
    chunks = ["chunk1", "chunk2", "chunk3"]
    result = build_rag_prompt("What is X?", chunks)
    assert "Hard limits" in result
    assert "[Response level: standard]" in result
    assert "technical support agent" in result
    assert "Answer based ONLY on the provided context" in result
    assert "chunk1" in result
    assert "chunk2" in result
    assert "chunk3" in result
    assert "---" in result
    assert "Question: What is X?" in result
    assert "Answer:" in result


def test_build_rag_prompt_empty_chunks() -> None:
    """build_rag_prompt handles empty chunks."""
    result = build_rag_prompt("Q?", [])
    assert "Question: Q?" in result
    assert "(none)" in result
    assert "[Response level: standard]" in result


def test_build_rag_messages_splits_system_and_user_parts() -> None:
    system_prompt, user_message = build_rag_messages("What is X?", ["chunk1", "chunk2"])
    assert "Hard limits" in system_prompt
    assert "Context:" not in system_prompt
    assert "chunk1" in user_message
    assert "chunk2" in user_message
    assert "Question: What is X?" in user_message


def test_generate_answer_no_context(mock_openai_client: Mock) -> None:
    """Empty chunks → fallback message, no OpenAI call."""
    answer, tokens = generate_answer("question", [], api_key="sk-test")
    assert answer == "I don't have information about this."
    assert tokens == 0
    mock_openai_client.chat.completions.create.assert_not_called()


def test_validate_answer_no_context(mock_openai_client: Mock) -> None:
    """Empty context → invalid + no_context; no OpenAI call."""
    result = validate_answer("q", "a", [], api_key="sk-test")
    assert result == {"is_valid": False, "confidence": 0.0, "reason": "no_context"}
    mock_openai_client.chat.completions.create.assert_not_called()


def test_validate_answer_openai_error_non_blocking(mock_openai_client: Mock) -> None:
    """OpenAI/JSON errors → validation_skipped, does not raise."""
    mock_openai_client.chat.completions.create.side_effect = RuntimeError("boom")
    result = validate_answer("q", "a", ["chunk"], api_key="sk-test")
    assert result["is_valid"] is True
    assert result["confidence"] == 1.0
    assert result["reason"] == "validation_skipped"


def test_generate_answer_with_context(mock_openai_client: Mock) -> None:
    """With chunks, calls OpenAI and returns answer + tokens."""
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)

    answer, tokens = generate_answer("What?", ["chunk1"], api_key="sk-test")
    assert answer == "The answer is 42"
    assert tokens == 100
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"][0]["role"] == "system"
    assert call_kwargs["messages"][1]["role"] == "user"
    assert call_kwargs["temperature"] == 0.2
    assert call_kwargs["max_tokens"] == 500


def test_generate_answer_traces_summary_not_full_prompt(mock_openai_client: Mock) -> None:
    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_input: object | None = None
            self.generation_handle = FakeGeneration()

        def generation(self, **kwargs: object) -> FakeGeneration:
            self.generation_input = kwargs["input"]
            return self.generation_handle

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)
    trace = FakeTrace()
    from backend.chat import service as chat_service

    assert chat_service.settings.observability_capture_full_prompts is False

    generate_answer("What?", ["secret internal KB chunk"], api_key="sk-test", trace=trace)

    assert trace.generation_input == {
        "question_preview": "What?",
        "context_chunk_count": 1,
    }


def test_generate_answer_can_trace_full_prompt_when_enabled(
    mock_openai_client: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_input: object | None = None
            self.generation_metadata: object | None = None
            self.generation_handle = FakeGeneration()

        def generation(self, **kwargs: object) -> FakeGeneration:
            self.generation_input = kwargs["input"]
            self.generation_metadata = kwargs["metadata"]
            return self.generation_handle

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)
    trace = FakeTrace()

    monkeypatch.setattr(
        "backend.chat.service.settings.observability_capture_full_prompts",
        True,
    )

    generate_answer("What?", ["secret internal KB chunk"], api_key="sk-test", trace=trace)

    system_prompt, user_message = build_rag_messages("What?", ["secret internal KB chunk"])
    assert trace.generation_input == [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    assert trace.generation_metadata == {
        "temperature": 0.2,
        "max_tokens": 500,
        "context_chunk_count": 1,
        "captures_full_prompt": True,
        "finish_reason_expected": "stop_or_length",
        "system_prompt": system_prompt,
        "context_chunks": ["secret internal KB chunk"],
    }


def test_generate_answer_ends_generation_on_openai_error(mock_openai_client: Mock) -> None:
    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_handle = FakeGeneration()

        def generation(self, **kwargs: object) -> FakeGeneration:
            return self.generation_handle

    trace = FakeTrace()
    mock_openai_client.chat.completions.create.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        generate_answer("What?", ["chunk1"], api_key="sk-test", trace=trace)

    assert len(trace.generation_handle.end_calls) == 1
    end_call = trace.generation_handle.end_calls[0]
    assert end_call["level"] == "ERROR"
    assert end_call["status_message"] == "boom"
    assert "duration_ms" in end_call["metadata"]


def test_process_chat_message_ends_followup_span_on_exception(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, Client, EscalationTicket, EscalationTrigger, EscalationStatus

    class FakeSpan:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.followup_span = FakeSpan()

        def span(self, **kwargs: object) -> FakeSpan:
            if kwargs["name"] == "escalation-followup":
                return self.followup_span
            return FakeSpan()

        def update(self, **kwargs: object) -> None:
            return None

    token = register_and_verify_user(client, db_session, email="trace-followup@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Client"},
    )
    set_client_openai_key(client, token)
    client_row = db_session.get(Client, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None

    chat = Chat(
        client_id=client_row.id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        client_id=client_row.id,
        ticket_number="ESC-0001",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: fake_trace)
    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        process_chat_message(
            client_row.id,
            "no thanks",
            chat.session_id,
            db_session,
            api_key=cl_resp.json()["api_key"],
        )

    assert fake_trace.followup_span.end_calls == [
        {
            "output": {"error": True},
            "level": "ERROR",
            "status_message": "boom",
        }
    ]


def test_process_chat_message_adds_variant_summary_to_trace(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Client
    from backend.search.service import build_reliability_assessment

    class FakeSpan:
        def end(self, **kwargs: object) -> None:
            return None

    class FakeTrace:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, object]] = []

        def span(self, **kwargs: object) -> FakeSpan:
            return FakeSpan()

        def update(self, **kwargs: object) -> None:
            self.update_calls.append(kwargs)

        def promote(self, **kwargs: object) -> None:
            return None

    token = register_and_verify_user(client, db_session, email="trace-chat@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Chat Client"},
    )
    set_client_openai_key(client, token)
    client_row = db_session.get(Client, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: fake_trace)
    monkeypatch.setattr(
        "backend.chat.service.retrieve_context",
        lambda *args, **kwargs: RetrievalContext(
            chunk_texts=["reset password in settings"],
            document_ids=[uuid.uuid4()],
            scores=[0.93],
            mode="hybrid",
            best_rank_score=0.93,
            best_confidence_score=0.91,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.93, result_count=5),
            variant_mode="multi",
            query_variant_count=3,
            extra_embedded_queries=2,
            extra_embedding_api_requests=0,
            extra_vector_search_calls=2,
            bm25_expansion_mode="symmetric_variants",
            bm25_query_variant_count=2,
            bm25_variant_eval_count=2,
            extra_bm25_variant_evals=1,
            bm25_merged_hit_count_before_cap=4,
            bm25_merged_hit_count_after_cap=3,
            retrieval_duration_ms=18.4,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.generate_answer",
        lambda *args, **kwargs: ("Use the reset link in settings.", 17),
    )
    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *args, **kwargs: {"is_valid": True, "confidence": 0.95},
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *args, **kwargs: (False, None),
    )

    answer, _, tokens_used, chat_ended = process_chat_message(
        client_row.id,
        "How do I reset my password?",
        uuid.uuid4(),
        db_session,
        api_key=cl_resp.json()["api_key"],
    )

    assert answer == "Use the reset link in settings."
    assert tokens_used == 17
    assert chat_ended is False
    assert fake_trace.update_calls[-1]["metadata"]["variant_mode"] == "multi"
    assert fake_trace.update_calls[-1]["metadata"]["query_variant_count"] == 3
    assert fake_trace.update_calls[-1]["metadata"]["extra_embedded_queries"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["extra_embedding_api_requests"] == 0
    assert fake_trace.update_calls[-1]["metadata"]["extra_vector_search_calls"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["bm25_expansion_mode"] == "symmetric_variants"
    assert fake_trace.update_calls[-1]["metadata"]["bm25_query_variant_count"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["bm25_variant_eval_count"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["extra_bm25_variant_evals"] == 1
    assert fake_trace.update_calls[-1]["metadata"]["bm25_merged_hit_count_before_cap"] == 4
    assert fake_trace.update_calls[-1]["metadata"]["bm25_merged_hit_count_after_cap"] == 3
    assert fake_trace.update_calls[-1]["metadata"]["retrieval_duration_ms"] == 18.4
    assert fake_trace.update_calls[-1]["metadata"]["reliability"] == {
        "base_score": "high",
        "score": "high",
        "cap": None,
        "cap_reason": None,
        "signals": [],
        "evidence": {},
    }
    assert fake_trace.update_calls[-1]["tags"] == ["variants:multi"]


# --- API tests ---


def test_chat_success(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Valid api_key + question → get answer back."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="chat@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Chat Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=50)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "The answer is 42"
    assert "session_id" in data
    assert data["source_documents"] == [str(doc.id)]
    assert data["tokens_used"] == 50
    assert data.get("chat_ended") is False


def test_chat_creates_messages_in_db(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """After chat, messages saved to DB."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(client, db_session, email="msg@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Msg Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
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


def test_chat_invalid_api_key(client: TestClient) -> None:
    """Wrong api_key → 401."""
    response = client.post(
        "/chat",
        headers={"X-API-Key": "invalid-key-12345"},
        json={"question": "Hello"},
    )
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


def test_chat_missing_api_key(client: TestClient) -> None:
    """No X-API-Key header → 401."""
    response = client.post(
        "/chat",
        json={"question": "Hello"},
    )
    assert response.status_code == 401


def test_chat_without_openai_key(client: TestClient, db_session: Session) -> None:
    """400 if client has no OpenAI API key configured."""
    token = register_and_verify_user(client, db_session, email="nokey@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Key Client"},
    )
    api_key = cl_resp.json()["api_key"]
    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 400
    assert "OpenAI API key" in response.json()["detail"]


def test_chat_empty_question(client: TestClient, db_session: Session) -> None:
    """Empty string question → 422."""
    token = register_and_verify_user(client, db_session, email="empty@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": ""},
    )
    assert response.status_code == 422


def test_chat_no_embeddings(
    mock_openai_client: Mock, client: TestClient, db_session: Session
) -> None:
    """No docs uploaded → answer is 'I don't have information'."""
    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    token = register_and_verify_user(client, db_session, email="noemb@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Emb Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Anything"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"].startswith(FALLBACK_LOW_CONFIDENCE_ANSWER)
    assert "A support ticket was created for you." in data["answer"]
    assert "[[escalation_ticket:ESC-0001]]" in data["answer"]
    assert data["tokens_used"] == 15
    assert data.get("chat_ended") is False


def test_chat_uses_context(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Mock search returns chunk, verify it's in prompt."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="ctx@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Ctx Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    doc = Document(
        client_id=client_id,
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
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="99"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "What is the secret?"},
    )
    assert response.status_code == 200
    assert "99" in response.json()["answer"]
    # Verify the chunk was passed to chat (via build_rag_prompt)
    call_args = mock_openai_client.chat.completions.create.call_args
    messages = call_args.kwargs["messages"]
    assert len(messages) == 1
    assert "The secret number is 99" in messages[0]["content"]


def test_chat_hybrid_high_vector_confidence_does_not_auto_escalate(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(client, db_session, email="hybridsafe@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Hybrid Safe Client"},
    )
    set_client_openai_key(client, token)
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

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "сколько максимум документов можно загрузить?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Максимум 100 документов можно загрузить на аккаунт."
    assert "[[escalation_ticket:" not in data["answer"]
    assert data["source_documents"] == [str(doc_id)]


def test_retrieve_context_propagates_reliability_cap_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import SearchResultBundle, build_reliability_assessment

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.chat.service.search_similar_chunks_detailed",
        lambda *args, **kwargs: SearchResultBundle(
            results=[(embedding, 0.88)],
            best_vector_similarity=0.88,
            query_variants=["reset password"],
            reliability=build_reliability_assessment(
                top_score=0.88,
                result_count=5,
                source_overlap_detected=True,
            ),
        ),
    )

    class FakeBind:
        url = "postgresql://test"

    class FakeDB:
        bind = FakeBind()

    context = retrieve_context(
        client_id=uuid.uuid4(),
        question="reset password",
        db=FakeDB(),
        api_key="sk-test",
    )

    assert context.source_overlap_detected is True
    assert context.source_overlap_pairs == []
    assert context.conflicts_found is True
    assert context.conflict_pairs == []
    assert context.reliability_score == "medium"
    assert context.reliability_cap_reason == "source_overlap"


def test_retrieve_context_uses_vector_confidence_and_lexical_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import SearchResultBundle

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="secret number explanation",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.chat.service.search_similar_chunks_detailed",
        lambda *args, **kwargs: SearchResultBundle(
            results=[(embedding, 0.77)],
            best_vector_similarity=0.0,
            best_keyword_score=1.0,
            has_lexical_signal=True,
            query_variants=["secret number"],
        ),
    )

    class FakeBind:
        url = "sqlite://test"

    class FakeDB:
        bind = FakeBind()

    context = retrieve_context(
        client_id=uuid.uuid4(),
        question="secret number",
        db=FakeDB(),
        api_key="sk-test",
    )

    assert context.mode == "hybrid"
    assert context.best_rank_score == 0.77
    assert context.best_confidence_score == 0.0
    assert context.confidence_source == "vector_similarity"


def test_chat_session_continuity(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Two messages with same session_id → same chat in DB."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(client, db_session, email="cont@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Cont Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])
    session_id = str(uuid.uuid4())

    doc = Document(
        client_id=client_id,
        filename="cont.md",
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
        Mock(message=Mock(content="A1"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    r1 = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q1", "session_id": session_id},
    )
    assert r1.status_code == 200

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="A2"))
    ]
    r2 = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Q2", "session_id": session_id},
    )
    assert r2.status_code == 200

    chat = db_session.query(Chat).filter(
        Chat.session_id == uuid.UUID(session_id),
    ).first()
    assert chat is not None
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 4  # Q1, A1, Q2, A2


def test_chat_new_session_auto_generated(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """No session_id → auto-generated UUID returned."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="auto@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Auto Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="auto.md",
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
        Mock(message=Mock(content="Hi"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=3)

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hi"},
    )
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    uuid.UUID(session_id)  # valid UUID


def test_get_history_success(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Get chat history after conversation."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="hist@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Hist Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="hist.md",
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
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    chat_resp = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    session_id = chat_resp.json()["session_id"]

    hist_resp = client.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert hist_resp.status_code == 200
    data = hist_resp.json()
    assert data["session_id"] == session_id
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "Hello"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Reply"


def test_get_history_wrong_user(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """User B tries to get user A's session → 404."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token_a = register_and_verify_user(client, db_session, email="userA@example.com")
    cl_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    set_client_openai_key(client, token_a)
    api_key_a = cl_a.json()["api_key"]
    client_id_a = uuid.UUID(cl_a.json()["id"])
    session_id = str(uuid.uuid4())

    doc = Document(
        client_id=client_id_a,
        filename="a.md",
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
        Mock(message=Mock(content="A"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=1)

    client.post(
        "/chat",
        headers={"X-API-Key": api_key_a},
        json={"question": "Hi", "session_id": session_id},
    )

    token_b = register_and_verify_user(client, db_session, email="userB@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )

    hist_resp = client.get(
        f"/chat/history/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert hist_resp.status_code == 404


def test_get_history_unauthenticated(client: TestClient) -> None:
    """No JWT → 401."""
    session_id = str(uuid.uuid4())
    response = client.get(f"/chat/history/{session_id}")
    assert response.status_code == 401


# --- Sessions / logs inbox endpoint tests ---


def test_get_sessions_returns_only_own_client_sessions(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/sessions returns only sessions for the authenticated client."""
    from backend.models import Chat, Message, MessageRole

    token_a = register_and_verify_user(
        client, db_session, email="sessions_a@example.com"
    )
    cl_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    set_client_openai_key(client, token_a)
    client_id_a = uuid.UUID(cl_a.json()["id"])

    # Create chat + messages for client A
    chat_a = Chat(client_id=client_id_a, session_id=uuid.uuid4())
    db_session.add(chat_a)
    db_session.commit()
    db_session.refresh(chat_a)
    msg1 = Message(chat_id=chat_a.id, role=MessageRole.user, content="Q1")
    msg2 = Message(chat_id=chat_a.id, role=MessageRole.assistant, content="A1")
    db_session.add_all([msg1, msg2])
    db_session.commit()

    # Create user B and client B with their own session
    token_b = register_and_verify_user(
        client, db_session, email="sessions_b@example.com"
    )
    cl_b = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )
    client_id_b = uuid.UUID(cl_b.json()["id"])
    chat_b = Chat(client_id=client_id_b, session_id=uuid.uuid4())
    db_session.add(chat_b)
    db_session.commit()

    resp = client.get("/chat/sessions", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == str(chat_a.session_id)
    assert data["sessions"][0]["message_count"] == 2
    assert data["sessions"][0]["last_question"] == "Q1"
    assert data["sessions"][0]["last_answer_preview"] == "A1"


def test_get_sessions_sorted_by_last_activity_desc(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/sessions returns sessions sorted by last_activity DESC."""
    from datetime import datetime, timedelta
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(
        client, db_session, email="sessions_sort@example.com"
    )
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Sort Client"},
    )
    client_id = uuid.UUID(cl.json()["id"])

    base_time = datetime.now(timezone.utc)
    chat1 = Chat(client_id=client_id, session_id=uuid.uuid4())
    chat2 = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add_all([chat1, chat2])
    db_session.commit()
    db_session.refresh(chat1)
    db_session.refresh(chat2)

    m1 = Message(chat_id=chat1.id, role=MessageRole.user, content="Q1")
    m2 = Message(chat_id=chat1.id, role=MessageRole.assistant, content="A1")
    m3 = Message(chat_id=chat2.id, role=MessageRole.user, content="Q2")
    m4 = Message(chat_id=chat2.id, role=MessageRole.assistant, content="A2")
    db_session.add_all([m1, m2, m3, m4])
    db_session.commit()

    # Manually set created_at so chat2 is more recent
    from sqlalchemy import update
    from backend.models import Message as MsgModel
    db_session.execute(
        update(MsgModel).where(MsgModel.id == m4.id).values(created_at=base_time + timedelta(hours=1))
    )
    db_session.execute(
        update(MsgModel).where(MsgModel.id == m2.id).values(created_at=base_time)
    )
    db_session.commit()

    resp = client.get("/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 2
    # chat2 (more recent) should be first
    assert data["sessions"][0]["session_id"] == str(chat2.session_id)
    assert data["sessions"][0]["last_question"] == "Q2"
    assert data["sessions"][1]["session_id"] == str(chat1.session_id)
    assert data["sessions"][1]["last_question"] == "Q1"


def test_get_sessions_last_answer_preview_truncated(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """last_answer_preview is truncated to ~120 chars with ... if longer."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(
        client, db_session, email="sessions_preview@example.com"
    )
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Preview Client"},
    )
    client_id = uuid.UUID(cl.json()["id"])

    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    long_answer = "x" * 150
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="Q")
    m2 = Message(chat_id=chat.id, role=MessageRole.assistant, content=long_answer)
    db_session.add_all([m1, m2])
    db_session.commit()

    resp = client.get("/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    preview = data["sessions"][0]["last_answer_preview"]
    assert preview is not None
    assert len(preview) <= 124  # 120 + "..."
    assert preview.endswith("...")


def test_get_session_logs_success(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/logs/session/{id} returns full message list for valid session."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(client, db_session, email="logs@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl.json()["id"])

    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="Hello")
    m2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="Hi there")
    db_session.add_all([m1, m2])
    db_session.commit()

    resp = client.get(
        f"/chat/logs/session/{chat.session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "Hello"
    assert data["messages"][0]["content_original"] is None
    assert data["messages"][0]["content_original_available"] is False
    assert data["messages"][0]["session_id"] == str(chat.session_id)
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][1]["content"] == "Hi there"
    assert data["messages"][0]["created_at"] <= data["messages"][1]["created_at"]


def test_get_session_logs_can_include_original_for_authenticated_owner(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.chat.pii import redact
    from backend.core.crypto import encrypt_value
    from backend.models import Chat, Message, MessageRole, User

    token = register_and_verify_user(client, db_session, email="logs-original@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Original Client"},
    )
    client_id = uuid.UUID(cl.json()["id"])

    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    m1 = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content="email me at user@example.com",
        content_original_encrypted=encrypt_value("email me at user@example.com"),
        content_redacted=redact("email me at user@example.com").redacted_text,
    )
    db_session.add(m1)
    db_session.commit()
    user = db_session.query(User).filter_by(email="logs-original@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = client.get(
        f"/chat/logs/session/{chat.session_id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"][0]["content"] == "email me at [EMAIL]"
    assert data["messages"][0]["content_original"] == "email me at user@example.com"
    assert data["messages"][0]["content_original_available"] is True


def test_get_session_logs_include_original_requires_admin(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(client, db_session, email="logs-no-admin@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs No Admin Client"},
    )
    client_id = uuid.UUID(cl.json()["id"])

    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    db_session.add(Message(chat_id=chat.id, role=MessageRole.user, content="Hello"))
    db_session.commit()

    resp = client.get(
        f"/chat/logs/session/{chat.session_id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_delete_session_original_requires_admin_and_removes_original(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.chat.pii import redact
    from backend.core.crypto import encrypt_value
    from backend.models import Chat, Message, MessageRole, User

    token = register_and_verify_user(client, db_session, email="logs-delete@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Delete Client"},
    )
    client_id = uuid.UUID(cl.json()["id"])

    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content="[EMAIL]",
        content_original_encrypted=encrypt_value("user@example.com"),
        content_redacted=redact("user@example.com").redacted_text,
    )
    db_session.add(msg)
    db_session.commit()

    denied = client.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403

    user = db_session.query(User).filter_by(email="logs-delete@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = client.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 1

    db_session.refresh(msg)
    assert msg.content_original_encrypted is None
    assert msg.content == msg.content_redacted


def test_delete_session_original_clears_legacy_plaintext_when_redacted_missing(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.core.crypto import encrypt_value
    from backend.models import Chat, Message, MessageRole, User

    token = register_and_verify_user(client, db_session, email="logs-delete-empty@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Logs Delete Empty Client"},
    )
    client_id = uuid.UUID(cl.json()["id"])

    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(
        chat_id=chat.id,
        role=MessageRole.user,
        content="plaintext@example.com",
        content_original_encrypted=encrypt_value("plaintext@example.com"),
        content_redacted=None,
    )
    db_session.add(msg)
    db_session.commit()

    user = db_session.query(User).filter_by(email="logs-delete-empty@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = client.post(
        f"/chat/logs/session/{chat.session_id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db_session.refresh(msg)
    assert msg.content_original_encrypted is None
    assert msg.content == ""


def test_get_session_logs_404_wrong_client(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /chat/logs/session/{id} returns 404 if session belongs to another client."""
    from backend.models import Chat, Message, MessageRole

    token_a = register_and_verify_user(client, db_session, email="logsa@example.com")
    cl_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    client_id_a = uuid.UUID(cl_a.json()["id"])
    chat_a = Chat(client_id=client_id_a, session_id=uuid.uuid4())
    db_session.add(chat_a)
    db_session.commit()
    db_session.refresh(chat_a)
    m = Message(chat_id=chat_a.id, role=MessageRole.user, content="Secret")
    db_session.add(m)
    db_session.commit()

    token_b = register_and_verify_user(client, db_session, email="logsb@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )

    resp = client.get(
        f"/chat/logs/session/{chat_a.session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404


def test_get_session_logs_404_nonexistent(
    client: TestClient, db_session: Session
) -> None:
    """GET /chat/logs/session/{id} returns 404 for nonexistent session."""
    token = register_and_verify_user(client, db_session, email="logs404@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Client"},
    )
    fake_id = uuid.uuid4()
    resp = client.get(
        f"/chat/logs/session/{fake_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_get_sessions_requires_auth(client: TestClient) -> None:
    """GET /chat/sessions requires JWT."""
    resp = client.get("/chat/sessions")
    assert resp.status_code == 401


def test_get_session_logs_requires_auth(client: TestClient) -> None:
    """GET /chat/logs/session/{id} requires JWT."""
    resp = client.get(f"/chat/logs/session/{uuid.uuid4()}")
    assert resp.status_code == 401


# --- Feedback endpoint tests ---


def test_set_message_feedback_success_up(
    client: TestClient,
    db_session: Session,
) -> None:
    """Can set feedback=up on assistant message."""
    from backend.models import Chat, Message, MessageFeedback, MessageRole

    token = register_and_verify_user(client, db_session, email="fbup@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="Answer")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = client.post(
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
    client: TestClient,
    db_session: Session,
) -> None:
    """Can set feedback=down with ideal_answer on assistant message."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(client, db_session, email="fbdown@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Down Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="Bad answer")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = client.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down", "ideal_answer": "This is the ideal answer."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback"] == "down"
    assert data["ideal_answer"] == "This is the ideal answer."


def test_set_message_feedback_rejects_user_message(
    client: TestClient,
    db_session: Session,
) -> None:
    """400 if trying to set feedback on user message."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(client, db_session, email="fbuser@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb User Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.user, content="Question")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = client.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down"},
    )
    assert resp.status_code == 400
    assert "assistant" in resp.json()["detail"].lower()


def test_set_message_feedback_requires_auth(
    client: TestClient, db_session: Session
) -> None:
    """401 without JWT."""
    from backend.models import Chat, Message, MessageRole

    token = register_and_verify_user(client, db_session, email="fbauth@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fb Auth Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    msg = Message(chat_id=chat.id, role=MessageRole.assistant, content="A")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    resp = client.post(
        f"/chat/messages/{msg.id}/feedback",
        json={"feedback": "up"},
    )
    assert resp.status_code == 401


def test_set_message_feedback_wrong_client(
    client: TestClient,
    db_session: Session,
) -> None:
    """404 if trying to set feedback for message from another client."""
    from backend.models import Chat, Message, MessageRole

    token_a = register_and_verify_user(client, db_session, email="fbwca@example.com")
    cl_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    client_id_a = uuid.UUID(cl_a.json()["id"])
    chat_a = Chat(client_id=client_id_a, session_id=uuid.uuid4())
    db_session.add(chat_a)
    db_session.commit()
    db_session.refresh(chat_a)
    msg = Message(chat_id=chat_a.id, role=MessageRole.assistant, content="A")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    token_b = register_and_verify_user(client, db_session, email="fbwcb@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )

    resp = client.post(
        f"/chat/messages/{msg.id}/feedback",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"feedback": "down"},
    )
    assert resp.status_code == 404


def test_list_bad_answers_empty(
    client: TestClient, db_session: Session
) -> None:
    """Return empty items for new client."""
    token = register_and_verify_user(client, db_session, email="badempty@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    resp = client.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 0


def test_list_bad_answers_returns_items_for_client(
    client: TestClient,
    db_session: Session,
) -> None:
    """Create chat with user & assistant messages, mark some as down, ensure /chat/bad-answers returns them."""
    from backend.models import Chat, Message, MessageFeedback, MessageRole

    token = register_and_verify_user(client, db_session, email="baditems@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bad Items Client"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    m1 = Message(chat_id=chat.id, role=MessageRole.user, content="What is X?")
    m2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="Wrong answer", feedback=MessageFeedback.down)
    db_session.add_all([m1, m2])
    db_session.commit()
    db_session.refresh(m2)

    resp = client.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token}"})
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
    client.post(
        f"/chat/messages/{m2.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down", "ideal_answer": "Correct answer."},
    )
    resp2 = client.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200
    assert resp2.json()["items"][0]["ideal_answer"] == "Correct answer."


def test_list_bad_answers_respects_client_isolation(
    client: TestClient,
    db_session: Session,
) -> None:
    """Messages from other clients are not returned."""
    from backend.models import Chat, Message, MessageFeedback, MessageRole

    token_a = register_and_verify_user(client, db_session, email="badisoa@example.com")
    cl_a = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "Client A"},
    )
    client_id_a = uuid.UUID(cl_a.json()["id"])
    chat_a = Chat(client_id=client_id_a, session_id=uuid.uuid4())
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

    token_b = register_and_verify_user(client, db_session, email="badisob@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"name": "Client B"},
    )

    resp = client.get("/chat/bad-answers", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 0


# --- Debug endpoint tests ---


def test_debug_with_embeddings_vector_mode(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Debug endpoint keeps vector confidence separate from final retrieval mode."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="debugvec@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Vec Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="debug.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="The answer is 42 from vector search",
        vector=None,
        metadata_json={"vector": [0.9] + [0.0] * 1535, "chunk_index": 0},
    )
    db_session.add(emb)
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.9] + [0.0] * 1535)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=10)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "What is the answer?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "42"
    assert data["tokens_used"] == 10
    assert data["debug"]["mode"] == "hybrid"
    assert data["debug"]["confidence_source"] == "vector_similarity"
    assert data["debug"]["best_confidence_score"] > 0.0
    assert len(data["debug"]["chunks"]) >= 1
    chunk = data["debug"]["chunks"][0]
    assert chunk["document_id"] == str(doc.id)
    assert "score" in chunk
    assert chunk["score"] >= 0.3
    assert "preview" in chunk
    assert "42" in chunk["preview"] or "answer" in chunk["preview"].lower()


def test_debug_with_embeddings_keyword_mode(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Debug endpoint: low vector confidence → keyword fallback, mode keyword."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    token = register_and_verify_user(client, db_session, email="debugkw@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug Kw Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="debugkw.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    # Chunk with keyword "secret" - use orthogonal vectors so cosine < 0.3
    chunk_vec = [0.0, 1.0] + [0.0] * 1534
    emb = Embedding(
        document_id=doc.id,
        chunk_text="The secret number is 99. Very secret.",
        vector=None,
        metadata_json={"vector": chunk_vec, "chunk_index": 0},
    )
    decoy = Embedding(
        document_id=doc.id,
        chunk_text="Billing overview and invoice export steps.",
        vector=None,
        metadata_json={"vector": chunk_vec, "chunk_index": 1},
    )
    second_decoy = Embedding(
        document_id=doc.id,
        chunk_text="Password rotation policy for administrators only.",
        vector=None,
        metadata_json={"vector": chunk_vec, "chunk_index": 2},
    )
    db_session.add_all([emb, decoy, second_decoy])
    db_session.commit()

    # Query vector orthogonal to chunk → vector confidence 0, lexical signal drives ranking.
    query_vec = [1.0] + [0.0] * 1535
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=query_vec)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="99"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "secret number"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["debug"]["mode"] == "hybrid"
    assert data["debug"]["confidence_source"] == "vector_similarity"
    assert data["debug"]["best_confidence_score"] == 0.0
    assert len(data["debug"]["chunks"]) >= 1
    chunk = data["debug"]["chunks"][0]
    assert chunk["document_id"] == str(doc.id)
    assert chunk["score"] > 0.0
    assert "secret" in chunk["preview"].lower()


def test_debug_no_embeddings(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Debug endpoint: no embeddings → mode none, chunks empty."""
    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]

    token = register_and_verify_user(client, db_session, email="debugnone@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debug None Client"},
    )
    set_client_openai_key(client, token)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Anything"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "I don't have information about this."
    assert data["tokens_used"] == 0
    assert data["debug"]["mode"] == "none"
    assert data["debug"]["chunks"] == []


def test_debug_does_not_persist_chat(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """Debug runs do NOT create Chat/Message records."""
    from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message

    token = register_and_verify_user(client, db_session, email="debugnopersist@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Persist Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
        filename="debugnp.md",
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

    mock_openai_client.embeddings.create.return_value.data = [
        Mock(embedding=[0.1] * 1536)
    ]
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Reply"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=5)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "Hello"},
    )
    assert response.status_code == 200

    # No Chat/Message should have been created
    chats = db_session.query(Chat).filter(Chat.client_id == client_id).all()
    assert len(chats) == 0
    messages = db_session.query(Message).all()
    assert len(messages) == 0


def test_debug_requires_auth(client: TestClient) -> None:
    """Debug endpoint requires JWT."""
    response = client.post(
        "/chat/debug",
        json={"question": "Hello"},
    )
    assert response.status_code == 401


def test_debug_empty_question(client: TestClient, db_session: Session) -> None:
    """Debug with empty question → 422."""
    token = register_and_verify_user(client, db_session, email="debugempty@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Client"},
    )
    set_client_openai_key(client, token)

    response = client.post(
        "/chat/debug",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": ""},
    )
    assert response.status_code == 422


def test_chat_openai_unavailable_503(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    """OpenAI API error → 503."""
    from backend.models import Document, DocumentStatus, DocumentType, Embedding

    from openai import APIError

    token = register_and_verify_user(client, db_session, email="err@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Err Client"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]
    client_id = uuid.UUID(cl_resp.json()["id"])

    doc = Document(
        client_id=client_id,
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

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"question": "Hello"},
    )
    assert response.status_code == 503
    assert "OpenAI" in response.json()["detail"]


def test_chat_awaiting_email_valid_email_transitions_to_followup(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(client, db_session, email="await-valid@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Await Valid Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        client_id=client_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-await"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        client_id=client_id,
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

    response = client.post(
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
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(client, db_session, email="await-invalid@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Await Invalid Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(client_id=client_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        client_id=client_id,
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

    response = client.post(
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
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(client, db_session, email="follow-no@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow No Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        client_id=client_id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        client_id=client_id,
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

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "no thanks"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is True
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert chat.ended_at is not None


def test_chat_followup_unclear_twice_falls_back_to_yes(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

    token = register_and_verify_user(client, db_session, email="follow-unclear@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Follow Unclear Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        client_id=client_id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        client_id=client_id,
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

    r1 = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "maybe"},
    )
    assert r1.status_code == 200
    assert r1.json()["chat_ended"] is False
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is True
    assert (chat.user_context or {}).get("escalation_followup_clarify") is True

    r2 = client.post(
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
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(client, db_session, email="closed@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Closed Client"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(
        client_id=client_id,
        session_id=uuid.uuid4(),
        user_context={},
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

    response = client.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(chat.session_id), "question": "hello again"},
    )
    assert response.status_code == 200
    assert response.json()["chat_ended"] is True
    assert "Chat already ended" in response.json()["answer"]


def test_manual_escalate_requires_api_key(client: TestClient) -> None:
    response = client.post(
        f"/chat/{uuid.uuid4()}/escalate",
        json={"trigger": "user_request"},
    )
    assert response.status_code == 401


def test_manual_escalate_invalid_api_key(client: TestClient) -> None:
    response = client.post(
        f"/chat/{uuid.uuid4()}/escalate",
        headers={"X-API-Key": "bad-key"},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 401


def test_manual_escalate_without_openai_key_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(client, db_session, email="manual-nokey@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual NoKey"},
    )
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]

    chat = Chat(client_id=client_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    response = client.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 400


def test_manual_escalate_missing_session_returns_404(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="manual-404@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual 404"},
    )
    set_client_openai_key(client, token)
    api_key = cl_resp.json()["api_key"]

    response = client.post(
        f"/chat/{uuid.uuid4()}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 404


def test_manual_escalate_openai_error_returns_503(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat
    from openai import APIError

    token = register_and_verify_user(client, db_session, email="manual-503@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual 503"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]
    chat = Chat(client_id=client_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    def _raise_api_error(*args, **kwargs):
        raise APIError("Service unavailable", request=Mock(), body=None)

    monkeypatch.setattr("backend.chat.routes.perform_manual_escalation", _raise_api_error)

    response = client.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert response.status_code == 503


def test_manual_escalate_success_for_both_triggers(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat

    token = register_and_verify_user(client, db_session, email="manual-success@example.com")
    cl_resp = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual Success"},
    )
    set_client_openai_key(client, token)
    client_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]
    chat = Chat(client_id=client_id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()

    monkeypatch.setattr(
        "backend.chat.routes.perform_manual_escalation",
        lambda *args, **kwargs: ("Escalated.", "ESC-0009"),
    )

    r1 = client.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "user_request"},
    )
    assert r1.status_code == 200
    assert r1.json()["ticket_number"] == "ESC-0009"

    r2 = client.post(
        f"/chat/{chat.session_id}/escalate",
        headers={"X-API-Key": api_key},
        json={"trigger": "answer_rejected"},
    )
    assert r2.status_code == 200
    assert r2.json()["ticket_number"] == "ESC-0009"
