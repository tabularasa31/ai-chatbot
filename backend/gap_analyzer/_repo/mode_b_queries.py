"""Mode B question and cluster query operations, plus vector/BM25 corpus search."""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

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
    _Bm25CacheOps,
)
from backend.gap_analyzer._repo.capabilities import (
    _aware_datetime,
    _enum_value,
    _repository_capabilities,
)
from backend.gap_analyzer._repo.mode_a_queries import _mode_a_embedding_rows
from backend.gap_analyzer._repo.records import (
    ModeBClusterRecord,
    ModeBQuestionRecord,
    TenantBm25Match,
    TenantVectorMatch,
)
from backend.gap_analyzer.enums import GapClusterStatus
from backend.models import Document, Embedding, GapCluster, GapQuestion

logger = logging.getLogger(__name__)


class _ModeBQueriesOps:
    def __init__(self, db: Session) -> None:
        self._db = db

    @property
    def _is_postgres(self) -> bool:
        return (self._db.bind.dialect.name if self._db.bind is not None else "") == "postgresql"

    def list_unclustered_mode_b_questions(self, tenant_id: UUID) -> list[ModeBQuestionRecord]:
        rows = (
            self._db.query(GapQuestion)
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
            self._db.query(GapCluster)
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
                self._db.query(Embedding.id, distance_expr.label("distance"))
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

        rows = _mode_a_embedding_rows(
            self._db,
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
        corpus = _Bm25CacheOps(self._db).load_or_cache(
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
        question = self._db.get(GapQuestion, question_id)
        if question is None:
            logger.warning(
                "gap_analyzer_mode_b_question_embedding_target_missing question_id=%s",
                question_id,
            )
            return
        question.embedding = embedding
        self._db.add(question)
        self._db.flush()

    def bulk_update_mode_b_question_embeddings(
        self,
        *,
        embeddings_by_question_id: dict[UUID, list[float]],
    ) -> None:
        if not embeddings_by_question_id:
            return
        self._db.bulk_update_mappings(
            GapQuestion,
            [
                {"id": question_id, "embedding": embedding}
                for question_id, embedding in embeddings_by_question_id.items()
            ],
        )
        self._db.flush()

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
        capabilities = _repository_capabilities(self._db)
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
        self._db.add(cluster)
        self._db.flush()
        return cluster.id

    def assign_question_to_cluster(
        self,
        *,
        question_id: UUID,
        cluster_id: UUID,
    ) -> None:
        updated_rows = (
            self._db.query(GapQuestion)
            .filter(GapQuestion.id == question_id)
            .update({GapQuestion.cluster_id: cluster_id}, synchronize_session=False)
        )
        if updated_rows == 0:
            raise ValueError(f"GapQuestion not found for id={question_id}")
        self._db.flush()

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
        capabilities = _repository_capabilities(self._db)
        cluster = self._db.get(GapCluster, cluster_id)
        if cluster is None:
            raise ValueError(f"GapCluster not found for id={cluster_id}")
        cluster.centroid = centroid
        cluster.question_count = question_count
        cluster.aggregate_signal_weight = aggregate_signal_weight
        cluster.coverage_score = coverage_score
        cluster.status = _enum_value(status, capabilities=capabilities)
        cluster.last_question_at = last_question_at
        cluster.last_computed_at = last_computed_at
        self._db.add(cluster)
        self._db.flush()
