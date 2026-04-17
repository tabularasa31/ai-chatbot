"""Persistence seams and repository implementation for Gap Analyzer."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from backend.gap_analyzer._classification import (
    _classify_gap,
    _impact_statement,
)
from backend.gap_analyzer._math import (
    _cosine_similarity,
    _tokenize,
    _vector_from_unknown,
    _vector_norm,
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
    Client,
    Document,
    Embedding,
    GapAnalyzerJob,
    GapCluster,
    GapDismissal,
    GapDocTopic,
    GapQuestion,
    GapQuestionMessageLink,
)

logger = logging.getLogger(__name__)
_GAP_JOB_LEASE_SECONDS = 1800
_GAP_JOB_RETRY_DELAYS_SECONDS = (30, 120, 300)
_GAP_JOB_CLAIM_MAX_ATTEMPTS = 3
_GAP_JOB_LAST_ERROR_MAX_CHARS = 4000
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_SMOOTHING = 0.5
_BM25_MIN_MATCHED_QUERY_TERMS = 2
_BM25_CORPUS_CACHE_TTL_SECONDS = 300
_BM25_CORPUS_CACHE_MAX_ENTRIES = 128


@dataclass(frozen=True)
class _CachedBm25Document:
    exact_match_candidates: tuple[str, ...]
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class _CachedBm25Corpus:
    documents: tuple[_CachedBm25Document, ...]
    cached_at_monotonic: float


_BM25_CORPUS_CACHE: dict[tuple[UUID, tuple[str, ...]], _CachedBm25Corpus] = {}
_BM25_CORPUS_CACHE_LOCK = threading.RLock()


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
class TenantVectorMatch:
    score: float
    chunk_id: UUID


@dataclass(frozen=True)
class TenantBm25Match:
    hit: bool
    score: float
    match_kind: Literal["exact_title", "body", "none"]


@dataclass(frozen=True)
class GapJobEnqueueResult:
    status: GapCommandStatus
    enqueued: bool
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class GapJobRecord:
    job_id: UUID
    tenant_id: UUID
    job_kind: GapJobKind
    status: GapJobStatus
    trigger: str
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class _RepositoryCapabilities:
    enum_values_as_strings: bool
    supports_array_values: bool
    supports_skip_locked: bool


def _normalize_bm25_excluded_file_types(
    excluded_file_types: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(sorted(value.casefold() for value in excluded_file_types))


def _bm25_cache_key(
    tenant_id: UUID,
    excluded_file_types: tuple[str, ...],
) -> tuple[UUID, tuple[str, ...]]:
    return (tenant_id, _normalize_bm25_excluded_file_types(excluded_file_types))


def _evict_expired_bm25_cache_entries(now_monotonic: float) -> None:
    expired_keys = [
        key
        for key, corpus in _BM25_CORPUS_CACHE.items()
        if now_monotonic - corpus.cached_at_monotonic >= _BM25_CORPUS_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        _BM25_CORPUS_CACHE.pop(key, None)


def invalidate_bm25_cache_for_tenant(tenant_id: UUID | None) -> None:
    if tenant_id is None:
        return
    with _BM25_CORPUS_CACHE_LOCK:
        matching_keys = [key for key in _BM25_CORPUS_CACHE if key[0] == tenant_id]
        for key in matching_keys:
            _BM25_CORPUS_CACHE.pop(key, None)


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

    def fail_gap_job(self, *, job_id: UUID, tenant_id: UUID, error_message: str) -> bool:
        ...

    def refresh_gap_job_lease(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        ...

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> GapJobEnqueueResult:
        ...


@dataclass
class SqlAlchemyGapAnalyzerRepository:
    """Command-side persistence implementation for Gap Analyzer."""

    db: Session

    def __init__(self, db: Session):
        self.db = db

    def _load_bm25_corpus(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> _CachedBm25Corpus:
        cache_key = _bm25_cache_key(tenant_id, excluded_file_types)
        now_monotonic = time.monotonic()
        with _BM25_CORPUS_CACHE_LOCK:
            cached = _BM25_CORPUS_CACHE.get(cache_key)
            if (
                cached is not None
                and now_monotonic - cached.cached_at_monotonic
                < _BM25_CORPUS_CACHE_TTL_SECONDS
            ):
                return cached
            _evict_expired_bm25_cache_entries(now_monotonic)

        excluded = set(cache_key[1])
        documents: list[_CachedBm25Document] = []
        rows = (
            self.db.query(
                Embedding.chunk_text,
                Embedding.metadata_json,
                Document.filename,
                Document.file_type,
            )
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.client_id == tenant_id)
            .filter(Document.status == "ready")
            .filter(Embedding.chunk_text.isnot(None))
            .order_by(Document.id.asc(), Embedding.id.asc())
            .yield_per(500)
        )
        for chunk_text, metadata_json, filename, file_type in rows:
            file_type_value = str(getattr(file_type, "value", file_type)).casefold()
            if file_type_value in excluded:
                continue
            metadata = metadata_json if isinstance(metadata_json, dict) else {}
            exact_match_candidates = tuple(
                candidate.casefold()
                for candidate in (
                    _string_or_none(metadata.get("section_title")),
                    _string_or_none(metadata.get("page_title")),
                    _string_or_none(filename),
                )
                if candidate
            )
            tokens = tuple(_tokenize(chunk_text or ""))
            if not exact_match_candidates and not tokens:
                continue
            documents.append(
                _CachedBm25Document(
                    exact_match_candidates=exact_match_candidates,
                    tokens=tokens,
                )
            )

        cached = _CachedBm25Corpus(
            documents=tuple(documents),
            cached_at_monotonic=now_monotonic,
        )
        with _BM25_CORPUS_CACHE_LOCK:
            _BM25_CORPUS_CACHE[cache_key] = cached
            if len(_BM25_CORPUS_CACHE) > _BM25_CORPUS_CACHE_MAX_ENTRIES:
                oldest_key = min(
                    _BM25_CORPUS_CACHE,
                    key=lambda key: _BM25_CORPUS_CACHE[key].cached_at_monotonic,
                )
                _BM25_CORPUS_CACHE.pop(oldest_key, None)
        return cached

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
        extracted_at = datetime.now(UTC)
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
                .filter(Document.client_id == tenant_id)
                .filter(Document.status == "ready")
                .filter(Embedding.chunk_text.isnot(None))
                .filter(~Document.file_type.in_(excluded_file_types))
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
        corpus = self._load_bm25_corpus(
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
        capabilities = self._capabilities
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

    def enqueue_gap_job(
        self,
        *,
        tenant_id: UUID,
        job_kind: GapJobKind,
        trigger: str,
    ) -> GapJobEnqueueResult:
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
                status=_enum_value(GapJobStatus.queued, capabilities=self._capabilities),
                trigger=trigger,
                available_at=datetime.now(UTC),
            )
        )
        self.db.flush()
        return GapJobEnqueueResult(status=GapCommandStatus.accepted, enqueued=True)

    def claim_next_gap_job(self) -> GapJobRecord | None:
        """Claim the next eligible gap job.

        PostgreSQL uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so the row
        selection and reservation happen atomically inside the caller-owned
        transaction. Dialects without ``SKIP LOCKED`` support keep the
        retry-loop fallback for SQLite test coverage and correctness.
        """
        if self._capabilities.supports_skip_locked:
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

            candidate.status = _enum_value(GapJobStatus.in_progress, capabilities=self._capabilities)
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
                        GapAnalyzerJob.status: _enum_value(GapJobStatus.in_progress, capabilities=self._capabilities),
                        GapAnalyzerJob.leased_at: now,
                        GapAnalyzerJob.lease_expires_at: lease_expires_at,
                        GapAnalyzerJob.started_at: case(
                            (GapAnalyzerJob.started_at.is_(None), now),
                            else_=GapAnalyzerJob.started_at,
                        ),
                        GapAnalyzerJob.attempt_count: GapAnalyzerJob.attempt_count + 1,
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

    def complete_gap_job(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        now = datetime.now(UTC)
        updated_rows = (
            self.db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id)
            .filter(GapAnalyzerJob.tenant_id == tenant_id)
            .update(
                {
                    GapAnalyzerJob.status: _enum_value(GapJobStatus.completed, capabilities=self._capabilities),
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

    def fail_gap_job(self, *, job_id: UUID, tenant_id: UUID, error_message: str) -> bool:
        job = (
            self.db.query(GapAnalyzerJob)
            .filter(GapAnalyzerJob.id == job_id, GapAnalyzerJob.tenant_id == tenant_id)
            .first()
        )
        if job is None:
            logger.warning(
                "gap_analyzer_job_finalize_skipped_unexpected_tenant job_id=%s tenant_id=%s",
                job_id,
                tenant_id,
            )
            return False
        now = datetime.now(UTC)
        attempt_count = int(job.attempt_count or 0)
        max_attempts = int(job.max_attempts or 0)
        if attempt_count >= max_attempts:
            job.status = _enum_value(GapJobStatus.failed, capabilities=self._capabilities)
            job.finished_at = now
            job.available_at = now
        else:
            retry_delay_seconds = _retry_delay_seconds(attempt_count)
            job.status = _enum_value(GapJobStatus.retry, capabilities=self._capabilities)
            job.available_at = now + timedelta(seconds=retry_delay_seconds)
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
            if cluster_updated_at is not None and (last_updated is None or cluster_updated_at > last_updated):
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

    @property
    def _is_postgres(self) -> bool:
        return (self.db.bind.dialect.name if self.db.bind is not None else "") == "postgresql"

    def _mode_a_embedding_rows(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> list[tuple[Embedding, Document]]:
        rows_query = (
            self.db.query(Embedding, Document)
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

def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _truncate_gap_job_error(error_message: str) -> str:
    if len(error_message) <= _GAP_JOB_LAST_ERROR_MAX_CHARS:
        return error_message
    truncated_prefix = "...[truncated]\n"
    tail_size = _GAP_JOB_LAST_ERROR_MAX_CHARS - len(truncated_prefix)
    if tail_size <= 0:
        return error_message[-_GAP_JOB_LAST_ERROR_MAX_CHARS :]
    # Keep the traceback tail because the final frames and exception text are usually the most actionable.
    return truncated_prefix + error_message[-tail_size:]


def _bm25_streamed_score(
    *,
    doc_length: int,
    term_frequencies: dict[str, int],
    query_token_counts: Counter[str],
    idfs: dict[str, float],
    average_doc_length: float,
) -> float:
    if doc_length <= 0 or average_doc_length <= 0:
        return 0.0
    score = 0.0
    length_norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * (doc_length / average_doc_length))
    for token, query_count in query_token_counts.items():
        term_frequency = term_frequencies.get(token, 0)
        if term_frequency <= 0:
            continue
        idf = idfs.get(token, 0.0)
        if idf <= 0.0:
            continue
        score += query_count * idf * (
            (term_frequency * (_BM25_K1 + 1.0)) / (term_frequency + length_norm)
        )
    return score


def _repository_capabilities(db: Session) -> _RepositoryCapabilities:
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    return _RepositoryCapabilities(
        enum_values_as_strings=dialect_name == "sqlite",
        supports_array_values=dialect_name != "sqlite",
        supports_skip_locked=dialect_name == "postgresql",
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


def _gap_job_status(value: GapJobStatus | str) -> GapJobStatus:
    if isinstance(value, GapJobStatus):
        return value
    return GapJobStatus(str(value))


def _gap_job_kind(value: GapJobKind | str) -> GapJobKind:
    if isinstance(value, GapJobKind):
        return value
    return GapJobKind(str(value))


def _retry_delay_seconds(attempt_count: int) -> int:
    index = max(0, min(len(_GAP_JOB_RETRY_DELAYS_SECONDS) - 1, max(attempt_count - 1, 0)))
    return _GAP_JOB_RETRY_DELAYS_SECONDS[index]


def _remaining_lease_seconds(lease_expires_at: datetime | None) -> int | None:
    if lease_expires_at is None:
        return None
    aware_lease_expires_at = _aware_datetime(lease_expires_at)
    remaining = int((aware_lease_expires_at - datetime.now(UTC)).total_seconds())
    return max(1, remaining) if remaining > 0 else None


