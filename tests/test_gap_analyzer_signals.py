from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import ChatPipelineResult, RetrievalContext, process_chat_message
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import (
    Chat,
    GapQuestion,
    GapQuestionMessageLink,
    Message,
    MessageFeedback,
    MessageRole,
)
from backend.search.service import build_reliability_assessment
from tests.conftest import register_and_verify_user, set_client_openai_key


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


def test_gap_signal_default_timestamp_is_timezone_aware() -> None:
    signal = GapSignal(
        tenant_id=uuid.uuid4(),
        question_text="How does this work?",
        answer_confidence=0.4,
        was_rejected=False,
        had_fallback=False,
        was_escalated=False,
        user_thumbed_down=False,
    )
    assert signal.created_at.tzinfo is not None
    assert signal.created_at.utcoffset() is not None


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


def test_feedback_down_reweights_linked_gap_signal(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-feedback@example.com",
        name="Gap Feedback Tenant",
    )
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    user_message = Message(chat_id=chat.id, role=MessageRole.user, content="How does this work?")
    assistant_first = Message(chat_id=chat.id, role=MessageRole.assistant, content="First answer")
    assistant_second = Message(chat_id=chat.id, role=MessageRole.assistant, content="Second answer")
    db_session.add_all([user_message, assistant_first, assistant_second])
    db_session.commit()
    db_session.refresh(user_message)
    db_session.refresh(assistant_first)
    db_session.refresh(assistant_second)

    gap_question_first = GapQuestion(
        tenant_id=tenant_id,
        question_text="How does this work?",
        gap_signal_weight=1.0,
    )
    gap_question_second = GapQuestion(
        tenant_id=tenant_id,
        question_text="How does this work?",
        gap_signal_weight=1.0,
    )
    db_session.add_all([gap_question_first, gap_question_second])
    db_session.flush()
    db_session.add_all(
        [
            GapQuestionMessageLink(
                gap_question_id=gap_question_first.id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_first.id,
                chat_id=chat.id,
                session_id=chat.session_id,
                attempt_index=0,
            ),
            GapQuestionMessageLink(
                gap_question_id=gap_question_second.id,
                user_message_id=user_message.id,
                assistant_message_id=assistant_second.id,
                chat_id=chat.id,
                session_id=chat.session_id,
                attempt_index=1,
            ),
        ]
    )
    db_session.commit()

    response = tenant.post(
        f"/chat/messages/{assistant_second.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "down", "ideal_answer": "Better answer"},
    )
    assert response.status_code == 200, response.json()

    db_session.refresh(assistant_second)
    db_session.refresh(gap_question_first)
    db_session.refresh(gap_question_second)

    assert assistant_second.feedback == MessageFeedback.down
    assert gap_question_first.gap_signal_weight == 1.0
    assert gap_question_second.gap_signal_weight == 4.0


def test_feedback_up_restores_base_weight_for_linked_gap_signal(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-feedback-up@example.com",
        name="Gap Feedback Up Tenant",
    )
    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    user_message = Message(chat_id=chat.id, role=MessageRole.user, content="How does this work?")
    assistant_message = Message(
        chat_id=chat.id,
        role=MessageRole.assistant,
        content="First answer",
        feedback=MessageFeedback.down,
    )
    db_session.add_all([user_message, assistant_message])
    db_session.commit()
    db_session.refresh(user_message)
    db_session.refresh(assistant_message)

    gap_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How does this work?",
        gap_signal_weight=4.0,
        answer_confidence=0.4,
    )
    db_session.add(gap_question)
    db_session.flush()
    db_session.add(
        GapQuestionMessageLink(
            gap_question_id=gap_question.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            chat_id=chat.id,
            session_id=chat.session_id,
            attempt_index=0,
        )
    )
    db_session.commit()

    response = tenant.post(
        f"/chat/messages/{assistant_message.id}/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"feedback": "up"},
    )
    assert response.status_code == 200, response.json()

    db_session.refresh(assistant_message)
    db_session.refresh(gap_question)

    assert assistant_message.feedback == MessageFeedback.up
    assert gap_question.gap_signal_weight == 1.5


def test_chat_fallback_persists_gap_signal(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_token, owner_client_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-process@example.com",
        name="Gap Process Tenant",
    )
    set_client_openai_key(tenant, owner_token)

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda *args, **kwargs: Mock(detected=False, level=None, method=None, score=None),
    )
    monkeypatch.setattr(
        "backend.chat.service._trigger_log_analysis_threshold",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "backend.chat.service.run_chat_pipeline",
        lambda *args, **kwargs: ChatPipelineResult(
            raw_answer="I am not sure.",
            final_answer="I am not sure.",
            tokens_used=3,
            strategy="rag_only",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=False,
            validation_applied=True,
            validation_outcome="fallback",
            retrieval=_make_retrieval_context(0.3),
            validation={"is_valid": False, "confidence": 0.3, "reason": "fallback"},
            escalation_recommended=False,
            escalation_trigger=None,
        ),
    )

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        owner_client_id,
        "How does billing work?",
        session_id,
        db_session,
        api_key="sk-test",
    )

    assert outcome.text == "I am not sure."

    gap_question = (
        db_session.query(GapQuestion)
        .filter(GapQuestion.tenant_id == owner_client_id)
        .one()
    )
    message_link = (
        db_session.query(GapQuestionMessageLink)
        .join(GapQuestion, GapQuestion.id == GapQuestionMessageLink.gap_question_id)
        .filter(GapQuestion.tenant_id == owner_client_id)
        .one()
    )

    assert gap_question.question_text == "How does billing work?"
    assert gap_question.had_fallback is True
    assert gap_question.answer_confidence == 0.3
    assert gap_question.gap_signal_weight == 2.0
    assert message_link.assistant_message_id is not None
