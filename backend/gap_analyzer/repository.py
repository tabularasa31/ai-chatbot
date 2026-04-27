"""Persistence layer for Gap Analyzer."""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from backend.core.openai_errors import OpenAIFailureKind
from backend.gap_analyzer._classification import _classify_gap, _impact_statement
from backend.gap_analyzer._math import (
    _cosine_similarity,
    _tokenize,
    _vector_from_unknown,
    _vector_norm,
)
from backend.gap_analyzer._repo.bm25_cache import (
    _BM25_MIN_MATCHED_QUERY_TERMS,
    _BM25_SMOOTHING,
    _bm25_streamed_score,
    _load_or_cache_bm25_corpus,
    invalidate_bm25_cache_for_tenant,  # noqa: F401 — public, re-exported for callers
)
from backend.gap_analyzer._repo.capabilities import (
    _aware_datetime,
    _enum_value,
    _example_questions_value,
    _repository_capabilities,
    _string_or_none,
)
from backend.gap_analyzer._repo.job_queue_helpers import (
    _GAP_JOB_CLAIM_MAX_ATTEMPTS,
    _GAP_JOB_LEASE_SECONDS,
    _gap_job_kind,
    _gap_job_status,
    _remaining_lease_seconds,
    _truncate_gap_job_error,
)
from backend.gap_analyzer._repo.job_retry import (
    effective_max_attempts,
    retry_delay_for_kind,
)
from backend.gap_analyzer._repo.records import (
    GapJobEnqueueResult,
    GapJobRecord,
    ModeACorpusChunk,
    ModeADismissalRecord,
    ModeBClusterRecord,
    ModeBQuestionRecord,
    StoredGapSignalState,
    TenantBm25Match,
    TenantVectorMatch,
)
from backend.gap_analyzer.enums import (
    GapClusterStatus,
    GapCommandStatus,
    GapDocTopicStatus,
    GapJobKind,
    GapJobStatus,
    GapSource,
)
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.gap_analyzer.schemas import GapRunMode, GapSummaryResponse
from backend.models import (
    Document,
    Embedding,
    GapAnalyzerJob,
    GapCluster,
    GapDismissal,
    GapDocTopic,
    GapQuestion,
    GapQuestionMessageLink,
    Tenant,
)

logger = logging.getLogger(__name__)


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

    def vector_top_k_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_embedding: list[float],
        top_k: int,
        excluded_file_types: tuple[str, ...],
    ) -> list[TenantVectorMatch]:
        ...

    def bm25_match_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_text: str,
        excluded_file_types: tuple[str, ...],
    ) -> TenantBm25Match:
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
        is_new: bool = True,
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

    def enqueue_gap_job(
        self,
        *,
        tenant_id: UUID,
        job_kind: GapJobKind,
        trigger: str,
    ) -> GapJobEnqueueResult:
        ...

    def claim_next_gap_job(self) -> GapJobRecord | None:
        ...

    def complete_gap_job(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        ...

    def fail_gap_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        error_message: str,
        failure_kind: OpenAIFailureKind = OpenAIFailureKind.UNKNOWN,
        retry_after_seconds: float | None = None,
    ) -> bool:
        ...

    def release_gap_job_for_retry(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> bool:
        ...

    def refresh_gap_job_lease(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        ...

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> GapJobEnqueueResult:
        ...


class SqlAlchemyGapAnalyzerRepository:
    """Gap Analyzer persistence implementation."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @property
    def _is_postgres(self) -> bool:
        return (self.db.bind.dialect.name if self.db.bind is not None else "") == "postgresql"

    # --- helpers ---

    def _mode_a_embedding_rows(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> list[tuple[Embedding, Document]]:
        rows_query = (
            self.db.query(Embedding, Document)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
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
            if str(getattr(document.file_type, "value", document.file_type)).casefold()
            not in excluded
        ]

    # --- signals ---

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

    # --- mode A ---

    def get_client_openai_key(self, tenant_id: UUID) -> str | None:
        tenant = self.db.get(Tenant, tenant_id)
        return tenant.openai_api_key if tenant is not None else None

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
            .filter(Document.tenant_id == tenant_id)
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
        extracted_at = datetime.now(UTC)
        capabilities = _repository_capabilities(self.db)
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

    # --- mode B ---

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

    def vector_top_k_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_embedding: list[float],
        top_k: int,
        excluded_file_types: tuple[str, ...],
    ) -> list[TenantVectorMatch]:
        if not query_embedding or top_k <= 0:
            return []

        if self._is_postgres:
            distance_expr = Embedding.vector.cosine_distance(query_embedding)
            scored_rows = (
                self.db.query(Embedding.id, distance_expr.label("distance"))
                .join(Document, Embedding.document_id == Document.id)
                .filter(Document.tenant_id == tenant_id)
                .filter(Document.status == "ready")
                .filter(Embedding.chunk_text.isnot(None))
                .filter(~Document.file_type.in_([ft.casefold() for ft in excluded_file_types]))
                .order_by(distance_expr.asc(), Embedding.id.asc())
                .limit(top_k)
                .all()
            )
            return [
                TenantVectorMatch(
                    score=max(0.0, min(1.0, 1.0 - float(distance))),
                    chunk_id=chunk_id,
                )
                for chunk_id, distance in scored_rows
            ]

        rows = self._mode_a_embedding_rows(
            tenant_id=tenant_id,
            excluded_file_types=excluded_file_types,
        )
        if not rows:
            return []
        scored_matches: list[TenantVectorMatch] = []
        query_norm = _vector_norm(query_embedding)
        for embedding, _document in rows:
            vector = _vector_from_unknown(embedding.vector)
            if vector is None:
                continue
            scored_matches.append(
                TenantVectorMatch(
                    score=_cosine_similarity(
                        query_embedding,
                        vector,
                        first_norm=query_norm,
                        second_norm=_vector_norm(vector),
                    ),
                    chunk_id=embedding.id,
                )
            )
        scored_matches.sort(key=lambda item: (-item.score, str(item.chunk_id)))
        return scored_matches[:top_k]

    def bm25_match_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_text: str,
        excluded_file_types: tuple[str, ...],
    ) -> TenantBm25Match:
        normalized_query = query_text.strip().casefold()
        if not normalized_query:
            return TenantBm25Match(hit=False, score=0.0, match_kind="none")
        query_tokens = _tokenize(query_text)
        query_token_counts = Counter(query_tokens)
        if not query_token_counts:
            return TenantBm25Match(hit=False, score=0.0, match_kind="none")
        query_terms = set(query_token_counts)
        corpus = _load_or_cache_bm25_corpus(
            self.db,
            tenant_id=tenant_id,
            excluded_file_types=excluded_file_types,
        )

        total_docs = 0
        total_doc_length = 0
        doc_frequencies = {token: 0 for token in query_terms}
        matching_docs: list[tuple[int, dict[str, int]]] = []
        for document in corpus.documents:
            for candidate in document.exact_match_candidates:
                if candidate == normalized_query:
                    return TenantBm25Match(hit=True, score=1.0, match_kind="exact_title")

            tokens = document.tokens
            if not tokens:
                continue

            total_docs += 1
            doc_length = len(tokens)
            total_doc_length += doc_length

            term_frequencies: dict[str, int] = {}
            seen_terms: set[str] = set()
            for token in tokens:
                if token not in query_terms:
                    continue
                term_frequencies[token] = term_frequencies.get(token, 0) + 1
                if token not in seen_terms:
                    doc_frequencies[token] += 1
                    seen_terms.add(token)
            if term_frequencies:
                matching_docs.append((doc_length, term_frequencies))

        if total_docs == 0 or not matching_docs:
            return TenantBm25Match(hit=False, score=0.0, match_kind="none")

        average_doc_length = total_doc_length / total_docs if total_docs > 0 else 0.0
        idfs = {
            # Use a smoothed positive IDF so streamed BM25 keeps ranking
            # single-term tenant queries instead of collapsing to <= 0 when a
            # term appears in every matching chunk of a very small corpus.
            token: math.log1p(
                (total_docs - doc_frequency + _BM25_SMOOTHING)
                / (doc_frequency + _BM25_SMOOTHING)
            )
            for token, doc_frequency in doc_frequencies.items()
            if doc_frequency > 0
        }
        # Require at least one document to match enough distinct query terms so
        # broad lexical overlap cannot win on a single frequent token alone.
        best_doc_match_count = max(
            len(term_frequencies) for _, term_frequencies in matching_docs
        )
        min_required_match_count = min(
            len(query_terms), _BM25_MIN_MATCHED_QUERY_TERMS
        )
        if best_doc_match_count < min_required_match_count:
            return TenantBm25Match(hit=False, score=0.0, match_kind="none")
        best_score = max(
            _bm25_streamed_score(
                doc_length=doc_length,
                term_frequencies=term_frequencies,
                query_token_counts=query_token_counts,
                idfs=idfs,
                average_doc_length=average_doc_length,
            )
            for doc_length, term_frequencies in matching_docs
        )
        if best_score <= 0.0:
            return TenantBm25Match(hit=False, score=0.0, match_kind="none")
        return TenantBm25Match(
            hit=True,
            score=best_score / (best_score + 1.0),
            match_kind="body",
        )

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
        is_new: bool = True,
    ) -> UUID:
        capabilities = _repository_capabilities(self.db)
        cluster = GapCluster(
            tenant_id=tenant_id,
            label=label,
            centroid=centroid,
            question_count=question_count,
            aggregate_signal_weight=aggregate_signal_weight,
            coverage_score=coverage_score,
            status=_enum_value(status, capabilities=capabilities),
            is_new=is_new,
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
        capabilities = _repository_capabilities(self.db)
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

    # --- job queue ---

    def enqueue_gap_job(
        self,
        *,
        tenant_id: UUID,
        job_kind: GapJobKind,
        trigger: str,
    ) -> GapJobEnqueueResult:
        capabilities = _repository_capabilities(self.db)
        existing = (
            self.db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.tenant_id == tenant_id, GapAnalyzerJob.job_kind == job_kind)
            .filter(GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value, GapJobStatus.in_progress.value]))
            .order_by(GapAnalyzerJob.created_at.desc(), GapAnalyzerJob.id.desc())
            .first()
        )
        if existing is not None:
            status = GapCommandStatus.in_progress if _gap_job_status(existing.status) == GapJobStatus.in_progress else GapCommandStatus.accepted
            retry_after_seconds = _remaining_lease_seconds(existing.lease_expires_at) if _gap_job_status(existing.status) == GapJobStatus.in_progress else None
            return GapJobEnqueueResult(status=status, enqueued=False, retry_after_seconds=retry_after_seconds)

        self.db.add(
            GapAnalyzerJob(
                tenant_id=tenant_id,
                job_kind=job_kind,
                status=_enum_value(GapJobStatus.queued, capabilities=capabilities),
                trigger=trigger,
                available_at=datetime.now(UTC),
            )
        )
        self.db.flush()
        return GapJobEnqueueResult(status=GapCommandStatus.accepted, enqueued=True)

    def claim_next_gap_job(self) -> GapJobRecord | None:
        """Claim the next eligible gap job, using SKIP LOCKED when available."""
        capabilities = _repository_capabilities(self.db)
        if capabilities.supports_skip_locked:
            now = datetime.now(UTC)
            lease_expires_at = now + timedelta(seconds=_GAP_JOB_LEASE_SECONDS)
            candidate = (
                self.db.query(GapAnalyzerJob)
                .filter(
                    or_(
                        and_(
                            GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value]),
                            GapAnalyzerJob.available_at <= now,
                        ),
                        and_(
                            GapAnalyzerJob.status == GapJobStatus.in_progress,
                            GapAnalyzerJob.lease_expires_at.isnot(None),
                            GapAnalyzerJob.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(GapAnalyzerJob.available_at.asc(), GapAnalyzerJob.created_at.asc(), GapAnalyzerJob.id.asc())
                .with_for_update(skip_locked=True, of=GapAnalyzerJob)
                .limit(1)
                .first()
            )
            if candidate is None:
                return None

            candidate.status = _enum_value(GapJobStatus.in_progress, capabilities=capabilities)
            candidate.leased_at = now
            candidate.lease_expires_at = lease_expires_at
            candidate.started_at = candidate.started_at or now
            candidate.attempt_count = int(candidate.attempt_count or 0) + 1
            candidate.updated_at = now
            self.db.add(candidate)
            self.db.flush()
            return GapJobRecord(
                job_id=candidate.id,
                tenant_id=candidate.tenant_id,
                job_kind=_gap_job_kind(candidate.job_kind),
                status=_gap_job_status(candidate.status),
                trigger=candidate.trigger,
                attempt_count=int(candidate.attempt_count or 0),
                max_attempts=int(candidate.max_attempts or 0),
            )

        for _ in range(_GAP_JOB_CLAIM_MAX_ATTEMPTS):
            now = datetime.now(UTC)
            lease_expires_at = now + timedelta(seconds=_GAP_JOB_LEASE_SECONDS)
            candidate = (
                self.db.query(GapAnalyzerJob.id)
                .filter(
                    or_(
                        and_(
                            GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value]),
                            GapAnalyzerJob.available_at <= now,
                        ),
                        and_(
                            GapAnalyzerJob.status == GapJobStatus.in_progress,
                            GapAnalyzerJob.lease_expires_at.isnot(None),
                            GapAnalyzerJob.lease_expires_at < now,
                        ),
                    )
                )
                .order_by(GapAnalyzerJob.available_at.asc(), GapAnalyzerJob.created_at.asc(), GapAnalyzerJob.id.asc())
                .first()
            )
            if candidate is None:
                return None

            job_id = candidate[0]
            updated_rows = (
                self.db.query(GapAnalyzerJob)
                .filter(GapAnalyzerJob.id == job_id)
                .filter(
                    or_(
                        and_(
                            GapAnalyzerJob.status.in_([GapJobStatus.queued.value, GapJobStatus.retry.value]),
                            GapAnalyzerJob.available_at <= now,
                        ),
                        and_(
                            GapAnalyzerJob.status == GapJobStatus.in_progress,
                            GapAnalyzerJob.lease_expires_at.isnot(None),
                            GapAnalyzerJob.lease_expires_at < now,
                        ),
                    )
                )
                .update(
                    {
                        GapAnalyzerJob.status: _enum_value(GapJobStatus.in_progress, capabilities=capabilities),
                        GapAnalyzerJob.leased_at: now,
                        GapAnalyzerJob.lease_expires_at: lease_expires_at,
                        GapAnalyzerJob.started_at: case(
                            (GapAnalyzerJob.started_at.is_(None), now),
                            else_=GapAnalyzerJob.started_at,
                        ),
                        GapAnalyzerJob.attempt_count: case(
                            (GapAnalyzerJob.attempt_count.is_(None), 1),
                            else_=GapAnalyzerJob.attempt_count + 1,
                        ),
                        GapAnalyzerJob.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if updated_rows == 0:
                continue
            self.db.flush()
            job = self.db.get(GapAnalyzerJob, job_id)
            if job is None:
                return None
            return GapJobRecord(
                job_id=job.id,
                tenant_id=job.tenant_id,
                job_kind=_gap_job_kind(job.job_kind),
                status=_gap_job_status(job.status),
                trigger=job.trigger,
                attempt_count=int(job.attempt_count or 0),
                max_attempts=int(job.max_attempts or 0),
            )
        return None

    def refresh_gap_job_lease(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=_GAP_JOB_LEASE_SECONDS)
        updated_rows = (
            self.db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .filter(GapAnalyzerJob.status == GapJobStatus.in_progress)
            .update(
                {
                    GapAnalyzerJob.leased_at: now,
                    GapAnalyzerJob.lease_expires_at: lease_expires_at,
                    GapAnalyzerJob.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        self.db.flush()
        return updated_rows > 0

    def release_gap_job_for_retry(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> bool:
        capabilities = _repository_capabilities(self.db)
        now = datetime.now(UTC)
        updated_rows = (
            self.db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .filter(GapAnalyzerJob.status == GapJobStatus.in_progress)
            .update(
                {
                    GapAnalyzerJob.status: _enum_value(GapJobStatus.retry, capabilities=capabilities),
                    GapAnalyzerJob.leased_at: None,
                    GapAnalyzerJob.lease_expires_at: None,
                    GapAnalyzerJob.available_at: now,
                    GapAnalyzerJob.updated_at: now,
                    GapAnalyzerJob.last_error: _truncate_gap_job_error(
                        f"released_for_graceful_shutdown: {reason}"
                    ),
                },
                synchronize_session=False,
            )
        )
        self.db.flush()
        return updated_rows > 0

    def complete_gap_job(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        now = datetime.now(UTC)
        updated_rows = (
            self.db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .filter(GapAnalyzerJob.status == GapJobStatus.in_progress)
            .update(
                {
                    GapAnalyzerJob.status: _enum_value(GapJobStatus.completed, capabilities=_repository_capabilities(self.db)),
                    GapAnalyzerJob.finished_at: now,
                    GapAnalyzerJob.leased_at: None,
                    GapAnalyzerJob.lease_expires_at: None,
                    GapAnalyzerJob.updated_at: now,
                    GapAnalyzerJob.last_error: None,
                },
                synchronize_session=False,
            )
        )
        self.db.flush()
        if updated_rows == 0:
            logger.warning(
                "gap_analyzer_job_finalize_skipped_unexpected_tenant job_id=%s tenant_id=%s",
                job_id,
                tenant_id,
            )
            return False
        return True

    def fail_gap_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        error_message: str,
        failure_kind: OpenAIFailureKind = OpenAIFailureKind.UNKNOWN,
        retry_after_seconds: float | None = None,
    ) -> bool:
        job = (
            self.db.query(GapAnalyzerJob)
            .filter(
                GapAnalyzerJob.id == job_id,
                GapAnalyzerJob.tenant_id == tenant_id,
                GapAnalyzerJob.status == GapJobStatus.in_progress,
            )
            .first()
        )
        if job is None:
            logger.warning(
                "gap_analyzer_job_finalize_skipped_unexpected_tenant job_id=%s tenant_id=%s",
                job_id,
                tenant_id,
            )
            return False
        capabilities = _repository_capabilities(self.db)
        now = datetime.now(UTC)
        attempt_count = int(job.attempt_count or 0)
        max_attempts = effective_max_attempts(job, failure_kind)
        final_failure = (
            failure_kind == OpenAIFailureKind.PERMANENT or attempt_count >= max_attempts
        )
        if final_failure:
            job.status = _enum_value(GapJobStatus.failed, capabilities=capabilities)
            job.finished_at = now
            job.available_at = now
            logger.warning(
                "gap_analyzer_job_final_failure",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "job_kind": str(job.job_kind),
                    "attempt_count": attempt_count,
                    "failure_kind": failure_kind.value,
                    "last_error_preview": error_message[:200],
                },
            )
        else:
            retry_delay = retry_delay_for_kind(
                attempt_count=attempt_count,
                failure_kind=failure_kind,
                retry_after_seconds=retry_after_seconds,
            )
            job.status = _enum_value(GapJobStatus.retry, capabilities=capabilities)
            job.available_at = now + timedelta(seconds=retry_delay)
            logger.info(
                "gap_analyzer_job_retry_scheduled",
                extra={
                    "job_id": str(job_id),
                    "tenant_id": str(tenant_id),
                    "attempt_count": attempt_count,
                    "next_attempt": attempt_count + 1,
                    "failure_kind": failure_kind.value,
                    "delay_seconds": retry_delay,
                    "retry_after_hint": retry_after_seconds,
                },
            )
        job.leased_at = None
        job.lease_expires_at = None
        job.updated_at = now
        job.last_error = _truncate_gap_job_error(error_message)
        self.db.add(job)
        self.db.flush()
        return True

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> GapJobEnqueueResult:
        results: list[GapJobEnqueueResult] = []
        if mode in {GapRunMode.mode_a, GapRunMode.both}:
            results.append(
                self.enqueue_gap_job(
                    tenant_id=tenant_id,
                    job_kind=GapJobKind.mode_a,
                    trigger="manual",
                )
            )
        if mode in {GapRunMode.mode_b, GapRunMode.both}:
            results.append(
                self.enqueue_gap_job(
                    tenant_id=tenant_id,
                    job_kind=GapJobKind.mode_b,
                    trigger="manual",
                )
            )
        if any(result.enqueued for result in results):
            return GapJobEnqueueResult(status=GapCommandStatus.accepted, enqueued=True)
        retry_after_seconds = max(
            [result.retry_after_seconds for result in results if result.retry_after_seconds is not None],
            default=None,
        )
        return GapJobEnqueueResult(
            status=GapCommandStatus.in_progress if results else GapCommandStatus.accepted,
            enqueued=False,
            retry_after_seconds=retry_after_seconds,
        )

    # --- summary ---

    def get_gap_summary(self, *, tenant_id: UUID) -> GapSummaryResponse:
        active_linked_mode_a_ids = {
            linked_doc_topic_id
            for (linked_doc_topic_id,) in (
                self.db.query(GapCluster.linked_doc_topic_id)
                .filter(GapCluster.tenant_id == tenant_id)
                .filter(GapCluster.label.isnot(None))
                .filter(GapCluster.status == GapClusterStatus.active)
                .filter(GapCluster.linked_doc_topic_id.isnot(None))
                .all()
            )
            if linked_doc_topic_id is not None
        }
        dismissed_mode_a_ids = {
            gap_id
            for (gap_id,) in (
                self.db.query(GapDismissal.gap_id)
                .filter(GapDismissal.tenant_id == tenant_id)
                .filter(GapDismissal.source == GapSource.mode_a)
                .all()
            )
        }

        topic_query = (
            self.db.query(
                GapDocTopic.id,
                GapDocTopic.coverage_score,
                GapDocTopic.is_new,
                GapDocTopic.extracted_at,
            )
            .filter(GapDocTopic.tenant_id == tenant_id)
            .filter(GapDocTopic.topic_label.isnot(None))
            .filter(GapDocTopic.status == GapDocTopicStatus.active)
        )
        if dismissed_mode_a_ids:
            topic_query = topic_query.filter(~GapDocTopic.id.in_(dismissed_mode_a_ids))
        if active_linked_mode_a_ids:
            topic_query = topic_query.filter(~GapDocTopic.id.in_(active_linked_mode_a_ids))

        cluster_rows = (
            self.db.query(
                GapCluster.coverage_score,
                GapCluster.is_new,
                GapCluster.last_computed_at,
                GapCluster.last_question_at,
                GapCluster.created_at,
            )
            .filter(GapCluster.tenant_id == tenant_id)
            .filter(GapCluster.label.isnot(None))
            .filter(GapCluster.status == GapClusterStatus.active)
            .all()
        )

        uncovered_count = 0
        partial_count = 0
        total_active = 0
        new_badge_count = 0
        last_updated: datetime | None = None

        for _topic_id, coverage_score, is_new, extracted_at in topic_query.all():
            total_active += 1
            classification = _classify_gap(coverage_score)
            if classification == "uncovered":
                uncovered_count += 1
            elif classification == "partial":
                partial_count += 1
            if bool(is_new):
                new_badge_count += 1
            if extracted_at is not None and (last_updated is None or extracted_at > last_updated):
                last_updated = extracted_at

        for coverage_score, is_new, last_computed_at, last_question_at, created_at in cluster_rows:
            total_active += 1
            classification = _classify_gap(coverage_score)
            if classification == "uncovered":
                uncovered_count += 1
            elif classification == "partial":
                partial_count += 1
            if bool(is_new):
                new_badge_count += 1
            cluster_updated_at = last_computed_at or last_question_at or created_at
            if cluster_updated_at is not None:
                cluster_updated_at = _aware_datetime(cluster_updated_at)
                if last_updated is None or cluster_updated_at > last_updated:
                    last_updated = cluster_updated_at

        return GapSummaryResponse(
            total_active=total_active,
            uncovered_count=uncovered_count,
            partial_count=partial_count,
            impact_statement=_impact_statement(
                total_active=total_active,
                uncovered_count=uncovered_count,
                partial_count=partial_count,
            ),
            new_badge_count=new_badge_count,
            last_updated=last_updated,
        )
