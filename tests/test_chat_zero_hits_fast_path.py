"""Tests for the strict zero-RAG-hits fast path in the chat pipeline.

Covers:

* First zero-hits turn returns a localized "rephrase" prompt instead of
  calling the answer LLM, and sets ``chat.last_reply_was_rephrase_prompt``.
* Consecutive zero-hits turn + LLM relevance verdict "relevant" triggers
  pre-confirm escalation and resets the flag.
* Consecutive zero-hits turn + LLM verdict "not relevant" emits the
  NOT_RELEVANT off-topic reject and resets the flag.
* Any non-zero-hits success resets the flag.
* The previously misleading comment in ``relevance_checker.py`` describing
  an unimplemented off-topic pattern exception is gone.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import (
    RetrievalContext,
    process_chat_message,
)
from backend.faq.faq_matcher import FAQMatchResult
from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Tenant
from backend.search.service import build_reliability_assessment

from tests._async_utils import as_async as _as_async
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client(http: TestClient, db: Session, *, email: str) -> tuple[Tenant, str]:
    token = register_and_verify_user(http, db, email=email)
    cl_resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Zero Hits Tenant"},
    )
    assert cl_resp.status_code in (200, 201), cl_resp.text
    set_client_openai_key(http, token)
    api_key = cl_resp.json()["api_key"]
    client_row = db.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None
    return client_row, api_key


def _insert_chunk(db: Session, *, tenant_id: uuid.UUID) -> None:
    doc = Document(
        tenant_id=tenant_id,
        filename="kb.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="Doc chunk",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db.add(emb)
    db.commit()


def _empty_retrieval() -> RetrievalContext:
    return RetrievalContext(
        chunk_texts=[],
        document_ids=[],
        scores=[],
        mode="none",
        best_rank_score=None,
        best_confidence_score=None,
        confidence_source="none",
        reliability=build_reliability_assessment(top_score=0.0, result_count=0),
        vector_similarities=None,
    )


def _nonempty_retrieval() -> RetrievalContext:
    return RetrievalContext(
        chunk_texts=["A relevant chunk"],
        document_ids=[uuid.uuid4()],
        scores=[0.9],
        mode="hybrid",
        best_rank_score=0.9,
        best_confidence_score=0.9,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.9, result_count=1),
        vector_similarities=None,
    )


def _stub_pre_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    *,
    relevance: tuple[bool, str, object] = (
        True,
        "ok",
        SimpleNamespace(product_name="Product", topics=["Topic"]),
    ),
) -> None:
    """Common monkeypatches: injection clean, FAQ no-match, no escalation, no rewrites."""
    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda _text, *, tenant_id, api_key, trace=None: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.async_detect_injection",
        _as_async(lambda *_a, **_kw: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        )),
    )
    monkeypatch.setattr(
        "backend.chat.service.check_relevance_with_profile",
        lambda **_kw: relevance,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_check_relevance_with_profile",
        _as_async(lambda **_kw: relevance),
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *_a, **_kw: (False, None),
    )
    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_kw: FAQMatchResult(
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

    async def _no_rewrite(*_a, **_kw):
        return None

    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite", _no_rewrite
    )
    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite_for_kb", _no_rewrite
    )


def test_first_zero_hits_emits_soft_reply_and_sets_flag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="zh-first@example.com")
    _insert_chunk(db_session, tenant_id=cl_row.id)

    _stub_pre_retrieval(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _empty_retrieval()),
    )
    # Asserts the answer LLM is never reached on the zero-hits path.
    def _fail_generate(*_a, **_kw):  # pragma: no cover - asserts on hit
        raise AssertionError("answer LLM must not be called on zero hits")

    monkeypatch.setattr("backend.chat.service.generate_answer", _fail_generate)

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id, "Tell me about borscht recipe", session_id, db_session,
        api_key=api_key,
    )

    assert outcome.text  # localized soft-reply, exact wording goes through localization
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.last_reply_was_rephrase_prompt is True


def test_consecutive_zero_hits_relevant_escalates(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="zh-esc@example.com")
    _insert_chunk(db_session, tenant_id=cl_row.id)

    profile_stub = SimpleNamespace(product_name="Product", topics=["Topic"])
    _stub_pre_retrieval(
        monkeypatch,
        relevance=(True, "ok", profile_stub),
    )

    # Distinct stub for the post-retrieval consecutive-failure relevance call:
    # this is the one that decides escalation. Profile is non-empty so the
    # guard's no_profile fast-path is skipped.
    consecutive_calls: list[dict] = []

    async def _post_retrieval_relevance(**kwargs):
        consecutive_calls.append(kwargs)
        return (True, "in_domain", profile_stub)

    monkeypatch.setattr(
        "backend.chat.service.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _empty_retrieval()),
    )

    # Seed an existing chat with the rephrase-prompt flag already on.
    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    # Pre-confirm rendering hits OpenAI in production; stub it.
    monkeypatch.setattr(
        "backend.chat.service.render_pre_confirm_text",
        lambda **_kw: SimpleNamespace(
            message_to_user="Want me to escalate this to a human?",
            tokens_used=1,
        ),
    )

    outcome = process_chat_message(
        cl_row.id, "Question with no docs", session_id, db_session,
        api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is True
    assert chat.last_reply_was_rephrase_prompt is False
    assert "escalate" in (outcome.text or "").lower()
    # The post-retrieval relevance call must bypass the short-query fast path.
    assert any(call.get("force_llm_check") is True for call in consecutive_calls)


def test_consecutive_zero_hits_not_relevant_emits_offtopic_reject(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="zh-ot@example.com")
    _insert_chunk(db_session, tenant_id=cl_row.id)

    profile_stub = SimpleNamespace(product_name="Product", topics=["Topic"])
    _stub_pre_retrieval(monkeypatch, relevance=(True, "ok", profile_stub))

    async def _post_retrieval_relevance(**_kwargs):
        return (False, "off_topic", profile_stub)

    monkeypatch.setattr(
        "backend.chat.service.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _empty_retrieval()),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    outcome = process_chat_message(
        cl_row.id, "Some unrelated query", session_id, db_session,
        api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is False
    assert chat.last_reply_was_rephrase_prompt is False
    assert outcome.text


def test_successful_turn_resets_rephrase_flag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _create_client(tenant, db_session, email="zh-reset@example.com")
    _insert_chunk(db_session, tenant_id=cl_row.id)

    _stub_pre_retrieval(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _nonempty_retrieval()),
    )
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        _as_async(lambda *_a, **_kw: ("OK answer", 5, 10, 5, False)),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    process_chat_message(
        cl_row.id, "A real question", session_id, db_session, api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.last_reply_was_rephrase_prompt is False


def test_relevance_checker_comment_hygiene() -> None:
    """The misleading 'Exception: queries that match an explicit off-topic
    pattern are still rejected' comment described unimplemented behavior;
    after the language-agnostic redesign it must be gone.
    """
    path = Path("backend/guards/relevance_checker.py")
    src = path.read_text(encoding="utf-8")
    assert "Exception: queries that match an explicit off-topic pattern" not in src
