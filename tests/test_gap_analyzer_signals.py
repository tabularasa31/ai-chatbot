from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import RetrievalContext
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import (
    Chat,
    GapQuestion,
    GapQuestionMessageLink,
    Message,
    MessageRole,
)
from backend.search.service import build_reliability_assessment
from tests.conftest import register_and_verify_user


def _create_client_and_token(
    tenant: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(tenant, db_session, email=email)
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.json()
    return token, uuid.UUID(response.json()["id"])


def _make_retrieval_context(score: float) -> RetrievalContext:
    return RetrievalContext(
        chunk_texts=["retrieved docs"],
        document_ids=[uuid.uuid4()],
        scores=[score],
        mode="vector",
        best_rank_score=score,
        best_confidence_score=score,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=score, result_count=1),
        vector_similarities=[score],
    )


def test_gap_signal_default_timestamp_is_naive_utc() -> None:
    """``GapSignal.created_at`` flows into ``GapQuestion.created_at`` —
    a naive ``DateTime`` column. PR #682 switched the default from
    ``datetime.now(UTC)`` (aware) to a naive UTC value because asyncpg
    rejects aware values for ``TIMESTAMP WITHOUT TIME ZONE`` and the
    ``before_flush`` listener would otherwise rewrite the value on every
    insert. Asserting naive here documents the contract.
    """
    signal = GapSignal(
        tenant_id=uuid.uuid4(),
        question_text="How does this work?",
        answer_confidence=0.4,
        was_rejected=False,
        had_fallback=False,
        was_escalated=False,
        user_thumbed_down=False,
    )
    assert signal.created_at.tzinfo is None


@pytest.mark.parametrize(
    ("signal_kwargs", "expected_weight"),
    [
        ({"answer_confidence": 0.4}, 1.5),
        ({"answer_confidence": 0.9, "had_fallback": True}, 2.0),
        ({"answer_confidence": 0.9, "was_escalated": True}, 3.0),
    ],
)
def test_gap_signal_ingestion_persists_weight_and_message_link(
    tenant: TestClient,
    db_session: Session,
    signal_kwargs: dict[str, object],
    expected_weight: float,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email=f"gap-signal-{expected_weight}@example.com",
        name="Gap Signal Tenant",
    )
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    user_message = Message(chat_id=chat.id, role=MessageRole.user, content="How does this work?")
    assistant_message = Message(chat_id=chat.id, role=MessageRole.assistant, content="Assistant answer")
    db_session.add_all([user_message, assistant_message])
    db_session.commit()
    db_session.refresh(user_message)
    db_session.refresh(assistant_message)

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    signal_payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "chat_id": chat.id,
        "session_id": chat.session_id,
        "user_message_id": user_message.id,
        "assistant_message_id": assistant_message.id,
        "question_text": "How does this work?",
        "answer_confidence": 0.9,
        "was_rejected": False,
        "had_fallback": False,
        "was_escalated": False,
        "user_thumbed_down": False,
    }
    signal_payload.update(signal_kwargs)
    orchestrator.ingest_signal(GapSignal(**signal_payload))
    db_session.commit()

    gap_question = db_session.query(GapQuestion).one()
    message_link = db_session.query(GapQuestionMessageLink).one()

    assert gap_question.gap_signal_weight == expected_weight
    assert gap_question.question_text == "How does this work?"
    assert message_link.gap_question_id == gap_question.id
    assert message_link.user_message_id == user_message.id
    assert message_link.assistant_message_id == assistant_message.id
    assert message_link.chat_id == chat.id
    assert message_link.session_id == chat.session_id
