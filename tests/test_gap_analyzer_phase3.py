from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.documents import url_service
from backend.embeddings.service import run_embeddings_background
from backend.gap_analyzer import jobs as gap_jobs
from backend.gap_analyzer.enums import GapDismissReason, GapDocTopicStatus, GapSource
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    GapDismissal,
    GapDocTopic,
    SourceSchedule,
    SourceStatus,
    User,
    UrlSource,
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
    filename: str,
    file_type: DocumentType,
    chunk_text: str,
    vector: list[float],
    metadata_json: dict[str, object] | None = None,
    source_url: str | None = None,
) -> Document:
    document = Document(
        client_id=client_id,
        filename=filename,
        file_type=file_type,
        status=DocumentStatus.ready,
        parsed_text=chunk_text,
        source_url=source_url,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        Embedding(
            document_id=document.id,
            chunk_text=chunk_text,
            vector=vector,
            metadata_json=metadata_json or {},
        )
    )
    db_session.commit()
    db_session.refresh(document)
    return document


def test_run_mode_a_excludes_swagger_docs_from_coverage_corpus(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-swagger@example.com",
        name="Gap Mode A Swagger Client",
    )
    _add_document_with_embedding(
        db_session,
        client_id=client_id,
        filename="openapi.json",
        file_type=DocumentType.swagger,
        chunk_text="Swagger rate limits operation details",
        vector=_vector(1.0, 0.0, 0.0),
        metadata_json={"section_title": "Rate limits"},
    )
    _add_document_with_embedding(
        db_session,
        client_id=client_id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        chunk_text="Billing invoice export guide",
        vector=_vector(0.0, 1.0, 0.0),
        metadata_json={"section_title": "Billing"},
    )

    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.extract_mode_a_candidates",
        lambda **_: [
            ModeATopicCandidate(
                topic_label="Rate limits",
                example_questions=["How do I configure API rate limits?"],
            )
        ],
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.embed_texts",
        lambda **kwargs: [_vector(1.0, 0.0, 0.0) for _ in kwargs["texts"]],
    )

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    result = orchestrator.run_mode_a(client_id)

    topics = (
        db_session.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == client_id, GapDocTopic.status == GapDocTopicStatus.active)
        .all()
    )

    assert result.tenant_id == client_id
    assert len(topics) == 1
    assert topics[0].topic_label == "Rate limits"
    assert topics[0].extraction_chunk_hash


def test_run_mode_a_skips_llm_and_row_updates_when_hash_unchanged(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-hash@example.com",
        name="Gap Mode A Hash Client",
    )
    _add_document_with_embedding(
        db_session,
        client_id=client_id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        chunk_text="Invoice export overview",
        vector=_vector(0.0, 1.0, 0.0),
        metadata_json={"section_title": "Billing"},
    )

    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.extract_mode_a_candidates",
        lambda **_: [
            ModeATopicCandidate(
                topic_label="Billing exports",
                example_questions=["How do I export invoices?"],
            )
        ],
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.embed_texts",
        lambda **kwargs: [_vector(1.0, 0.0, 0.0) for _ in kwargs["texts"]],
    )

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_a(client_id)

    first_topic = (
        db_session.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == client_id, GapDocTopic.status == GapDocTopicStatus.active)
        .one()
    )
    first_topic_id = first_topic.id
    first_extracted_at = first_topic.extracted_at
    first_hash = first_topic.extraction_chunk_hash

    def _unexpected_extract(**_: object) -> list[ModeATopicCandidate]:
        raise AssertionError("LLM extraction should not run when extraction hash is unchanged")

    monkeypatch.setattr("backend.gap_analyzer.orchestrator.extract_mode_a_candidates", _unexpected_extract)
    orchestrator.run_mode_a(client_id)

    topics = (
        db_session.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == client_id, GapDocTopic.status == GapDocTopicStatus.active)
        .all()
    )

    assert len(topics) == 1
    assert topics[0].id == first_topic_id
    assert topics[0].extracted_at == first_extracted_at
    assert topics[0].extraction_chunk_hash == first_hash


def test_run_mode_a_filters_candidates_at_or_above_coverage_gate(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-gate@example.com",
        name="Gap Mode A Gate Client",
    )
    _add_document_with_embedding(
        db_session,
        client_id=client_id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        chunk_text="How do I configure API rate limits and throttling",
        vector=_vector(1.0, 0.0, 0.0),
        metadata_json={"section_title": "Rate limits"},
    )

    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.extract_mode_a_candidates",
        lambda **_: [
            ModeATopicCandidate(
                topic_label="Rate limits",
                example_questions=["How do I configure API rate limits?"],
            )
        ],
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.embed_texts",
        lambda **kwargs: [_vector(1.0, 0.0, 0.0) for _ in kwargs["texts"]],
    )

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_a(client_id)

    active_topics = (
        db_session.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == client_id, GapDocTopic.status == GapDocTopicStatus.active)
        .all()
    )
    hash_marker = (
        db_session.query(GapDocTopic)
        .filter(
            GapDocTopic.tenant_id == client_id,
            GapDocTopic.status == GapDocTopicStatus.closed,
            GapDocTopic.topic_label.is_(None),
        )
        .one()
    )

    assert active_topics == []
    assert hash_marker.extraction_chunk_hash


def test_run_mode_a_suppresses_dismissed_topics_across_reindex(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-dismissed@example.com",
        name="Gap Mode A Dismissed Client",
    )
    dismissed_by = (
        db_session.query(User).filter(User.email == "gap-mode-a-dismissed@example.com").one().id
    )
    _add_document_with_embedding(
        db_session,
        client_id=client_id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        chunk_text="Invoice export overview",
        vector=_vector(0.0, 1.0, 0.0),
        metadata_json={"section_title": "Billing"},
    )
    db_session.add(
        GapDismissal(
            tenant_id=client_id,
            source=GapSource.mode_a,
            gap_id=uuid.uuid4(),
            topic_label="Billing exports",
            topic_label_embedding=_vector(1.0, 0.0, 0.0),
            reason=GapDismissReason.other,
            dismissed_by=dismissed_by,
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.extract_mode_a_candidates",
        lambda **_: [
            ModeATopicCandidate(
                topic_label="Billing exports",
                example_questions=["How do I export invoices?"],
            )
        ],
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator.embed_texts",
        lambda **kwargs: [_vector(1.0, 0.0, 0.0) for _ in kwargs["texts"]],
    )

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    orchestrator.run_mode_a(client_id)

    active_topics = (
        db_session.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == client_id, GapDocTopic.status == GapDocTopicStatus.active)
        .all()
    )

    assert active_topics == []


def test_run_embeddings_background_triggers_queue_empty_mode_a_check_after_ready(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-embedding-trigger@example.com",
        name="Gap Mode A Embedding Trigger Client",
    )
    document = Document(
        client_id=client_id,
        filename="guide.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.embedding,
        parsed_text="hello",
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    trigger = Mock()
    knowledge_extract = Mock()
    monkeypatch.setattr("backend.embeddings.service.create_embeddings_for_document", lambda *args, **kwargs: None)
    monkeypatch.setattr("backend.embeddings.service.run_mode_a_for_tenant_when_queue_empty_best_effort", trigger)
    monkeypatch.setattr(
        "backend.tenant_knowledge.extract_tenant_knowledge.run_extract_client_knowledge_for_document",
        knowledge_extract,
    )

    run_embeddings_background(document.id, api_key="sk-test")

    db_session.refresh(document)
    assert document.status == DocumentStatus.ready
    trigger.assert_called_once_with(client_id)


def test_crawl_url_source_triggers_queue_empty_mode_a_check_after_successful_finalize(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-url-trigger@example.com",
        name="Gap Mode A URL Trigger Client",
    )
    source = UrlSource(
        client_id=client_id,
        name="Docs",
        url="https://docs.example.com/start",
        normalized_domain="docs.example.com",
        status=SourceStatus.queued,
        crawl_schedule=SourceSchedule.manual,
        metadata_json={},
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    trigger = Mock()
    monkeypatch.setattr(
        url_service,
        "_plan_crawl",
        lambda *_args, **_kwargs: url_service._CrawlPlan(
            urls=["https://docs.example.com/start"],
            discovered_urls=["https://docs.example.com/start"],
            remaining_capacity=10,
        ),
    )
    monkeypatch.setattr(
        url_service,
        "_index_pages",
        lambda *_args, **_kwargs: url_service._CrawlResult(
            indexed_urls={"https://docs.example.com/start"},
            failures=[],
            chunks_created=3,
        ),
    )
    monkeypatch.setattr("backend.documents.url_service.run_mode_a_for_tenant_when_queue_empty_best_effort", trigger)
    session_factory = sessionmaker(bind=db_session.get_bind(), class_=Session, future=True)
    monkeypatch.setattr(url_service, "SessionLocal", session_factory)

    url_service.crawl_url_source(source.id, api_key="sk-test")

    trigger.assert_called_once_with(client_id)


def test_mode_a_queue_empty_helper_skips_run_when_documents_or_sources_are_pending(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-queue-busy@example.com",
        name="Gap Mode A Queue Busy Client",
    )
    db_session.add_all(
        [
            Document(
                client_id=client_id,
                filename="pending.md",
                file_type=DocumentType.markdown,
                status=DocumentStatus.embedding,
                parsed_text="pending",
            ),
            UrlSource(
                client_id=client_id,
                name="Docs",
                url="https://docs.example.com/start",
                normalized_domain="docs.example.com",
                status=SourceStatus.indexing,
                crawl_schedule=SourceSchedule.manual,
                metadata_json={},
            ),
        ]
    )
    db_session.commit()

    trigger = Mock()
    session_factory = sessionmaker(bind=db_session.get_bind(), class_=Session, future=True)
    monkeypatch.setattr(gap_jobs.core_db, "SessionLocal", session_factory)
    monkeypatch.setattr(gap_jobs, "run_mode_a_for_tenant_best_effort", trigger)

    gap_jobs.run_mode_a_for_tenant_when_queue_empty_best_effort(client_id)

    trigger.assert_not_called()


def test_mode_a_queue_empty_helper_runs_once_when_tenant_queue_is_empty(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-mode-a-queue-empty@example.com",
        name="Gap Mode A Queue Empty Client",
    )

    trigger = Mock()
    session_factory = sessionmaker(bind=db_session.get_bind(), class_=Session, future=True)
    monkeypatch.setattr(gap_jobs.core_db, "SessionLocal", session_factory)
    monkeypatch.setattr(gap_jobs, "run_mode_a_for_tenant_best_effort", trigger)

    gap_jobs.run_mode_a_for_tenant_when_queue_empty_best_effort(client_id)

    trigger.assert_called_once_with(client_id)
