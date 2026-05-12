"""Persistence layer for Gap Analyzer.

Thin facade — all real persistence lives in `_repo/` submodules:

- `_repo/signals.py`     — GapQuestion signal ingestion (Phase 2)
- `_repo/mode_a.py`      — Mode A corpus/dismissals/topics
- `_repo/mode_b.py`      — Mode B clusters + per-tenant vector/BM25 retrieval
- `_repo/job_queue.py`   — Job enqueue/claim/finalize/lease management
- `_repo/summary.py`     — Active gap summary read model

Records, dialect capabilities, BM25 cache, and retry/lease helpers live in the
existing `_repo/records.py`, `_repo/capabilities.py`, `_repo/bm25_cache.py`,
`_repo/job_queue_helpers.py`, and `_repo/job_retry.py` modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.core.openai_errors import OpenAIFailureKind
from backend.gap_analyzer._repo import (
    job_queue as _job_queue,
)
from backend.gap_analyzer._repo import (
    mode_a as _mode_a,
)
from backend.gap_analyzer._repo import (
    mode_b as _mode_b,
)
from backend.gap_analyzer._repo import (
    signals as _signals,
)
from backend.gap_analyzer._repo import (
    summary as _summary,
)
from backend.gap_analyzer._repo.bm25_cache import (
    invalidate_bm25_cache_for_tenant,
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
    GapJobKind,
)
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.gap_analyzer.schemas import GapRunMode, GapSummaryResponse

__all__ = [
    "GapAnalyzerRepository",
    "GapJobEnqueueResult",
    "GapJobRecord",
    "ModeACorpusChunk",
    "ModeADismissalRecord",
    "ModeBClusterRecord",
    "ModeBQuestionRecord",
    "SqlAlchemyGapAnalyzerRepository",
    "StoredGapSignalState",
    "TenantBm25Match",
    "TenantVectorMatch",
    "invalidate_bm25_cache_for_tenant",
]


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
    """Gap Analyzer persistence facade.

    Delegates each operation to a focused `_repo/` submodule. No business logic
    lives here — only a 1:1 mapping from method name to module function.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- signals ---

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
        _signals.store_signal(self.db, signal, signal_weight=signal_weight)

    def get_signal_state_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
    ) -> StoredGapSignalState | None:
        return _signals.get_signal_state_for_assistant_message(
            self.db,
            tenant_id=tenant_id,
            assistant_message_id=assistant_message_id,
        )

    def update_signal_weight(
        self,
        *,
        gap_question_id: UUID,
        signal_weight: float,
    ) -> None:
        _signals.update_signal_weight(
            self.db,
            gap_question_id=gap_question_id,
            signal_weight=signal_weight,
        )

    # --- mode A ---

    def get_client_openai_key(self, tenant_id: UUID) -> str | None:
        return _mode_a.get_client_openai_key(self.db, tenant_id)

    def get_latest_mode_a_hash(self, tenant_id: UUID) -> str | None:
        return _mode_a.get_latest_mode_a_hash(self.db, tenant_id)

    def get_mode_a_corpus_chunks(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> list[ModeACorpusChunk]:
        return _mode_a.get_mode_a_corpus_chunks(
            self.db,
            tenant_id=tenant_id,
            excluded_file_types=excluded_file_types,
        )

    def list_mode_a_dismissals(self, tenant_id: UUID) -> list[ModeADismissalRecord]:
        return _mode_a.list_mode_a_dismissals(self.db, tenant_id)

    def replace_mode_a_topics(
        self,
        *,
        tenant_id: UUID,
        candidates: list[ModeATopicCandidate],
        coverage_scores: dict[str, float],
        topic_embeddings: dict[str, list[float]],
        extraction_chunk_hash: str,
    ) -> None:
        _mode_a.replace_mode_a_topics(
            self.db,
            tenant_id=tenant_id,
            candidates=candidates,
            coverage_scores=coverage_scores,
            topic_embeddings=topic_embeddings,
            extraction_chunk_hash=extraction_chunk_hash,
        )

    # --- mode B ---

    def list_unclustered_mode_b_questions(self, tenant_id: UUID) -> list[ModeBQuestionRecord]:
        return _mode_b.list_unclustered_mode_b_questions(self.db, tenant_id)

    def list_mode_b_clusters(self, tenant_id: UUID) -> list[ModeBClusterRecord]:
        return _mode_b.list_mode_b_clusters(self.db, tenant_id)

    def vector_top_k_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_embedding: list[float],
        top_k: int,
        excluded_file_types: tuple[str, ...],
    ) -> list[TenantVectorMatch]:
        return _mode_b.vector_top_k_for_tenant(
            self.db,
            tenant_id=tenant_id,
            query_embedding=query_embedding,
            top_k=top_k,
            excluded_file_types=excluded_file_types,
        )

    def bm25_match_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_text: str,
        excluded_file_types: tuple[str, ...],
    ) -> TenantBm25Match:
        return _mode_b.bm25_match_for_tenant(
            self.db,
            tenant_id=tenant_id,
            query_text=query_text,
            excluded_file_types=excluded_file_types,
        )

    def update_mode_b_question_embedding(
        self,
        *,
        question_id: UUID,
        embedding: list[float],
    ) -> None:
        _mode_b.update_mode_b_question_embedding(
            self.db,
            question_id=question_id,
            embedding=embedding,
        )

    def bulk_update_mode_b_question_embeddings(
        self,
        *,
        embeddings_by_question_id: dict[UUID, list[float]],
    ) -> None:
        _mode_b.bulk_update_mode_b_question_embeddings(
            self.db,
            embeddings_by_question_id=embeddings_by_question_id,
        )

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
        return _mode_b.create_mode_b_cluster(
            self.db,
            tenant_id=tenant_id,
            label=label,
            centroid=centroid,
            question_count=question_count,
            aggregate_signal_weight=aggregate_signal_weight,
            coverage_score=coverage_score,
            status=status,
            last_question_at=last_question_at,
            last_computed_at=last_computed_at,
            is_new=is_new,
        )

    def assign_question_to_cluster(
        self,
        *,
        question_id: UUID,
        cluster_id: UUID,
    ) -> None:
        _mode_b.assign_question_to_cluster(
            self.db,
            question_id=question_id,
            cluster_id=cluster_id,
        )

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
        _mode_b.update_mode_b_cluster(
            self.db,
            cluster_id=cluster_id,
            centroid=centroid,
            question_count=question_count,
            aggregate_signal_weight=aggregate_signal_weight,
            coverage_score=coverage_score,
            status=status,
            last_question_at=last_question_at,
            last_computed_at=last_computed_at,
        )

    # --- job queue ---

    def enqueue_gap_job(
        self,
        *,
        tenant_id: UUID,
        job_kind: GapJobKind,
        trigger: str,
    ) -> GapJobEnqueueResult:
        return _job_queue.enqueue_gap_job(
            self.db,
            tenant_id=tenant_id,
            job_kind=job_kind,
            trigger=trigger,
        )

    def claim_next_gap_job(self) -> GapJobRecord | None:
        return _job_queue.claim_next_gap_job(self.db)

    def refresh_gap_job_lease(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        return _job_queue.refresh_gap_job_lease(
            self.db, job_id=job_id, tenant_id=tenant_id
        )

    def release_gap_job_for_retry(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> bool:
        return _job_queue.release_gap_job_for_retry(
            self.db, job_id=job_id, tenant_id=tenant_id, reason=reason
        )

    def complete_gap_job(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        return _job_queue.complete_gap_job(
            self.db, job_id=job_id, tenant_id=tenant_id
        )

    def fail_gap_job(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        error_message: str,
        failure_kind: OpenAIFailureKind = OpenAIFailureKind.UNKNOWN,
        retry_after_seconds: float | None = None,
    ) -> bool:
        return _job_queue.fail_gap_job(
            self.db,
            job_id=job_id,
            tenant_id=tenant_id,
            error_message=error_message,
            failure_kind=failure_kind,
            retry_after_seconds=retry_after_seconds,
        )

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> GapJobEnqueueResult:
        return _job_queue.enqueue_recalculation(self.db, tenant_id, mode)

    # --- summary ---

    def get_gap_summary(self, *, tenant_id: UUID) -> GapSummaryResponse:
        return _summary.get_gap_summary(self.db, tenant_id=tenant_id)
