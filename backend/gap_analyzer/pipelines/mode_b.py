"""Mode B clustering state machine helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from backend.gap_analyzer._classification import _mode_b_status_from_coverage
from backend.gap_analyzer._math import _cosine_similarity, _vector_from_unknown, _vector_norm
from backend.gap_analyzer.domain import ClusteringPolicy
from backend.gap_analyzer.enums import GapClusterStatus
from backend.gap_analyzer.pipelines.mode_a import _compute_gap_coverage
from backend.gap_analyzer.repository import (
    GapAnalyzerRepository,
    ModeBClusterRecord,
    ModeBQuestionRecord,
)

logger = logging.getLogger(__name__)


@dataclass
class _MutableModeBCluster:
    cluster_id: UUID
    label: str
    centroid: list[float]
    centroid_norm: float
    question_count: int
    aggregate_signal_weight: float
    coverage_score: float
    status: GapClusterStatus
    last_question_at: datetime

    def __post_init__(self) -> None:
        # Keep mutable cluster state internally consistent even if callers pass a stale norm.
        self.centroid_norm = _vector_norm(self.centroid)


class _ModeBClusterUpdateRejectedError(RuntimeError):
    def __init__(
        self,
        *,
        cluster_id: UUID,
        question_id: UUID,
        centroid_len: int,
        question_len: int,
    ) -> None:
        super().__init__(
            "Mode B cluster update rejected due to vector length mismatch: "
            f"cluster_id={cluster_id} centroid_len={centroid_len} "
            f"question_id={question_id} question_len={question_len}"
        )
        self.cluster_id = cluster_id
        self.question_id = question_id
        self.centroid_len = centroid_len
        self.question_len = question_len


def _prepare_mode_b_clusters(clusters: list[ModeBClusterRecord]) -> list[_MutableModeBCluster]:
    prepared: list[_MutableModeBCluster] = []
    for cluster in clusters:
        centroid = _vector_from_unknown(cluster.centroid)
        if centroid is None:
            continue
        try:
            status = GapClusterStatus(cluster.status)
        except ValueError:
            logger.warning(
                "gap_analyzer_mode_b_invalid_cluster_status cluster_id=%s status=%s",
                cluster.cluster_id,
                cluster.status,
            )
            continue
        prepared.append(
            _MutableModeBCluster(
                cluster_id=cluster.cluster_id,
                label=(cluster.label or "").strip() or "Untitled gap",
                centroid=centroid,
                centroid_norm=_vector_norm(centroid),
                question_count=cluster.question_count,
                aggregate_signal_weight=cluster.aggregate_signal_weight,
                coverage_score=cluster.coverage_score or 0.0,
                status=status,
                last_question_at=cluster.last_question_at or datetime.now(UTC),
            )
        )
    return prepared


def _match_mode_b_cluster(
    *,
    question_embedding: list[float],
    question_norm: float,
    clusters: list[_MutableModeBCluster],
    similarity_threshold: float,
) -> _MutableModeBCluster | None:
    best_cluster: _MutableModeBCluster | None = None
    best_similarity = 0.0
    for cluster in clusters:
        if cluster.status in {GapClusterStatus.dismissed, GapClusterStatus.inactive}:
            continue
        similarity = _cosine_similarity(
            question_embedding,
            cluster.centroid,
            first_norm=question_norm,
            second_norm=cluster.centroid_norm,
        )
        if similarity >= similarity_threshold and similarity > best_similarity:
            best_similarity = similarity
            best_cluster = cluster
    return best_cluster


def _build_new_mode_b_cluster(
    *,
    repository: GapAnalyzerRepository,
    tenant_id: UUID,
    question: ModeBQuestionRecord,
    question_embedding: list[float],
    question_norm: float,
) -> _MutableModeBCluster:
    coverage_score = _compute_gap_coverage(
        repository=repository,
        tenant_id=tenant_id,
        query_text=question.question_text,
        query_embedding=question_embedding,
    )
    status = _mode_b_status_from_coverage(coverage_score)
    return _MutableModeBCluster(
        cluster_id=UUID(int=0),
        label=question.question_text.strip(),
        centroid=question_embedding,
        centroid_norm=question_norm,
        question_count=1,
        aggregate_signal_weight=question.gap_signal_weight,
        coverage_score=coverage_score,
        status=status,
        last_question_at=question.created_at,
    )


def _update_mode_b_cluster(
    *,
    cluster: _MutableModeBCluster,
    question: ModeBQuestionRecord,
    question_embedding: list[float],
) -> None:
    if len(cluster.centroid) != len(question_embedding):
        logger.warning(
            "gap_analyzer_mode_b_centroid_length_mismatch_falling_back_to_new_cluster cluster_id=%s centroid_len=%s question_id=%s question_len=%s",
            cluster.cluster_id,
            len(cluster.centroid),
            question.question_id,
            len(question_embedding),
        )
        raise _ModeBClusterUpdateRejectedError(
            cluster_id=cluster.cluster_id,
            question_id=question.question_id,
            centroid_len=len(cluster.centroid),
            question_len=len(question_embedding),
        )
    previous_count = cluster.question_count
    cluster.question_count += 1
    cluster.aggregate_signal_weight += question.gap_signal_weight
    cluster.last_question_at = max(cluster.last_question_at, question.created_at)
    cluster.centroid = [
        ((cluster.centroid[index] * previous_count) + question_embedding[index]) / cluster.question_count
        for index in range(len(cluster.centroid))
    ]
    cluster.centroid_norm = _vector_norm(cluster.centroid)


def _finalize_mode_b_cluster(
    *,
    repository: GapAnalyzerRepository,
    tenant_id: UUID,
    cluster: _MutableModeBCluster,
) -> None:
    cluster.coverage_score = _compute_gap_coverage(
        repository=repository,
        tenant_id=tenant_id,
        query_text=cluster.label,
        query_embedding=cluster.centroid,
    )
    cluster.status = _mode_b_status_from_coverage(cluster.coverage_score)


def _persist_new_mode_b_cluster(
    *,
    repository: GapAnalyzerRepository,
    tenant_id: UUID,
    question: ModeBQuestionRecord,
    cluster: _MutableModeBCluster,
    clusters: list[_MutableModeBCluster],
    started_at: datetime,
    is_new: bool,
) -> None:
    cluster_id = repository.create_mode_b_cluster(
        tenant_id=tenant_id,
        label=cluster.label,
        centroid=cluster.centroid,
        question_count=cluster.question_count,
        aggregate_signal_weight=cluster.aggregate_signal_weight,
        coverage_score=cluster.coverage_score,
        status=cluster.status,
        is_new=is_new,
        last_question_at=cluster.last_question_at,
        last_computed_at=started_at,
    )
    repository.assign_question_to_cluster(question_id=question.question_id, cluster_id=cluster_id)
    cluster.cluster_id = cluster_id
    clusters.append(cluster)


def _apply_mode_b_questions_to_clusters(
    *,
    repository: GapAnalyzerRepository,
    tenant_id: UUID,
    questions: list[ModeBQuestionRecord],
    clusters: list[_MutableModeBCluster],
    started_at: datetime,
    new_cluster_is_new: bool,
) -> None:
    dirty_clusters: dict[UUID, _MutableModeBCluster] = {}
    for question in questions:
        question_embedding = _vector_from_unknown(question.embedding)
        if question_embedding is None:
            continue
        question_norm = _vector_norm(question_embedding)
        target_cluster = _match_mode_b_cluster(
            question_embedding=question_embedding,
            question_norm=question_norm,
            clusters=clusters,
            similarity_threshold=ClusteringPolicy().similarity_threshold,
        )
        if target_cluster is None:
            new_cluster = _build_new_mode_b_cluster(
                repository=repository,
                tenant_id=tenant_id,
                question=question,
                question_embedding=question_embedding,
                question_norm=question_norm,
            )
            _persist_new_mode_b_cluster(
                repository=repository,
                tenant_id=tenant_id,
                question=question,
                cluster=new_cluster,
                clusters=clusters,
                started_at=started_at,
                is_new=new_cluster_is_new,
            )
            continue

        try:
            _update_mode_b_cluster(
                cluster=target_cluster,
                question=question,
                question_embedding=question_embedding,
            )
        except _ModeBClusterUpdateRejectedError:
            new_cluster = _build_new_mode_b_cluster(
                repository=repository,
                tenant_id=tenant_id,
                question=question,
                question_embedding=question_embedding,
                question_norm=question_norm,
            )
            _persist_new_mode_b_cluster(
                repository=repository,
                tenant_id=tenant_id,
                question=question,
                cluster=new_cluster,
                clusters=clusters,
                started_at=started_at,
                is_new=new_cluster_is_new,
            )
            continue

        repository.assign_question_to_cluster(
            question_id=question.question_id,
            cluster_id=target_cluster.cluster_id,
        )
        dirty_clusters[target_cluster.cluster_id] = target_cluster

    for cluster in dirty_clusters.values():
        _finalize_mode_b_cluster(
            repository=repository,
            tenant_id=tenant_id,
            cluster=cluster,
        )
        repository.update_mode_b_cluster(
            cluster_id=cluster.cluster_id,
            centroid=cluster.centroid,
            question_count=cluster.question_count,
            aggregate_signal_weight=cluster.aggregate_signal_weight,
            coverage_score=cluster.coverage_score,
            status=cluster.status,
            last_question_at=cluster.last_question_at,
            last_computed_at=started_at,
        )
