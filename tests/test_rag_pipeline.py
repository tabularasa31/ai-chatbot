from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import (
    RetrievalContext,
    process_chat_message,
)
from backend.faq.faq_matcher import FAQMatchResult, FAQRow
from backend.models import Client, Document, DocumentStatus, DocumentType, Embedding
from backend.search.service import build_reliability_assessment

from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client(
    http: TestClient,
    db: Session,
    *,
    email: str,
) -> tuple[Client, str]:
    token = register_and_verify_user(http, db, email=email)
    cl_resp = http.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "RAG Test Client"},
    )
    assert cl_resp.status_code in (200, 201), cl_resp.text
    set_client_openai_key(http, token)
    api_key = cl_resp.json()["api_key"]
    client_row = db.get(Client, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None
    return client_row, api_key


def _insert_single_chunk(
    db: Session,
    *,
    client_id: uuid.UUID,
    chunk_text: str = "Reset password help",
    vector: list[float] | None = None,
) -> None:
    doc = Document(
        client_id=client_id,
        filename="rag.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    if vector is None:
        vector = [0.1] * 1536

    emb = Embedding(
        document_id=doc.id,
        chunk_text=chunk_text,
        vector=None,
        metadata_json={"vector": vector, "chunk_index": 0},
    )
    db.add(emb)
    db.commit()


class _FakeSpan:
    def __init__(self) -> None:
        self.end_calls: list[dict[str, object]] = []

    def end(self, **kwargs: object) -> None:
        self.end_calls.append(kwargs)


class _FakeGeneration:
    def __init__(self) -> None:
        self.end_calls: list[dict[str, object]] = []

    def end(self, **kwargs: object) -> None:
        self.end_calls.append(kwargs)


class _FakeTrace:
    def __init__(self) -> None:
        self.spans: dict[str, list[_FakeSpan]] = {}
        self.generation_calls: list[dict[str, object]] = []

    def span(self, *, name: str, input: object | None = None, metadata: dict[str, object] | None = None):  # type: ignore[override]
        span = _FakeSpan()
        self.spans.setdefault(name, []).append(span)
        return span

    def generation(self, **kwargs: object) -> _FakeGeneration:
        self.generation_calls.append(kwargs)
        return _FakeGeneration()

    def update(self, **kwargs: object) -> None:
        return None

    def promote(self, **kwargs: object) -> None:
        return None


def test_embedding_once(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(client, db_session, email="embed-once@example.com")
    _insert_single_chunk(db_session, client_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_precheck",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", modules=["ModA"])),
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *_, **__: (False, None),
    )
    monkeypatch.setattr(
        "backend.chat.service.match_faq",
        lambda **_: FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        ),
    )

    _ = process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )

    assert mock_openai_client.embeddings.create.call_count == 1


def test_faq_context_in_prompt(
    mock_openai_client: Mock,
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.chat import service as chat_service

    monkeypatch.setattr(chat_service.settings, "observability_capture_full_prompts", True)

    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(client, db_session, email="faq-prompt@example.com")
    _insert_single_chunk(db_session, client_id=cl_row.id, chunk_text="Some docs chunk.")

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_precheck",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", modules=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    # Keep retrieval deterministic and fast.
    monkeypatch.setattr(
        "backend.chat.service.retrieve_context",
        lambda *_, **__: RetrievalContext(
            chunk_texts=["Retrieved chunk"],
            document_ids=[uuid.uuid4()],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=2),
            vector_similarities=None,
        ),
    )

    faq_item = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.9,
    )
    monkeypatch.setattr(
        "backend.chat.service.match_faq",
        lambda **_: FAQMatchResult(
            strategy="faq_context",
            faq_items=[faq_item],
            top_score=0.9,
            selected_score=0.9,
            selected_faq_id=str(faq_item.id),
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        ),
    )

    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *_, **__: {"is_valid": True, "confidence": 1.0, "reason": "grounded"},
    )

    process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )

    assert fake_trace.generation_calls, "generation should have happened"
    generation_kwargs = fake_trace.generation_calls[-1]
    messages = generation_kwargs["input"]
    assert isinstance(messages, list)
    system_prompt = messages[0]["content"]
    assert "VERIFIED FAQ CANDIDATES" in system_prompt
    assert "Q: How to reset password?" in system_prompt
    assert "A: Use the reset link." in system_prompt


def test_langfuse_faq_match_span(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(client, db_session, email="faq-span@example.com")
    _insert_single_chunk(db_session, client_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_precheck",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", modules=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    monkeypatch.setattr(
        "backend.chat.service.retrieve_context",
        lambda *_, **__: RetrievalContext(
            chunk_texts=["Retrieved chunk"],
            document_ids=[uuid.uuid4()],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=2),
            vector_similarities=None,
        ),
    )

    faq_item = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.81,
    )
    monkeypatch.setattr(
        "backend.chat.service.match_faq",
        lambda **_: FAQMatchResult(
            strategy="faq_context",
            faq_items=[faq_item],
            top_score=0.81,
            selected_score=0.81,
            selected_faq_id=str(faq_item.id),
            direct_guard_used=True,
            direct_guard_passed=False,
            decision_reason="test_reason",
        ),
    )

    monkeypatch.setattr(
        "backend.chat.service.generate_answer",
        lambda *_, **__: ("Answer", 1),
    )
    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *_, **__: {"is_valid": True, "confidence": 1.0, "reason": "grounded"},
    )

    process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )

    spans = fake_trace.spans.get("faq_match")
    assert spans and spans[-1].end_calls, "faq_match span must be logged"
    metadata = spans[-1].end_calls[-1].get("metadata")
    assert isinstance(metadata, dict)
    assert metadata["strategy"] == "faq_context"
    assert metadata["tenant_id"] == str(cl_row.id)
    assert metadata["top_score"] == 0.81
    assert metadata["selected_score"] == 0.81
    assert metadata["faq_ids"] == [str(faq_item.id)]
    assert metadata["selected_faq_id"] == str(faq_item.id)
    assert metadata["direct_guard_used"] is True
    assert metadata["direct_guard_passed"] is False
    assert metadata["decision_reason"] == "test_reason"
    assert metadata["retrieval_skipped"] is False
    assert metadata["generation_skipped"] is False


def test_upstream_query_embedding_span_present_with_precomputed_path(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(client, db_session, email="embed-span@example.com")
    _insert_single_chunk(db_session, client_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_precheck",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", modules=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    monkeypatch.setattr(
        "backend.chat.service.match_faq",
        lambda **_: FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.retrieve_context",
        lambda *_, **__: RetrievalContext(
            chunk_texts=["Retrieved chunk"],
            document_ids=[uuid.uuid4()],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=2),
            vector_similarities=None,
        ),
    )
    monkeypatch.setattr("backend.chat.service.generate_answer", lambda *_, **__: ("Answer", 1))
    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *_, **__: {"is_valid": True, "confidence": 1.0, "reason": "grounded"},
    )

    process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )

    embedding_spans = fake_trace.spans.get("query-embedding")
    assert embedding_spans and embedding_spans[-1].end_calls
    payload = embedding_spans[-1].end_calls[-1].get("output")
    assert isinstance(payload, dict)
    assert payload["embedding_api_request_count"] == 1
    assert payload["upstream_precomputed"] is True


def test_faq_direct_skips_retrieval_and_generation(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(client, db_session, email="faq-direct@example.com")

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_precheck",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", modules=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    faq_answer = "Direct FAQ answer"
    faq_row = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer=faq_answer,
        approved=True,
        score=0.99,
    )
    monkeypatch.setattr(
        "backend.chat.service.match_faq",
        lambda **_: FAQMatchResult(
            strategy="faq_direct",
            faq_items=[faq_row],
            top_score=0.99,
            selected_score=0.99,
            selected_faq_id=str(faq_row.id),
            direct_guard_used=True,
            direct_guard_passed=True,
            decision_reason="test",
        ),
    )

    def _unexpected_retrieve(*_: object, **__: object):
        raise AssertionError("retrieve_context must not be called")

    def _unexpected_generate(*_: object, **__: object):
        raise AssertionError("generate_answer must not be called")

    monkeypatch.setattr("backend.chat.service.retrieve_context", _unexpected_retrieve)
    monkeypatch.setattr("backend.chat.service.generate_answer", _unexpected_generate)
    monkeypatch.setattr("backend.chat.service.validate_answer", _unexpected_generate)

    answer, docs, tokens_used, chat_ended = process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )
    assert chat_ended is False
    assert docs == []
    assert tokens_used == 0
    assert answer == faq_answer


def test_guard_error_degrades_to_context(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(client, db_session, email="guard-error@example.com")
    _insert_single_chunk(db_session, client_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_precheck",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", modules=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.99,
    )

    monkeypatch.setattr(
        "backend.faq.faq_matcher._fetch_top_faq_rows",
        lambda **_: [top],
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: (_ for _ in ()).throw(RuntimeError("guard boom")),
    )

    # Ensure retrieval and generation run (rag_only/direct not allowed).
    called = {"retrieval": False, "generation": False}
    monkeypatch.setattr(
        "backend.chat.service.retrieve_context",
        lambda *_, **__: (
            called.__setitem__("retrieval", True)
            or RetrievalContext(
                chunk_texts=["Retrieved chunk"],
                document_ids=[uuid.uuid4()],
                scores=[0.9],
                mode="hybrid",
                best_rank_score=0.9,
                best_confidence_score=0.9,
                confidence_source="vector_similarity",
                reliability=build_reliability_assessment(top_score=0.9, result_count=2),
                vector_similarities=None,
            )
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.generate_answer",
        lambda *_, **__: (called.__setitem__("generation", True) or "Answer", 1),
    )
    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *_, **__: {"is_valid": True, "confidence": 1.0, "reason": "grounded"},
    )

    process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )

    assert called["retrieval"] is True
    assert called["generation"] is True

    spans = fake_trace.spans.get("faq_match")
    assert spans and spans[-1].end_calls, "faq_match span must be logged"
    metadata = spans[-1].end_calls[-1].get("metadata")
    assert isinstance(metadata, dict)
    assert metadata["strategy"] == "faq_context"
    assert metadata["direct_guard_used"] is True
    assert metadata["direct_guard_passed"] is False


def test_faq_context_without_retrieval_chunks_still_generates_with_faq_hints(
    mock_openai_client: Mock,
) -> None:
    from backend.chat.service import generate_answer

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Answer from FAQ hint"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=12)

    faq_item = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link from login page.",
        approved=True,
        score=0.88,
    )

    answer, tokens = generate_answer(
        "How can I reset password?",
        context_chunks=[],
        api_key="sk-test",
        faq_context_items=[faq_item],
    )

    assert answer == "Answer from FAQ hint"
    assert tokens == 12

    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["messages"][0]["role"] == "system"
    system_prompt = call_kwargs["messages"][0]["content"]
    assert "VERIFIED FAQ CANDIDATES" in system_prompt
    assert "Q: How to reset password?" in system_prompt
    assert "A: Use the reset link from login page." in system_prompt

