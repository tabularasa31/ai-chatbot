from __future__ import annotations

import uuid
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
from backend.models import Tenant, Document, DocumentStatus, DocumentType, Embedding
from backend.search.service import build_reliability_assessment

from tests._async_utils import as_async as _as_async
from tests.conftest import register_and_verify_user, set_client_openai_key



def _create_client(
    http: TestClient,
    db: Session,
    *,
    email: str,
) -> tuple[Tenant, str]:
    token = register_and_verify_user(http, db, email=email)
    cl_resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "RAG Test Tenant"},
    )
    assert cl_resp.status_code in (200, 201), cl_resp.text
    set_client_openai_key(http, token)
    api_key = cl_resp.json()["api_key"]
    client_row = db.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None
    return client_row, api_key


def _insert_single_chunk(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    chunk_text: str = "Reset password help",
    vector: list[float] | None = None,
) -> None:
    doc = Document(
        tenant_id=tenant_id,
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
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="embed-once@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", topics=["ModA"])),
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *_, **__: (False, None),
    )
    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_: FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        )),
    )
    monkeypatch.setattr(
        "backend.chat.service._start_mode_b_followup",
        lambda _tenant_id: None,
    )
    # Suppress LLM-driven query rewrites so the test isolates the embedding
    # call count: base variants are embedded in parallel with the relevance
    # guard (one call), and retrieve_context must not embed again.
    async def _no_rewrite(*_, **__):
        return None

    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite",
        _no_rewrite,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite_for_kb",
        _no_rewrite,
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
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.chat import service as chat_service

    monkeypatch.setattr(chat_service.settings, "observability_capture_full_prompts", True)

    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(tenant, db_session, email="faq-prompt@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id, chunk_text="Some docs chunk.")

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", topics=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    # Keep retrieval deterministic and fast.
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_, **__: RetrievalContext(
            chunk_texts=["Retrieved chunk"],
            document_ids=[uuid.uuid4()],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=2),
            vector_similarities=None,
        )),
    )

    faq_item = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.9,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_: FAQMatchResult(
            strategy="faq_context",
            faq_items=[faq_item],
            top_score=0.9,
            selected_score=0.9,
            selected_faq_id=str(faq_item.id),
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        )),
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
    user_message = messages[1]["content"]
    assert "VERIFIED FAQ CANDIDATES" not in system_prompt
    assert "VERIFIED FAQ CANDIDATES" in user_message
    assert "Q: How to reset password?" in user_message
    assert "A: Use the reset link." in user_message


def test_langfuse_faq_match_span(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(tenant, db_session, email="faq-span@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", topics=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_, **__: RetrievalContext(
            chunk_texts=["Retrieved chunk"],
            document_ids=[uuid.uuid4()],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=2),
            vector_similarities=None,
        )),
    )

    faq_item = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.81,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_: FAQMatchResult(
            strategy="faq_context",
            faq_items=[faq_item],
            top_score=0.81,
            selected_score=0.81,
            selected_faq_id=str(faq_item.id),
            direct_guard_used=True,
            direct_guard_passed=False,
            decision_reason="test_reason",
        )),
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
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(tenant, db_session, email="embed-span@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", topics=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_: FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        )),
    )
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_, **__: RetrievalContext(
            chunk_texts=["Retrieved chunk"],
            document_ids=[uuid.uuid4()],
            scores=[0.9],
            mode="hybrid",
            best_rank_score=0.9,
            best_confidence_score=0.9,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.9, result_count=2),
            vector_similarities=None,
        )),
    )
    monkeypatch.setattr("backend.chat.service.generate_answer", lambda *_, **__: ("Answer", 1))
    monkeypatch.setattr(
        "backend.chat.service.validate_answer",
        lambda *_, **__: {"is_valid": True, "confidence": 1.0, "reason": "grounded"},
    )
    # Suppress LLM-driven query rewrites so only the base embedding batch runs.
    async def _no_rewrite(*_, **__):
        return None

    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite",
        _no_rewrite,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite_for_kb",
        _no_rewrite,
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
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="faq-direct@example.com")

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", topics=["ModA"])),
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
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_: FAQMatchResult(
            strategy="faq_direct",
            faq_items=[faq_row],
            top_score=0.99,
            selected_score=0.99,
            selected_faq_id=str(faq_row.id),
            direct_guard_used=True,
            direct_guard_passed=True,
            decision_reason="test",
        )),
    )

    def _unexpected_retrieve(*_: object, **__: object):
        raise AssertionError("retrieve_context must not be called")

    def _unexpected_generate(*_: object, **__: object):
        raise AssertionError("generate_answer must not be called")

    monkeypatch.setattr("backend.chat.service.async_retrieve_context", _as_async(_unexpected_retrieve))
    monkeypatch.setattr("backend.chat.service.generate_answer", _unexpected_generate)
    monkeypatch.setattr("backend.chat.service.validate_answer", _unexpected_generate)

    outcome = process_chat_message(
        cl_row.id,
        "Reset password",
        uuid.uuid4(),
        db_session,
        api_key=api_key,
    )
    assert outcome.chat_ended is False
    assert outcome.document_ids == []
    assert outcome.tokens_used == 0
    assert outcome.text == faq_answer


def test_guard_error_degrades_to_context(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_trace = _FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **_: fake_trace)

    cl_row, api_key = _create_client(tenant, db_session, email="guard-error@example.com")
    _insert_single_chunk(db_session, tenant_id=cl_row.id)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_: (True, "ok", SimpleNamespace(product_name="Product", topics=["ModA"])),
    )
    monkeypatch.setattr("backend.chat.service.should_escalate", lambda *_, **__: (False, None))

    top = FAQRow(
        id=uuid.uuid4(),
        question="How to reset password?",
        answer="Use the reset link.",
        approved=True,
        score=0.99,
    )

    async def _async_fetch_returns_top(**_):
        return [top]

    monkeypatch.setattr(
        "backend.faq.faq_matcher._async_fetch_top_faq_rows",
        _async_fetch_returns_top,
    )
    monkeypatch.setattr(
        "backend.faq.faq_matcher.direct_applicability_guard",
        lambda **_: (_ for _ in ()).throw(RuntimeError("guard boom")),
    )

    # Ensure retrieval and generation run (rag_only/direct not allowed).
    called = {"retrieval": False, "generation": False}
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_, **__: (
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
        )),
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
    assert call_kwargs["messages"][1]["role"] == "user"
    system_prompt = call_kwargs["messages"][0]["content"]
    user_message = call_kwargs["messages"][1]["content"]
    assert "VERIFIED FAQ CANDIDATES" not in system_prompt
    assert "VERIFIED FAQ CANDIDATES" in user_message
    assert "Q: How to reset password?" in user_message
    assert "A: Use the reset link from login page." in user_message


# ---------------------------------------------------------------------------
# Unit tests for _parse_validation_json (markdown-fence stripping)
# ---------------------------------------------------------------------------


class TestParseValidationJson:
    """Tests for the internal JSON parser used by validate_answer."""

    def setup_method(self) -> None:
        from backend.chat.handlers.rag import _parse_validation_json

        self._parse = _parse_validation_json

    def _valid_payload(self) -> str:
        return '{"is_valid": true, "confidence": 0.9, "reason": "grounded"}'

    def test_clean_json(self) -> None:
        result = self._parse(self._valid_payload())
        assert result == {"is_valid": True, "confidence": 0.9, "reason": "grounded"}

    def test_json_fenced_with_json_hint(self) -> None:
        raw = f"```json\n{self._valid_payload()}\n```"
        result = self._parse(raw)
        assert result["is_valid"] is True
        assert result["confidence"] == 0.9

    def test_json_fenced_without_hint(self) -> None:
        raw = f"```\n{self._valid_payload()}\n```"
        result = self._parse(raw)
        assert result["is_valid"] is True

    def test_fence_prefix_only(self) -> None:
        raw = f"```json\n{self._valid_payload()}"
        result = self._parse(raw)
        assert result["is_valid"] is True

    def test_fence_suffix_only(self) -> None:
        raw = f"{self._valid_payload()}\n```"
        result = self._parse(raw)
        assert result["is_valid"] is True

    def test_brace_search_fallback(self) -> None:
        raw = f"Here is the result:\n{self._valid_payload()}\nHope that helps."
        result = self._parse(raw)
        assert result["is_valid"] is True

    def test_warns_on_fence_stripping(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        raw = f"```json\n{self._valid_payload()}\n```"
        with caplog.at_level(logging.WARNING, logger="backend.chat.handlers.rag"):
            self._parse(raw)
        assert any("markdown fences" in r.message for r in caplog.records)

    def test_warns_on_brace_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        raw = f"Some text before {self._valid_payload()} some text after"
        with caplog.at_level(logging.WARNING, logger="backend.chat.handlers.rag"):
            self._parse(raw)
        assert any("brace-search" in r.message for r in caplog.records)

    def test_raises_on_unparseable_content(self) -> None:
        import json

        with pytest.raises(json.JSONDecodeError):
            self._parse("not json at all, no braces here")

    def test_clean_json_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="backend.chat.handlers.rag"):
            self._parse(self._valid_payload())
        assert caplog.records == []


class TestStripThoughtTags:
    def setup_method(self) -> None:
        from backend.chat.handlers.rag import _strip_thought_tags

        self._strip = _strip_thought_tags

    def test_closed_tag_stripped(self) -> None:
        text = "<thought>Let me think.</thought> Here is the answer."
        assert self._strip(text) == "Here is the answer."

    def test_truncated_thought_stripped_to_empty(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        text = "<thought>Let me think about this. The user is asking about pricing,"
        with caplog.at_level(logging.WARNING, logger="backend.chat.handlers.rag"):
            result = self._strip(text)
        assert result == ""
        assert any("thought_tag_truncated" in r.message for r in caplog.records)

    def test_truncated_thought_mid_text_preserves_content_before(self) -> None:
        text = "Hello! <thought>internal reasoning that got cut off by max_tokens"
        result = self._strip(text)
        assert result == "Hello!"
        assert "<thought>" not in result
