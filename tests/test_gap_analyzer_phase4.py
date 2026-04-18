from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.service import _start_mode_b_followup, _try_ingest_gap_signal
from backend.gap_analyzer.enums import GapJobKind
from backend.gap_analyzer._math import _tokenize
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.pipelines.link_sync import _sync_mode_links
from backend.gap_analyzer.pipelines.mode_b import (
    _ModeBClusterUpdateRejectedError,
    _prepare_mode_b_clusters,
    _update_mode_b_cluster,
)
from backend.gap_analyzer.repository import (
    ModeBClusterRecord,
    ModeBQuestionRecord,
    SqlAlchemyGapAnalyzerRepository,
)
from backend.models import (
    Chat,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    GapCluster,
    GapClusterStatus,
    GapDocTopic,
    GapDocTopicStatus,
    GapQuestion,
    Message,
    MessageRole,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def _vector(*values: float) -> list[float]:
    padded = list(values)[:1536]
    return padded + [0.0] * (1536 - len(padded))


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
    tenant_id = uuid.UUID(response.json()["id"])
    set_client_openai_key(tenant, token)
    return token, tenant_id


def _add_document_with_embedding(
    db_session: Session,
    *,
    tenant_id: uuid.UUID,
    chunk_text: str,
    vector: list[float],
) -> None:
    document = Document(
        tenant_id=tenant_id,
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
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-create@example.com",
        name="Gap Mode B Create Tenant",
    )
    gap_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work?",
        embedding=_vector(1.0, 0.0, 0.0),
        gap_signal_weight=2.0,
    )
    db_session.add(gap_question)
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    result = orchestrator.run_mode_b(tenant_id)

    cluster = db_session.query(GapCluster).filter(GapCluster.tenant_id == tenant_id).one()
    db_session.refresh(gap_question)

    assert result.tenant_id == tenant_id
    assert gap_question.cluster_id == cluster.id
    assert cluster.label == "How do invoice exports work?"
    assert cluster.question_count == 1
    assert cluster.aggregate_signal_weight == pytest.approx(2.0)
    assert cluster.status == GapClusterStatus.active


def test_run_mode_b_joins_existing_active_cluster_when_similarity_matches(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-join@example.com",
        name="Gap Mode B Join Tenant",
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="How do invoice exports work?",
        centroid=_vector(1.0, 0.0, 0.0),
        question_count=1,
        aggregate_signal_weight=1.5,
        coverage_score=0.1,
        status=GapClusterStatus.active,
    )
    question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work for teams?",
        embedding=_vector(0.95, 0.05, 0.0),
        gap_signal_weight=2.0,
    )
    db_session.add_all([cluster, question])
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_b(tenant_id)

    clusters = db_session.query(GapCluster).filter(GapCluster.tenant_id == tenant_id).all()
    db_session.refresh(cluster)
    db_session.refresh(question)

    assert len(clusters) == 1
    assert question.cluster_id == cluster.id
    assert cluster.question_count == 2
    assert cluster.aggregate_signal_weight == pytest.approx(3.5)


def test_run_mode_b_closes_cluster_when_document_coverage_is_high(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-covered@example.com",
        name="Gap Mode B Covered Tenant",
    )
    _add_document_with_embedding(
        db_session,
        tenant_id=tenant_id,
        chunk_text="How do invoice exports work and how do invoice exports work for teams",
        vector=_vector(1.0, 0.0, 0.0),
    )
    gap_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work?",
        embedding=_vector(1.0, 0.0, 0.0),
        gap_signal_weight=1.0,
    )
    db_session.add(gap_question)
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_b(tenant_id)

    cluster = db_session.query(GapCluster).filter(GapCluster.tenant_id == tenant_id).one()
    assert cluster.status == GapClusterStatus.closed
    assert cluster.coverage_score is not None
    assert cluster.coverage_score >= 0.70


def test_run_mode_b_links_cluster_to_matching_mode_a_topic(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-link@example.com",
        name="Gap Mode B Link Tenant",
    )
    topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Invoice exports",
        topic_embedding=_vector(1.0, 0.0, 0.0),
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work?",
        embedding=_vector(0.98, 0.02, 0.0),
        gap_signal_weight=2.0,
    )
    db_session.add_all([topic, question])
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_b(tenant_id)

    cluster = db_session.query(GapCluster).filter(GapCluster.tenant_id == tenant_id).one()
    db_session.refresh(topic)

    assert cluster.linked_doc_topic_id == topic.id
    assert topic.linked_cluster_id == cluster.id


def test_sync_mode_links_clears_stale_links_for_inactive_clusters(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-inactive-link@example.com",
        name="Gap Mode B Inactive Link Tenant",
    )
    stale_topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Legacy imports",
        topic_embedding=_vector(0.0, 1.0, 0.0),
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    fresh_topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Invoice exports",
        topic_embedding=_vector(0.98, 0.02, 0.0),
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    inactive_cluster = GapCluster(
        tenant_id=tenant_id,
        label="Legacy imports",
        centroid=_vector(0.0, 1.0, 0.0),
        question_count=2,
        aggregate_signal_weight=1.0,
        coverage_score=0.9,
        status=GapClusterStatus.inactive,
        last_computed_at=datetime.now(timezone.utc),
    )
    active_cluster = GapCluster(
        tenant_id=tenant_id,
        label="Invoice exports",
        centroid=_vector(0.99, 0.01, 0.0),
        question_count=3,
        aggregate_signal_weight=2.0,
        coverage_score=0.2,
        status=GapClusterStatus.active,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([stale_topic, fresh_topic, inactive_cluster, active_cluster])
    db_session.commit()

    stale_topic.linked_cluster_id = inactive_cluster.id
    inactive_cluster.linked_doc_topic_id = stale_topic.id
    db_session.add_all([stale_topic, inactive_cluster])
    db_session.commit()

    _sync_mode_links(db_session, tenant_id=tenant_id)
    db_session.refresh(stale_topic)
    db_session.refresh(fresh_topic)
    db_session.refresh(inactive_cluster)
    db_session.refresh(active_cluster)

    assert stale_topic.linked_cluster_id is None
    assert inactive_cluster.linked_doc_topic_id is None
    assert fresh_topic.linked_cluster_id == active_cluster.id
    assert active_cluster.linked_doc_topic_id == fresh_topic.id


def test_sync_mode_links_clears_stale_links_for_unlabeled_topics(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-a-unlabeled-link@example.com",
        name="Gap Mode A Unlabeled Link Tenant",
    )
    unlabeled_topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label=None,
        topic_embedding=_vector(1.0, 0.0, 0.0),
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="Legacy unlabeled docs gap",
        centroid=_vector(1.0, 0.0, 0.0),
        question_count=1,
        aggregate_signal_weight=1.0,
        coverage_score=0.3,
        status=GapClusterStatus.active,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([unlabeled_topic, cluster])
    db_session.commit()

    unlabeled_topic.linked_cluster_id = cluster.id
    cluster.linked_doc_topic_id = unlabeled_topic.id
    db_session.add_all([unlabeled_topic, cluster])
    db_session.commit()

    _sync_mode_links(db_session, tenant_id=tenant_id)
    db_session.refresh(unlabeled_topic)
    db_session.refresh(cluster)

    assert unlabeled_topic.linked_cluster_id is None
    assert cluster.linked_doc_topic_id is None


def test_try_ingest_gap_signal_triggers_mode_b_best_effort(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-trigger@example.com",
        name="Gap Mode B Trigger Tenant",
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

    trigger_calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        "backend.chat.service._start_mode_b_followup",
        lambda tenant_id: trigger_calls.append(tenant_id),
    )

    _try_ingest_gap_signal(
        chat=chat,
        tenant_id=tenant_id,
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

    assert trigger_calls == [tenant_id]


def test_run_mode_b_joins_existing_closed_cluster_when_similarity_matches(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-closed-join@example.com",
        name="Gap Mode B Closed Join Tenant",
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="How do invoice exports work?",
        centroid=_vector(1.0, 0.0, 0.0),
        question_count=1,
        aggregate_signal_weight=1.5,
        coverage_score=0.9,
        status=GapClusterStatus.closed,
    )
    question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work for teams?",
        embedding=_vector(0.95, 0.05, 0.0),
        gap_signal_weight=2.0,
    )
    db_session.add_all([cluster, question])
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_b(tenant_id)

    clusters = db_session.query(GapCluster).filter(GapCluster.tenant_id == tenant_id).all()
    db_session.refresh(cluster)
    db_session.refresh(question)

    assert len(clusters) == 1
    assert question.cluster_id == cluster.id
    assert cluster.question_count == 2


def test_tokenize_preserves_hyphenated_terms() -> None:
    tokens = _tokenize("Rate-limit guidance for invoice-export flows")

    assert "rate-limit" in tokens
    assert "invoice-export" in tokens


def test_prepare_mode_b_clusters_skips_unknown_status() -> None:
    prepared = _prepare_mode_b_clusters(
        [
            ModeBClusterRecord(
                cluster_id=uuid.uuid4(),
                label="Broken cluster",
                centroid=_vector(1.0, 0.0, 0.0),
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.1,
                status="broken",
                last_question_at=None,
            )
        ]
    )

    assert prepared == []


def test_update_mode_b_cluster_refuses_mismatched_vector_lengths() -> None:
    cluster = _prepare_mode_b_clusters(
        [
            ModeBClusterRecord(
                cluster_id=uuid.uuid4(),
                label="Invoice exports",
                centroid=_vector(1.0, 0.0, 0.0),
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.1,
                status=GapClusterStatus.active.value,
                last_question_at=None,
            )
        ]
    )[0]
    question = ModeBQuestionRecord(
        question_id=uuid.uuid4(),
        question_text="Invoice exports for teams?",
        embedding=[1.0, 0.0],
        gap_signal_weight=2.0,
        language="en",
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(_ModeBClusterUpdateRejectedError):
        _update_mode_b_cluster(
            cluster=cluster,
            question=question,
            question_embedding=[1.0, 0.0],
        )
    assert cluster.question_count == 1


def test_mutable_mode_b_cluster_post_init_recomputes_centroid_norm() -> None:
    cluster = _prepare_mode_b_clusters(
        [
            ModeBClusterRecord(
                cluster_id=uuid.uuid4(),
                label="Invoice exports",
                centroid=_vector(1.0, 0.0, 0.0),
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.1,
                status=GapClusterStatus.active.value,
                last_question_at=None,
            )
        ]
    )[0]

    cluster.centroid_norm = 0.0
    cluster.__post_init__()

    assert cluster.centroid_norm > 0.0


def test_ensure_mode_b_question_embeddings_skips_blank_questions_without_misalignment(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-mode-b-blank-embeddings@example.com",
        name="Gap Mode B Blank Embeddings Tenant",
    )
    blank_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="   ",
        embedding=None,
        gap_signal_weight=1.0,
    )
    valid_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work?",
        embedding=None,
        gap_signal_weight=1.0,
    )
    db_session.add_all([blank_question, valid_question])
    db_session.commit()

    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.embed_texts",
        lambda *, encrypted_api_key, texts: [[0.9] * 1536] if texts == [valid_question.question_text] else [],
    )

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator._ensure_mode_b_question_embeddings(
        encrypted_api_key="encrypted-key",
        questions=SqlAlchemyGapAnalyzerRepository(db_session).list_unclustered_mode_b_questions(tenant_id),
    )

    db_session.refresh(blank_question)
    db_session.refresh(valid_question)

    assert blank_question.embedding is None
    assert valid_question.embedding is not None


def test_start_mode_b_followup_enqueues_durable_mode_b_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid.uuid4()
    calls: list[tuple[uuid.UUID, GapJobKind, str]] = []
    monkeypatch.setattr(
        "backend.chat.service.enqueue_gap_job_for_tenant_best_effort",
        lambda queued_tenant_id, *, job_kind, trigger: calls.append((queued_tenant_id, job_kind, trigger)),
    )

    _start_mode_b_followup(tenant_id)

    assert calls == [(tenant_id, GapJobKind.mode_b, "chat_signal")]
