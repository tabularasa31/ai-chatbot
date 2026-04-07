"""Persistence seams and repository implementation for Gap Analyzer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer.enums import GapClusterStatus, GapDocTopicStatus, GapSource
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.gap_analyzer.schemas import GapRunMode
from backend.models import (
    Client,
    Document,
    Embedding,
    GapCluster,
    GapDismissal,
    GapDocTopic,
    GapQuestion,
    GapQuestionMessageLink,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredGapSignalState:
    gap_question_id: UUID
    answer_confidence: float | None
    had_fallback: bool
    had_rejected: bool
    had_escalation: bool


@dataclass(frozen=True)
class ModeACorpusChunk:
    chunk_id: UUID
    document_id: UUID
    chunk_text: str
    vector: object
    filename: str | None
    source_url: str | None
    file_type: str
    section_title: str | None
    page_title: str | None


@dataclass(frozen=True)
class ModeADismissalRecord:
    topic_label: str
    topic_label_embedding: object


@dataclass(frozen=True)
class ModeBQuestionRecord:
    question_id: UUID
    question_text: str
    embedding: object
    gap_signal_weight: float
    language: str | None
    created_at: datetime


@dataclass(frozen=True)
class ModeBClusterRecord:
    cluster_id: UUID
    label: str | None
    centroid: object
    question_count: int
    aggregate_signal_weight: float
    coverage_score: float | None
    status: str
    last_question_at: datetime | None


@dataclass(frozen=True)
class _RepositoryCapabilities:
    enum_values_as_strings: bool
    supports_array_values: bool


class GapAnalyzerRepository(Protocol):
    """Command-side persistence boundary for Gap Analyzer."""

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
        ...

    def get_signal_state_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
    ) -> StoredGapSignalState | None:
        ...

    def update_signal_weight(
        self,
        *,
        gap_question_id: UUID,
        signal_weight: float,
    ) -> None:
        ...

    def get_client_openai_key(self, tenant_id: UUID) -> str | None:
        ...

    def get_latest_mode_a_hash(self, tenant_id: UUID) -> str | None:
        ...

    def get_mode_a_corpus_chunks(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> list[ModeACorpusChunk]:
        ...

    def list_mode_a_dismissals(self, tenant_id: UUID) -> list[ModeADismissalRecord]:
        ...

    def replace_mode_a_topics(
        self,
        *,
        tenant_id: UUID,
        candidates: list[ModeATopicCandidate],
        coverage_scores: dict[str, float],
        topic_embeddings: dict[str, list[float]],
        extraction_chunk_hash: str,
    ) -> None:
        ...

    def list_unclustered_mode_b_questions(self, tenant_id: UUID) -> list[ModeBQuestionRecord]:
        ...

    def list_mode_b_clusters(self, tenant_id: UUID) -> list[ModeBClusterRecord]:
        ...

    def update_mode_b_question_embedding(
        self,
        *,
        question_id: UUID,
        embedding: list[float],
    ) -> None:
        ...

    def bulk_update_mode_b_question_embeddings(
        self,
        *,
        embeddings_by_question_id: dict[UUID, list[float]],
    ) -> None:
        ...

    def create_mode_b_cluster(
        self,
        *,
        tenant_id: UUID,
        label: str,
        centroid: list[float],
        question_count: int,
        aggregate_signal_weight: float,
        coverage_score: float,
        status: GapClusterStatus,
        last_question_at: datetime,
        last_computed_at: datetime,
    ) -> UUID:
        ...

    def assign_question_to_cluster(
        self,
        *,
        question_id: UUID,
        cluster_id: UUID,
    ) -> None:
        ...

    def update_mode_b_cluster(
        self,
        *,
        cluster_id: UUID,
        centroid: list[float],
        question_count: int,
        aggregate_signal_weight: float,
        coverage_score: float,
        status: GapClusterStatus,
        last_question_at: datetime,
        last_computed_at: datetime,
    ) -> None:
        ...

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> None:
        ...


@dataclass
class SqlAlchemyGapAnalyzerRepository:
    """Command-side persistence implementation for Gap Analyzer."""

    db: Session

    @property
    def _capabilities(self) -> _RepositoryCapabilities:
        return _repository_capabilities(self.db)

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
        if signal.chat_id is None or signal.session_id is None:
            raise ValueError("GapSignal requires chat_id and session_id for Phase 2 ingestion")
        if signal.user_message_id is None or signal.assistant_message_id is None:
            raise ValueError(
                "GapSignal requires user_message_id and assistant_message_id for Phase 2 ingestion"
            )

        gap_question = GapQuestion(
            tenant_id=signal.tenant_id,
            question_text=signal.question_text,
            gap_signal_weight=signal_weight,
            answer_confidence=signal.answer_confidence,
            had_fallback=signal.had_fallback or signal.was_rejected,
            had_escalation=signal.was_escalated,
            language=signal.language,
            created_at=signal.created_at,
        )
        self.db.add(gap_question)
        self.db.flush()

        self.db.add(
            GapQuestionMessageLink(
                gap_question_id=gap_question.id,
                user_message_id=signal.user_message_id,
                assistant_message_id=signal.assistant_message_id,
                chat_id=signal.chat_id,
                session_id=signal.session_id,
                attempt_index=signal.attempt_index,
                created_at=signal.created_at,
            )
        )
        self.db.flush()

    def get_signal_state_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
    ) -> StoredGapSignalState | None:
        matches = (
            self.db.query(GapQuestion)
            .join(
                GapQuestionMessageLink,
                GapQuestionMessageLink.gap_question_id == GapQuestion.id,
            )
            .filter(
                GapQuestion.tenant_id == tenant_id,
                GapQuestionMessageLink.assistant_message_id == assistant_message_id,
            )
            .order_by(GapQuestion.created_at.desc(), GapQuestion.id.desc())
            .all()
        )
        if not matches:
            return None
        if len(matches) > 1:
            logger.warning(
                "gap_analyzer_multiple_signal_links_for_assistant_message: tenant_id=%s assistant_message_id=%s matches=%s",
                tenant_id,
                assistant_message_id,
                len(matches),
            )

        gap_question = matches[0]
        return StoredGapSignalState(
            gap_question_id=gap_question.id,
            answer_confidence=gap_question.answer_confidence,
            had_fallback=bool(gap_question.had_fallback),
            # Phase 2 persists reject/fallback turns in the same underlying bucket.
            had_rejected=bool(gap_question.had_fallback),
            had_escalation=bool(gap_question.had_escalation),
        )

    def update_signal_weight(
        self,
        *,
        gap_question_id: UUID,
        signal_weight: float,
    ) -> None:
        gap_question = self.db.get(GapQuestion, gap_question_id)
        if gap_question is None:
            raise ValueError(f"GapQuestion not found for id={gap_question_id}")
        gap_question.gap_signal_weight = signal_weight
        self.db.add(gap_question)
        self.db.flush()

    def get_client_openai_key(self, tenant_id: UUID) -> str | None:
        client = self.db.get(Client, tenant_id)
        return client.openai_api_key if client is not None else None

    def get_latest_mode_a_hash(self, tenant_id: UUID) -> str | None:
        row = (
            self.db.query(GapDocTopic.extraction_chunk_hash)
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
            self.db.query(Embedding, Document)
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
            self.db.query(GapDismissal)
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
        extracted_at = datetime.now(timezone.utc)
        capabilities = self._capabilities
        self.db.query(GapDocTopic).filter(GapDocTopic.tenant_id == tenant_id).delete()
        if not candidates:
            self.db.add(
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
            self.db.flush()
            return

        for candidate in candidates:
            example_questions: object = _example_questions_value(
                candidate.example_questions,
                capabilities=capabilities,
            )
            self.db.add(
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
        self.db.flush()

    def list_unclustered_mode_b_questions(self, tenant_id: UUID) -> list[ModeBQuestionRecord]:
        rows = (
            self.db.query(GapQuestion)
            .filter(GapQuestion.tenant_id == tenant_id)
            .filter(GapQuestion.cluster_id.is_(None))
            .order_by(GapQuestion.created_at.asc(), GapQuestion.id.asc())
            .all()
        )
        return [
            ModeBQuestionRecord(
                question_id=row.id,
                question_text=row.question_text,
                embedding=row.embedding,
                gap_signal_weight=float(row.gap_signal_weight or 0.0),
                language=row.language,
                created_at=_aware_datetime(row.created_at),
            )
            for row in rows
        ]

    def list_mode_b_clusters(self, tenant_id: UUID) -> list[ModeBClusterRecord]:
        rows = (
            self.db.query(GapCluster)
            .filter(GapCluster.tenant_id == tenant_id)
            .filter(
                GapCluster.status.in_(
                    [
                        GapClusterStatus.active.value,
                        GapClusterStatus.closed.value,
                    ]
                )
            )
            .order_by(GapCluster.created_at.asc(), GapCluster.id.asc())
            .all()
        )
        return [
            ModeBClusterRecord(
                cluster_id=row.id,
                label=row.label,
                centroid=row.centroid,
                question_count=int(row.question_count or 0),
                aggregate_signal_weight=float(row.aggregate_signal_weight or 0.0),
                coverage_score=float(row.coverage_score) if row.coverage_score is not None else None,
                status=row.status.value if hasattr(row.status, "value") else str(row.status),
                last_question_at=_aware_datetime(row.last_question_at) if row.last_question_at else None,
            )
            for row in rows
        ]

    def update_mode_b_question_embedding(
        self,
        *,
        question_id: UUID,
        embedding: list[float],
    ) -> None:
        question = self.db.get(GapQuestion, question_id)
        if question is None:
            logger.warning(
                "gap_analyzer_mode_b_question_embedding_target_missing question_id=%s",
                question_id,
            )
            return
        question.embedding = embedding
        self.db.add(question)
        self.db.flush()

    def bulk_update_mode_b_question_embeddings(
        self,
        *,
        embeddings_by_question_id: dict[UUID, list[float]],
    ) -> None:
        if not embeddings_by_question_id:
            return
        self.db.bulk_update_mappings(
            GapQuestion,
            [
                {"id": question_id, "embedding": embedding}
                for question_id, embedding in embeddings_by_question_id.items()
            ],
        )
        self.db.flush()

    def create_mode_b_cluster(
        self,
        *,
        tenant_id: UUID,
        label: str,
        centroid: list[float],
        question_count: int,
        aggregate_signal_weight: float,
        coverage_score: float,
        status: GapClusterStatus,
        last_question_at: datetime,
        last_computed_at: datetime,
    ) -> UUID:
        capabilities = self._capabilities
        cluster = GapCluster(
            tenant_id=tenant_id,
            label=label,
            centroid=centroid,
            question_count=question_count,
            aggregate_signal_weight=aggregate_signal_weight,
            coverage_score=coverage_score,
            status=_enum_value(status, capabilities=capabilities),
            is_new=True,
            last_question_at=last_question_at,
            last_computed_at=last_computed_at,
        )
        self.db.add(cluster)
        self.db.flush()
        return cluster.id

    def assign_question_to_cluster(
        self,
        *,
        question_id: UUID,
        cluster_id: UUID,
    ) -> None:
        updated_rows = (
            self.db.query(GapQuestion)
            .filter(GapQuestion.id == question_id)
            .update({GapQuestion.cluster_id: cluster_id}, synchronize_session=False)
        )
        if updated_rows == 0:
            raise ValueError(f"GapQuestion not found for id={question_id}")
        self.db.flush()

    def update_mode_b_cluster(
        self,
        *,
        cluster_id: UUID,
        centroid: list[float],
        question_count: int,
        aggregate_signal_weight: float,
        coverage_score: float,
        status: GapClusterStatus,
        last_question_at: datetime,
        last_computed_at: datetime,
    ) -> None:
        capabilities = self._capabilities
        cluster = self.db.get(GapCluster, cluster_id)
        if cluster is None:
            raise ValueError(f"GapCluster not found for id={cluster_id}")
        cluster.centroid = centroid
        cluster.question_count = question_count
        cluster.aggregate_signal_weight = aggregate_signal_weight
        cluster.coverage_score = coverage_score
        cluster.status = _enum_value(status, capabilities=capabilities)
        cluster.last_question_at = last_question_at
        cluster.last_computed_at = last_computed_at
        self.db.add(cluster)
        self.db.flush()

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> None:
        raise NotImplementedError("Async recalc orchestration lands in Phase 5")


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _repository_capabilities(db: Session) -> _RepositoryCapabilities:
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    return _RepositoryCapabilities(
        enum_values_as_strings=dialect_name == "sqlite",
        supports_array_values=dialect_name != "sqlite",
    )


def _enum_value(value: GapClusterStatus | GapDocTopicStatus, *, capabilities: _RepositoryCapabilities) -> str | GapClusterStatus | GapDocTopicStatus:
    if capabilities.enum_values_as_strings:
        return value.value
    return value


def _example_questions_value(
    value: list[str],
    *,
    capabilities: _RepositoryCapabilities,
) -> object:
    if capabilities.supports_array_values:
        return value
    return None
