"""Persistence seams and repository implementation for Gap Analyzer.

The implementation is decomposed into focused submodules under _repo/:
  _repo/records.py        — public dataclass records
  _repo/capabilities.py   — dialect capabilities + enum helpers
  _repo/bm25_cache.py     — thread-safe BM25 LRU/TTL cache
  _repo/signals.py        — signal ingestion/query ops
  _repo/mode_a_queries.py — Mode A corpus/topic ops
  _repo/mode_b_queries.py — Mode B question/cluster + vector/BM25 ops
  _repo/job_queue.py      — job queue ops and helpers
  _repo/summary.py        — gap summary aggregation

This file re-exports all public symbols for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer._repo.bm25_cache import (
    invalidate_bm25_cache_for_tenant,  # noqa: F401 — public, re-exported for callers
)
from backend.gap_analyzer._repo.job_queue import (
    _GAP_JOB_LAST_ERROR_MAX_CHARS,  # noqa: F401 — re-export for test backward-compat
    _JobQueueOps,
)
from backend.gap_analyzer._repo.mode_a_queries import _ModeAQueriesOps
from backend.gap_analyzer._repo.mode_b_queries import _ModeBQueriesOps
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
from backend.gap_analyzer._repo.signals import _SignalsOps
from backend.gap_analyzer._repo.summary import _SummaryOps
from backend.gap_analyzer.enums import GapClusterStatus, GapJobKind
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.prompts import ModeATopicCandidate
from backend.gap_analyzer.schemas import GapRunMode, GapSummaryResponse


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
    """Command-side persistence implementation for Gap Analyzer.

    Delegates to focused ops objects; each handles one slice of the domain.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._signals = _SignalsOps(db)
        self._mode_a = _ModeAQueriesOps(db)
        self._mode_b = _ModeBQueriesOps(db)
        self._jobs = _JobQueueOps(db)
        self._summary_ops = _SummaryOps(db)

    # --- signals ---

    def store_signal(self, signal: GapSignal, *, signal_weight: float) -> None:
        return self._signals.store_signal(signal, signal_weight=signal_weight)

    def get_signal_state_for_assistant_message(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
    ) -> StoredGapSignalState | None:
        return self._signals.get_signal_state_for_assistant_message(
            tenant_id=tenant_id,
            assistant_message_id=assistant_message_id,
        )

    def update_signal_weight(
        self,
        *,
        gap_question_id: UUID,
        signal_weight: float,
    ) -> None:
        return self._signals.update_signal_weight(
            gap_question_id=gap_question_id,
            signal_weight=signal_weight,
        )

    # --- mode A ---

    def get_client_openai_key(self, tenant_id: UUID) -> str | None:
        return self._mode_a.get_client_openai_key(tenant_id)

    def get_latest_mode_a_hash(self, tenant_id: UUID) -> str | None:
        return self._mode_a.get_latest_mode_a_hash(tenant_id)

    def get_mode_a_corpus_chunks(
        self,
        *,
        tenant_id: UUID,
        excluded_file_types: tuple[str, ...],
    ) -> list[ModeACorpusChunk]:
        return self._mode_a.get_mode_a_corpus_chunks(
            tenant_id=tenant_id,
            excluded_file_types=excluded_file_types,
        )

    def list_mode_a_dismissals(self, tenant_id: UUID) -> list[ModeADismissalRecord]:
        return self._mode_a.list_mode_a_dismissals(tenant_id)

    def replace_mode_a_topics(
        self,
        *,
        tenant_id: UUID,
        candidates: list[ModeATopicCandidate],
        coverage_scores: dict[str, float],
        topic_embeddings: dict[str, list[float]],
        extraction_chunk_hash: str,
    ) -> None:
        return self._mode_a.replace_mode_a_topics(
            tenant_id=tenant_id,
            candidates=candidates,
            coverage_scores=coverage_scores,
            topic_embeddings=topic_embeddings,
            extraction_chunk_hash=extraction_chunk_hash,
        )

    # --- mode B ---

    def list_unclustered_mode_b_questions(self, tenant_id: UUID) -> list[ModeBQuestionRecord]:
        return self._mode_b.list_unclustered_mode_b_questions(tenant_id)

    def list_mode_b_clusters(self, tenant_id: UUID) -> list[ModeBClusterRecord]:
        return self._mode_b.list_mode_b_clusters(tenant_id)

    def vector_top_k_for_tenant(
        self,
        *,
        tenant_id: UUID,
        query_embedding: list[float],
        top_k: int,
        excluded_file_types: tuple[str, ...],
    ) -> list[TenantVectorMatch]:
        return self._mode_b.vector_top_k_for_tenant(
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
        return self._mode_b.bm25_match_for_tenant(
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
        return self._mode_b.update_mode_b_question_embedding(
            question_id=question_id,
            embedding=embedding,
        )

    def bulk_update_mode_b_question_embeddings(
        self,
        *,
        embeddings_by_question_id: dict[UUID, list[float]],
    ) -> None:
        return self._mode_b.bulk_update_mode_b_question_embeddings(
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
        return self._mode_b.create_mode_b_cluster(
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
        return self._mode_b.assign_question_to_cluster(
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
        return self._mode_b.update_mode_b_cluster(
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
        return self._jobs.enqueue_gap_job(
            tenant_id=tenant_id,
            job_kind=job_kind,
            trigger=trigger,
        )

    def claim_next_gap_job(self) -> GapJobRecord | None:
        return self._jobs.claim_next_gap_job()

    def complete_gap_job(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        return self._jobs.complete_gap_job(job_id=job_id, tenant_id=tenant_id)

    def fail_gap_job(self, *, job_id: UUID, tenant_id: UUID, error_message: str) -> bool:
        return self._jobs.fail_gap_job(job_id=job_id, tenant_id=tenant_id, error_message=error_message)

    def release_gap_job_for_retry(
        self,
        *,
        job_id: UUID,
        tenant_id: UUID,
        reason: str,
    ) -> bool:
        return self._jobs.release_gap_job_for_retry(
            job_id=job_id,
            tenant_id=tenant_id,
            reason=reason,
        )

    def refresh_gap_job_lease(self, *, job_id: UUID, tenant_id: UUID) -> bool:
        return self._jobs.refresh_gap_job_lease(job_id=job_id, tenant_id=tenant_id)

    def enqueue_recalculation(self, tenant_id: UUID, mode: GapRunMode) -> GapJobEnqueueResult:
        return self._jobs.enqueue_recalculation(tenant_id, mode)

    # --- summary ---

    def get_gap_summary(self, *, tenant_id: UUID) -> GapSummaryResponse:
        return self._summary_ops.get_gap_summary(tenant_id=tenant_id)
