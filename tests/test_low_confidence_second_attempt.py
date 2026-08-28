"""The weak-retrieval band offers a handoff only on a second consecutive miss.

``low_similarity`` means retrieval found something and scored it below the
handoff floor. Escalating on that first miss throws away the answer the
pipeline just generated — the pre-confirm reply replaces it — and puts a
one-word "yes" in front of a user who never asked for a person. So the first
weak turn keeps its answer and only arms the tracker; the second consecutive
weak turn is evidence the user is stuck, and escalates as before.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import LanguageDetectionResult
from backend.chat.service import (
    ChatPipelineResult,
    RetrievalContext,
    process_chat_message,
)
from backend.models import Chat, EscalationTrigger, Message, MessageRole
from backend.search.service import build_reliability_assessment
from tests.conftest import register_and_verify_user, set_client_openai_key

WEAK_ANSWER = "The docs only mention the limitations list."
PRE_CONFIRM = "PRE_CONFIRM_QUESTION"


class _FakeSpan:
    def end(self, **kwargs: object) -> None:
        return None


class _FakeTrace:
    def span(self, **kwargs: object) -> _FakeSpan:
        return _FakeSpan()

    def update(self, **kwargs: object) -> None:
        return None

    def promote(self, **kwargs: object) -> None:
        return None


def _weak_retrieval() -> RetrievalContext:
    """Chunks came back, but below the 0.45 handoff floor."""
    return RetrievalContext(
        chunk_texts=["tunnels to origin without a public IP are not supported"],
        document_ids=[uuid.uuid4()],
        scores=[0.31],
        mode="hybrid",
        best_rank_score=0.31,
        best_confidence_score=0.31,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.31, result_count=3),
    )


def _setup(tenant: TestClient, db_session: Session, email: str) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Attempt Tenant"},
    ).json()
    set_client_openai_key(tenant, token)
    return uuid.UUID(created["id"]), created["api_key"]


def _patch_weak_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every turn retrieves weakly and the pipeline recommends escalation."""
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: _FakeTrace())
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda text: LanguageDetectionResult("en", 0.99, True),
    )
    monkeypatch.setattr("backend.chat.service._try_ingest_gap_signal", lambda **kwargs: None)
    monkeypatch.setattr(
        "backend.chat.service._trigger_log_analysis_threshold",
        lambda *_a, **_k: None,
    )

    async def _weak_pipeline(*args, **kwargs) -> ChatPipelineResult:
        return ChatPipelineResult(
            raw_answer=WEAK_ANSWER,
            final_answer=WEAK_ANSWER,
            tokens_used=3,
            strategy="rag_only",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=False,
            retrieval=_weak_retrieval(),
            escalation_recommended=True,
            escalation_trigger=EscalationTrigger.low_similarity,
        )

    monkeypatch.setattr("backend.chat.service.async_run_chat_pipeline", _weak_pipeline)

    async def _fake_render_pre_confirm(**kwargs):
        return type(
            "EscalationOut", (), {"message_to_user": PRE_CONFIRM, "tokens_used": 1}
        )()

    monkeypatch.setattr(
        "backend.chat.service.render_pre_confirm_text", _fake_render_pre_confirm
    )


def _assistant_texts(db_session: Session, chat: Chat) -> list[str]:
    return [
        m.content
        for m in db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.assistant)
        .all()
    ]


def test_first_weak_turn_keeps_its_answer_and_offers_nothing(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _setup(tenant, db_session, "weak-first@example.com")
    session_id = uuid.uuid4()
    _patch_weak_turn(monkeypatch)

    outcome = process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )

    # The user reads the answer the pipeline produced, not a handoff question.
    assert outcome.text == WEAK_ANSWER
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None
    # ...and the tracker is armed so a second weak turn escalates.
    assert chat.last_reply_was_low_confidence is True
    assert PRE_CONFIRM not in _assistant_texts(db_session, chat)


def test_second_consecutive_weak_turn_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _setup(tenant, db_session, "weak-second@example.com")
    session_id = uuid.uuid4()
    _patch_weak_turn(monkeypatch)

    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    outcome = process_chat_message(
        tenant_id, "so how do I run one?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.low_similarity.value
    )
    # The handoff prompt replaces the RAG verdict, so it carries no citations.
    assert outcome.document_ids == []


def test_a_good_turn_between_two_weak_ones_resets_the_tracker(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two weak turns separated by an answered one are not 'consecutive'."""
    tenant_id, api_key = _setup(tenant, db_session, "weak-reset@example.com")
    session_id = uuid.uuid4()
    _patch_weak_turn(monkeypatch)

    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )

    async def _confident_pipeline(*args, **kwargs) -> ChatPipelineResult:
        return ChatPipelineResult(
            raw_answer="Yes, here is how.",
            final_answer="Yes, here is how.",
            tokens_used=3,
            strategy="rag_only",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=False,
            retrieval=_weak_retrieval(),
            escalation_recommended=False,
            escalation_trigger=None,
        )

    monkeypatch.setattr(
        "backend.chat.service.async_run_chat_pipeline", _confident_pipeline
    )
    process_chat_message(
        tenant_id, "and how do I deploy?", session_id, db_session, api_key=api_key
    )
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    assert chat.last_reply_was_low_confidence is False

    _patch_weak_turn(monkeypatch)
    outcome = process_chat_message(
        tenant_id, "what about custom domains?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == WEAK_ANSWER
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False
