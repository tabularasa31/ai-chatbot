"""Mode A corpus and topic query operations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer._repo.capabilities import (
    _enum_value,
    _example_questions_value,
    _repository_capabilities,
    _string_or_none,
)
from backend.gap_analyzer._repo.records import ModeACorpusChunk, ModeADismissalRecord
from backend.gap_analyzer.enums import GapDocTopicStatus, GapSource
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.models import Client, Document, Embedding, GapDismissal, GapDocTopic


def _mode_a_embedding_rows(
    db: Session,
    *,
    tenant_id: UUID,
    excluded_file_types: tuple[str, ...],
) -> list[tuple[Embedding, Document]]:
    rows_query = (
        db.query(Embedding, Document)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == tenant_id)
        .filter(Document.status == "ready")
        .filter(Embedding.chunk_text.isnot(None))
        .order_by(Document.id.asc(), Embedding.id.asc())
    )
    if excluded_file_types:
        rows_query = rows_query.filter(~Document.file_type.in_(excluded_file_types))
    rows = rows_query.all()
    excluded = {value.casefold() for value in excluded_file_types}
    return [
        (embedding, document)
        for embedding, document in rows
        if str(getattr(document.file_type, "value", document.file_type)).casefold() not in excluded
    ]


class _ModeAQueriesOps:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_client_openai_key(self, tenant_id: UUID) -> str | None:
        client = self._db.get(Client, tenant_id)
        return client.openai_api_key if client is not None else None

    def get_latest_mode_a_hash(self, tenant_id: UUID) -> str | None:
        row = (
            self._db.query(GapDocTopic.extraction_chunk_hash)
            .filter(GapDocTopic.tenant_id == tenant_id)
            .filter(GapDocTopic.extraction_chunk_hash.isnot(None))
            .order_by(GapDocTopic.extracted_at.desc(), GapDocTopic.id.desc())
            .first()
        )
        return row[0] if row is not None else None

    def get_mode_a_corpus_chunks(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> list[ModeACorpusChunk]:
        rows = (
            self._db.query(Embedding, Document)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.client_id == tenant_id)
            .filter(Document.status == "ready")
            .filter(Embedding.chunk_text.isnot(None))
            .order_by(Document.id.asc(), Embedding.id.asc())
            .all()
        )
        chunks: list[ModeACorpusChunk] = []
        excluded = {value.casefold() for value in excluded_file_types}
        for embedding, document in rows:
            file_type = document.file_type.value
            if file_type.casefold() in excluded:
                continue
            metadata = embedding.metadata_json if isinstance(embedding.metadata_json, dict) else {}
            chunks.append(
                ModeACorpusChunk(
                    chunk_id=embedding.id,
                    document_id=document.id,
                    chunk_text=embedding.chunk_text or "",
                    vector=embedding.vector,
                    filename=document.filename,
                    source_url=document.source_url,
                    file_type=file_type,
                    section_title=_string_or_none(metadata.get("section_title")),
                    page_title=_string_or_none(metadata.get("page_title")),
                )
            )
        return chunks

    def list_mode_a_dismissals(self, tenant_id: UUID) -> list[ModeADismissalRecord]:
        rows = (
            self._db.query(GapDismissal)
            .filter(GapDismissal.tenant_id == tenant_id)
            .filter(GapDismissal.source == GapSource.mode_a)
            .filter(GapDismissal.topic_label.isnot(None))
            .all()
        )
        return [
            ModeADismissalRecord(
                topic_label=row.topic_label or "",
                topic_label_embedding=row.topic_label_embedding,
            )
            for row in rows
            if row.topic_label
        ]

    def replace_mode_a_topics(
        self,
        *,
        tenant_id: UUID,
        candidates: list[ModeATopicCandidate],
        coverage_scores: dict[str, float],
        topic_embeddings: dict[str, list[float]],
        extraction_chunk_hash: str,
    ) -> None:
        extracted_at = datetime.now(UTC)
        capabilities = _repository_capabilities(self._db)
        self._db.query(GapDocTopic).filter(GapDocTopic.tenant_id == tenant_id).delete()
        if not candidates:
            self._db.add(
                GapDocTopic(
                    tenant_id=tenant_id,
                    topic_label=None,
                    coverage_score=None,
                    status=_enum_value(GapDocTopicStatus.closed, capabilities=capabilities),
                    example_questions=None,
                    extraction_chunk_hash=extraction_chunk_hash,
                    is_new=False,
                    extracted_at=extracted_at,
                )
            )
            self._db.flush()
            return

        for candidate in candidates:
            example_questions: object = _example_questions_value(
                candidate.example_questions,
                capabilities=capabilities,
            )
            self._db.add(
                GapDocTopic(
                    tenant_id=tenant_id,
                    topic_label=candidate.topic_label,
                    topic_embedding=topic_embeddings.get(candidate.topic_label),
                    coverage_score=coverage_scores.get(candidate.topic_label),
                    status=_enum_value(GapDocTopicStatus.active, capabilities=capabilities),
                    example_questions=example_questions,
                    extraction_chunk_hash=extraction_chunk_hash,
                    is_new=True,
                    extracted_at=extracted_at,
                )
            )
        self._db.flush()
