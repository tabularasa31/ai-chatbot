from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import _try_ingest_gap_signal
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator, _tokenize
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import (
    Chat,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    GapCluster,
    GapClusterStatus,
    GapQuestion,
    Message,
    MessageRole,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def _vector(*values: float) -> list[float]:
    padded = list(values)[:1536]
    return padded + [0.0] * (1536 - len(padded))


def _create_client_and_token(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(client, db_session, email=email)
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.json()
    client_id = uuid.UUID(response.json()["id"])
    set_client_openai_key(client, token)
    return token, client_id


def _add_document_with_embedding(
    db_session: Session,
    *,
    client_id: uuid.UUID,
    chunk_text: str,
    vector: list[float],
) -> None:
    document = Document(
        client_id=client_id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text=chunk_text,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        Embedding(
            document_id=document.id,
            chunk_text=chunk_text,
            vector=vector,
            metadata_json={"section_title": "Guide"},
        )
    )
    db_session.commit()


def test_run_mode_b_creates_cluster_for_unclustered_question(
    client: TestClient,
    db_session: Session,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-b-create@example.com",
        name="Gap Mode B Create Client",
    )
    gap_question = GapQuestion(
        tenant_id=client_id,
        question_text="How do invoice exports work?",
        embedding=_vector(1.0, 0.0, 0.0),
        gap_signal_weight=2.0,
    )
    db_session.add(gap_question)
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    result = orchestrator.run_mode_b(client_id)

    cluster = db_session.query(GapCluster).filter(GapCluster.tenant_id == client_id).one()
    db_session.refresh(gap_question)

    assert result.tenant_id == client_id
    assert gap_question.cluster_id == cluster.id
    assert cluster.label == "How do invoice exports work?"
    assert cluster.question_count == 1
    assert cluster.aggregate_signal_weight == pytest.approx(2.0)
    assert cluster.status == GapClusterStatus.active


def test_run_mode_b_joins_existing_active_cluster_when_similarity_matches(
    client: TestClient,
    db_session: Session,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-b-join@example.com",
        name="Gap Mode B Join Client",
    )
    cluster = GapCluster(
        tenant_id=client_id,
        label="How do invoice exports work?",
        centroid=_vector(1.0, 0.0, 0.0),
        question_count=1,
        aggregate_signal_weight=1.5,
        coverage_score=0.1,
        status=GapClusterStatus.active,
    )
    question = GapQuestion(
        tenant_id=client_id,
        question_text="How do invoice exports work for teams?",
        embedding=_vector(0.95, 0.05, 0.0),
        gap_signal_weight=2.0,
    )
    db_session.add_all([cluster, question])
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_b(client_id)

    clusters = db_session.query(GapCluster).filter(GapCluster.tenant_id == client_id).all()
    db_session.refresh(cluster)
    db_session.refresh(question)

    assert len(clusters) == 1
    assert question.cluster_id == cluster.id
    assert cluster.question_count == 2
    assert cluster.aggregate_signal_weight == pytest.approx(3.5)


def test_run_mode_b_closes_cluster_when_document_coverage_is_high(
    client: TestClient,
    db_session: Session,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-b-covered@example.com",
        name="Gap Mode B Covered Client",
    )
    _add_document_with_embedding(
        db_session,
        client_id=client_id,
        chunk_text="How do invoice exports work and how do invoice exports work for teams",
        vector=_vector(1.0, 0.0, 0.0),
    )
    gap_question = GapQuestion(
        tenant_id=client_id,
        question_text="How do invoice exports work?",
        embedding=_vector(1.0, 0.0, 0.0),
        gap_signal_weight=1.0,
    )
    db_session.add(gap_question)
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_b(client_id)

    cluster = db_session.query(GapCluster).filter(GapCluster.tenant_id == client_id).one()
    assert cluster.status == GapClusterStatus.closed
    assert cluster.coverage_score is not None
    assert cluster.coverage_score >= 0.70


def test_try_ingest_gap_signal_triggers_mode_b_best_effort(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-b-trigger@example.com",
        name="Gap Mode B Trigger Client",
    )
    chat = Chat(client_id=client_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    user_message = Message(chat_id=chat.id, role=MessageRole.user, content="How does this work?")
    assistant_message = Message(chat_id=chat.id, role=MessageRole.assistant, content="Assistant answer")
    db_session.add_all([user_message, assistant_message])
    db_session.commit()
    db_session.refresh(user_message)
    db_session.refresh(assistant_message)

    trigger_calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        "backend.chat.service.run_mode_b_for_tenant_best_effort",
        lambda tenant_id: trigger_calls.append(tenant_id),
    )

    _try_ingest_gap_signal(
        chat=chat,
        client_id=client_id,
        session_id=chat.session_id,
        user_message=user_message,
        assistant_message=assistant_message,
        question_text="How does this work?",
        answer_confidence=0.4,
        was_rejected=False,
        had_fallback=False,
        was_escalated=False,
        language="en",
    )

    assert trigger_calls == [client_id]


def test_tokenize_preserves_hyphenated_terms() -> None:
    tokens = _tokenize("Rate-limit guidance for invoice-export flows")

    assert "rate-limit" in tokens
    assert "invoice-export" in tokens
