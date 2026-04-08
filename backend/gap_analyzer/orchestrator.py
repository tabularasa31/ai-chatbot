"""Public orchestrator for Gap Analyzer command flows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import re
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.gap_analyzer.domain import (
    ClusteringPolicy,
    CoveragePolicy,
    DocumentScopePolicy,
    DraftGenerationPolicy,
    GapLifecyclePolicy,
    SignalWeightPolicy,
)
from backend.gap_analyzer.enums import GapClusterStatus, GapCommandStatus, GapDismissReason, GapDocTopicStatus, GapRunMode, GapSource
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.prompts import ModeATopicCandidate, embed_texts, extract_mode_a_candidates
from backend.gap_analyzer.repository import (
    GapAnalyzerRepository,
    ModeACorpusChunk,
    ModeADismissalRecord,
    ModeBClusterRecord,
    ModeBQuestionRecord,
    SqlAlchemyGapAnalyzerRepository,
)
from backend.gap_analyzer.schemas import (
    GapActionResponse,
    GapAnalyzerResponse,
    GapDraftResponse,
    GapItemResponse,
    GapSummaryResponse,
    ModeAResult,
    ModeASort,
    ModeAStatusFilter,
    ModeBResult,
    ModeBSort,
    ModeBStatusFilter,
    RecalculateCommandResult,
)
from backend.models import GapCluster, GapDismissal, GapDocTopic, GapQuestion

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")
_MODE_A_DRAFT_EXAMPLE_LIMIT = 5
_MODE_B_WEEKLY_RECLUSTER_DAYS = 30
_BULK_ID_BATCH_SIZE = 1000


@dataclass(frozen=True)
class _PreparedCorpusChunk:
    tokens: set[str]
    vector: list[float] | None
    vector_norm: float


@dataclass(frozen=True)
class _PreparedDismissal:
    normalized_label: str
    vector: list[float] | None
    vector_norm: float


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


class GapResourceNotFoundError(ValueError):
    """Raised when a requested gap resource does not exist for the tenant."""


class GapAnalyzerOrchestrator:
    """Command routing and bounded Gap Analyzer behavior."""

    def __init__(self, repository: GapAnalyzerRepository | None = None) -> None:
        self.repository = repository

    def ingest_signal(self, signal: GapSignal) -> None:
        repository = self._require_repository()
        repository.store_signal(
            signal,
            signal_weight=self._resolve_signal_weight(signal),
        )

    def run_mode_a(self, tenant_id: UUID) -> ModeAResult:
        repository = self._require_repository()
        started_at = datetime.now(timezone.utc)

        corpus_chunks = repository.get_mode_a_corpus_chunks(
            tenant_id=tenant_id,
            excluded_file_types=DocumentScopePolicy().excluded_mode_a_file_types,
        )
        sampled_chunks = _select_mode_a_sample(corpus_chunks)
        extraction_chunk_hash = _hash_sampled_chunks(sampled_chunks)
        latest_hash = repository.get_latest_mode_a_hash(tenant_id)
        if latest_hash == extraction_chunk_hash:
            return ModeAResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        encrypted_api_key = repository.get_client_openai_key(tenant_id)
        if not encrypted_api_key:
            logger.warning("gap_analyzer_mode_a_missing_openai_key tenant_id=%s", tenant_id)
            return ModeAResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        raw_candidates = extract_mode_a_candidates(
            encrypted_api_key=encrypted_api_key,
            sampled_chunks=[chunk.chunk_text for chunk in sampled_chunks],
        )
        candidates = _dedupe_candidates(raw_candidates)
        label_embeddings, coverage_embeddings = self._embed_mode_a_candidates(
            encrypted_api_key=encrypted_api_key,
            candidates=candidates,
        )
        dismissals = repository.list_mode_a_dismissals(tenant_id)
        prepared_corpus = _prepare_corpus_chunks(corpus_chunks)
        prepared_dismissals = _prepare_dismissals(dismissals)

        coverage_policy = CoveragePolicy()
        lifecycle_policy = GapLifecyclePolicy()
        coverage_scores: dict[str, float] = {}
        topic_embeddings: dict[str, list[float]] = {}
        persisted_candidates: list[ModeATopicCandidate] = []

        for candidate in candidates:
            label_embedding = label_embeddings.get(candidate.topic_label)
            label_embedding_norm = _vector_norm(label_embedding)
            if _is_dismissed_candidate(
                candidate=candidate,
                candidate_embedding=label_embedding,
                candidate_embedding_norm=label_embedding_norm,
                dismissals=prepared_dismissals,
                similarity_threshold=lifecycle_policy.dismissal_similarity,
            ):
                continue

            coverage_query = _build_coverage_query(candidate)
            coverage_score = _compute_mode_a_coverage(
                query_text=coverage_query,
                query_embedding=coverage_embeddings.get(candidate.topic_label),
                corpus_chunks=prepared_corpus,
            )
            if coverage_score >= coverage_policy.mode_a_gate:
                continue

            coverage_scores[candidate.topic_label] = coverage_score
            if label_embedding is not None:
                topic_embeddings[candidate.topic_label] = label_embedding
            persisted_candidates.append(candidate)

        repository.replace_mode_a_topics(
            tenant_id=tenant_id,
            candidates=persisted_candidates,
            coverage_scores=coverage_scores,
            topic_embeddings=topic_embeddings,
            extraction_chunk_hash=extraction_chunk_hash,
        )
        _sync_mode_links(self._require_sqlalchemy_repository().db, tenant_id=tenant_id)
        return ModeAResult(
            tenant_id=tenant_id,
            status=GapCommandStatus.accepted,
            started_at=started_at,
        )

    def run_mode_b(self, tenant_id: UUID) -> ModeBResult:
        repository = self._require_repository()
        started_at = datetime.now(timezone.utc)

        questions = repository.list_unclustered_mode_b_questions(tenant_id)
        if not questions:
            return ModeBResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        missing_embeddings = any(_vector_from_unknown(question.embedding) is None for question in questions)
        encrypted_api_key = repository.get_client_openai_key(tenant_id) if missing_embeddings else None
        if missing_embeddings and not encrypted_api_key:
            logger.warning("gap_analyzer_mode_b_missing_openai_key tenant_id=%s", tenant_id)
            return ModeBResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        if encrypted_api_key:
            self._ensure_mode_b_question_embeddings(
                encrypted_api_key=encrypted_api_key,
                questions=questions,
            )
            refreshed_questions = repository.list_unclustered_mode_b_questions(tenant_id)
            if not refreshed_questions:
                return ModeBResult(
                    tenant_id=tenant_id,
                    status=GapCommandStatus.accepted,
                    started_at=started_at,
                )
        else:
            refreshed_questions = questions

        corpus_chunks = repository.get_mode_a_corpus_chunks(
            tenant_id=tenant_id,
            excluded_file_types=DocumentScopePolicy().excluded_mode_a_file_types,
        )
        prepared_corpus = _prepare_corpus_chunks(corpus_chunks)
        clusters = _prepare_mode_b_clusters(repository.list_mode_b_clusters(tenant_id))
        _apply_mode_b_questions_to_clusters(
            repository=repository,
            tenant_id=tenant_id,
            questions=refreshed_questions,
            prepared_corpus=prepared_corpus,
            clusters=clusters,
            started_at=started_at,
            new_cluster_is_new=True,
        )

        _sync_mode_links(self._require_sqlalchemy_repository().db, tenant_id=tenant_id)
        return ModeBResult(
            tenant_id=tenant_id,
            status=GapCommandStatus.accepted,
            started_at=started_at,
        )

    def run_mode_b_weekly_reclustering(self, tenant_id: UUID) -> ModeBResult:
        repository = self._require_repository()
        db = self._require_sqlalchemy_repository().db
        started_at = datetime.now(timezone.utc)
        recent_cutoff = (started_at - timedelta(days=_MODE_B_WEEKLY_RECLUSTER_DAYS)).replace(tzinfo=None)

        affected_cluster_ids = [
            cluster_id
            for (cluster_id,) in (
                db.query(GapQuestion.cluster_id)
                .join(GapCluster, GapQuestion.cluster_id == GapCluster.id)
                .filter(GapQuestion.tenant_id == tenant_id)
                .filter(GapQuestion.created_at >= recent_cutoff)
                .filter(GapQuestion.cluster_id.isnot(None))
                .filter(GapCluster.status.in_([GapClusterStatus.active, GapClusterStatus.closed]))
                .distinct()
                .all()
            )
            if cluster_id is not None
        ]

        scope_query = (
            db.query(GapQuestion)
            .filter(GapQuestion.tenant_id == tenant_id)
            .order_by(GapQuestion.created_at.asc(), GapQuestion.id.asc())
        )
        if affected_cluster_ids:
            scope_query = scope_query.filter(
                or_(
                    GapQuestion.cluster_id.in_(affected_cluster_ids),
                    (GapQuestion.cluster_id.is_(None) & (GapQuestion.created_at >= recent_cutoff)),
                )
            )
        else:
            scope_query = scope_query.filter(GapQuestion.cluster_id.is_(None), GapQuestion.created_at >= recent_cutoff)

        question_rows = scope_query.all()
        if not question_rows:
            return ModeBResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        questions = [_mode_b_question_record_from_row(row) for row in question_rows]
        missing_embeddings = any(_vector_from_unknown(question.embedding) is None for question in questions)
        encrypted_api_key = repository.get_client_openai_key(tenant_id) if missing_embeddings else None
        if missing_embeddings and not encrypted_api_key:
            logger.warning("gap_analyzer_mode_b_weekly_recluster_missing_openai_key tenant_id=%s", tenant_id)
            return ModeBResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        if encrypted_api_key:
            self._ensure_mode_b_question_embeddings(
                encrypted_api_key=encrypted_api_key,
                questions=questions,
            )
            reloaded_rows: list[GapQuestion] = []
            for question_id_batch in _batched(
                [question.question_id for question in questions],
                _BULK_ID_BATCH_SIZE,
            ):
                reloaded_rows.extend(
                    db.query(GapQuestion)
                    .filter(GapQuestion.id.in_(question_id_batch))
                    .order_by(GapQuestion.created_at.asc(), GapQuestion.id.asc())
                    .all()
                )
            question_rows = sorted(
                reloaded_rows,
                key=lambda row: (row.created_at, row.id),
            )
            if not question_rows:
                return ModeBResult(
                    tenant_id=tenant_id,
                    status=GapCommandStatus.accepted,
                    started_at=started_at,
                )
            questions = [_mode_b_question_record_from_row(row) for row in question_rows]

        protected_cluster_ids = {
            row.cluster_id
            for row in question_rows
            if row.cluster_id is not None and _vector_from_unknown(row.embedding) is None
        }
        question_cluster_ids = {row.id: row.cluster_id for row in question_rows}
        if protected_cluster_ids:
            logger.warning(
                "gap_analyzer_mode_b_weekly_recluster_skipping_clusters_without_embeddings tenant_id=%s cluster_count=%s",
                tenant_id,
                len(protected_cluster_ids),
            )

        corpus_chunks = repository.get_mode_a_corpus_chunks(
            tenant_id=tenant_id,
            excluded_file_types=DocumentScopePolicy().excluded_mode_a_file_types,
        )
        prepared_corpus = _prepare_corpus_chunks(corpus_chunks)
        rebuild_cluster_ids = [cluster_id for cluster_id in affected_cluster_ids if cluster_id not in protected_cluster_ids]
        questions = [
            question
            for question in questions
            if _vector_from_unknown(question.embedding) is not None
            and (
                question_cluster_ids.get(question.question_id) is None
                or question_cluster_ids.get(question.question_id) in rebuild_cluster_ids
            )
        ]
        scoped_question_ids = [question.question_id for question in questions]

        if not scoped_question_ids and not rebuild_cluster_ids:
            return ModeBResult(
                tenant_id=tenant_id,
                status=GapCommandStatus.accepted,
                started_at=started_at,
            )

        if scoped_question_ids:
            for question_id_batch in _batched(scoped_question_ids, _BULK_ID_BATCH_SIZE):
                (
                    db.query(GapQuestion)
                    .filter(GapQuestion.id.in_(question_id_batch))
                    .update({GapQuestion.cluster_id: None}, synchronize_session=False)
                )
            db.flush()

        if rebuild_cluster_ids:
            (
                db.query(GapDocTopic)
                .filter(GapDocTopic.linked_cluster_id.in_(rebuild_cluster_ids))
                .update({GapDocTopic.linked_cluster_id: None}, synchronize_session=False)
            )
            (
                db.query(GapCluster)
                .filter(GapCluster.id.in_(rebuild_cluster_ids))
                .delete(synchronize_session=False)
            )
            db.flush()
            db.expire_all()

        clusters = _prepare_mode_b_clusters(repository.list_mode_b_clusters(tenant_id))
        _apply_mode_b_questions_to_clusters(
            repository=repository,
            tenant_id=tenant_id,
            questions=questions,
            prepared_corpus=prepared_corpus,
            clusters=clusters,
            started_at=started_at,
            new_cluster_is_new=False,
        )

        _sync_mode_links(db, tenant_id=tenant_id)
        return ModeBResult(
            tenant_id=tenant_id,
            status=GapCommandStatus.accepted,
            started_at=started_at,
        )

    def record_assistant_feedback(
        self,
        *,
        tenant_id: UUID,
        assistant_message_id: UUID,
        feedback_value: str,
    ) -> bool:
        if feedback_value not in {"up", "down", "none"}:
            return False

        repository = self._require_repository()
        signal_state = repository.get_signal_state_for_assistant_message(
            tenant_id=tenant_id,
            assistant_message_id=assistant_message_id,
        )
        if signal_state is None:
            return False

        repository.update_signal_weight(
            gap_question_id=signal_state.gap_question_id,
            signal_weight=self._resolve_signal_weight_from_values(
                answer_confidence=signal_state.answer_confidence,
                had_fallback=signal_state.had_fallback,
                was_rejected=signal_state.had_rejected,
                was_escalated=signal_state.had_escalation,
                user_thumbed_down=feedback_value == "down",
            ),
        )
        return True

    def list_gaps(
        self,
        *,
        tenant_id: UUID,
        mode_a_status: ModeAStatusFilter = "active",
        mode_b_status: ModeBStatusFilter = "active",
        mode_a_sort: ModeASort = "coverage_asc",
        mode_b_sort: ModeBSort = "signal_desc",
    ) -> GapAnalyzerResponse:
        db = self._require_sqlalchemy_repository().db
        suppressed_active_mode_a_ids = _active_mode_a_ids_suppressed_by_mode_b(
            db=db,
            tenant_id=tenant_id,
            mode_b_status=mode_b_status,
        )

        mode_a_items = _build_mode_a_items(
            db=db,
            tenant_id=tenant_id,
            status_filter=mode_a_status,
            sort=mode_a_sort,
            suppressed_active_topic_ids=suppressed_active_mode_a_ids,
        )
        mode_b_items = _build_mode_b_items(
            db=db,
            tenant_id=tenant_id,
            status_filter=mode_b_status,
            sort=mode_b_sort,
        )
        return GapAnalyzerResponse(
            summary=_build_gap_summary(mode_a_items=mode_a_items, mode_b_items=mode_b_items),
            mode_a_items=mode_a_items,
            mode_b_items=mode_b_items,
        )

    def dismiss_gap(
        self,
        *,
        tenant_id: UUID,
        source: GapSource,
        gap_id: UUID,
        dismissed_by: UUID,
        reason: GapDismissReason,
    ) -> GapActionResponse:
        db = self._require_sqlalchemy_repository().db
        if source == GapSource.mode_a:
            topic = (
                db.query(GapDocTopic)
                .filter(GapDocTopic.id == gap_id, GapDocTopic.tenant_id == tenant_id)
                .first()
            )
            if topic is None:
                raise GapResourceNotFoundError("Gap topic not found")
            existing = (
                db.query(GapDismissal)
                .filter(
                    GapDismissal.tenant_id == tenant_id,
                    GapDismissal.source == GapSource.mode_a,
                    GapDismissal.gap_id == gap_id,
                )
                .first()
            )
            if existing is None:
                db.add(
                    GapDismissal(
                        tenant_id=tenant_id,
                        source=GapSource.mode_a,
                        gap_id=gap_id,
                        topic_label=topic.topic_label,
                        topic_label_embedding=topic.topic_embedding,
                        reason=reason,
                        dismissed_by=dismissed_by,
                    )
                )
            return GapActionResponse(source=source, gap_id=gap_id, status="dismissed")

        cluster = (
            db.query(GapCluster)
            .filter(GapCluster.id == gap_id, GapCluster.tenant_id == tenant_id)
            .first()
        )
        if cluster is None:
            raise GapResourceNotFoundError("Gap cluster not found")
        cluster.status = GapClusterStatus.dismissed
        cluster.question_count_at_dismissal = cluster.question_count
        existing = (
            db.query(GapDismissal)
            .filter(
                GapDismissal.tenant_id == tenant_id,
                GapDismissal.source == GapSource.mode_b,
                GapDismissal.gap_id == gap_id,
            )
            .first()
        )
        if existing is None:
            db.add(
                GapDismissal(
                    tenant_id=tenant_id,
                    source=GapSource.mode_b,
                    gap_id=gap_id,
                    topic_label=cluster.label,
                    topic_label_embedding=cluster.centroid,
                    reason=reason,
                    dismissed_by=dismissed_by,
                )
            )
        db.add(cluster)
        return GapActionResponse(source=source, gap_id=gap_id, status="dismissed")

    def reactivate_gap(
        self,
        *,
        tenant_id: UUID,
        source: GapSource,
        gap_id: UUID,
    ) -> GapActionResponse:
        db = self._require_sqlalchemy_repository().db
        if source == GapSource.mode_a:
            (
                db.query(GapDismissal)
                .filter(
                    GapDismissal.tenant_id == tenant_id,
                    GapDismissal.source == GapSource.mode_a,
                    GapDismissal.gap_id == gap_id,
                )
                .delete()
            )
            return GapActionResponse(source=source, gap_id=gap_id, status="active")

        cluster = (
            db.query(GapCluster)
            .filter(GapCluster.id == gap_id, GapCluster.tenant_id == tenant_id)
            .first()
        )
        if cluster is None:
            raise GapResourceNotFoundError("Gap cluster not found")
        (
            db.query(GapDismissal)
            .filter(
                GapDismissal.tenant_id == tenant_id,
                GapDismissal.source == GapSource.mode_b,
                GapDismissal.gap_id == gap_id,
            )
            .delete()
        )
        status = _mode_b_status_from_coverage(float(cluster.coverage_score or 0.0))
        cluster.status = status
        db.add(cluster)
        return GapActionResponse(source=source, gap_id=gap_id, status=status.value)

    def build_draft(
        self,
        *,
        tenant_id: UUID,
        source: GapSource,
        gap_id: UUID,
    ) -> GapDraftResponse:
        db = self._require_sqlalchemy_repository().db
        if source == GapSource.mode_a:
            topic = (
                db.query(GapDocTopic)
                .filter(GapDocTopic.id == gap_id, GapDocTopic.tenant_id == tenant_id)
                .first()
            )
            dismissal = (
                db.query(GapDismissal)
                .filter(
                    GapDismissal.tenant_id == tenant_id,
                    GapDismissal.source == GapSource.mode_a,
                    GapDismissal.gap_id == gap_id,
                )
                .first()
            )
            label = ((topic.topic_label if topic else None) or (dismissal.topic_label if dismissal else None) or "Untitled gap").strip()
            example_questions = _clean_questions(topic.example_questions if topic and topic.example_questions else [])[
                :_MODE_A_DRAFT_EXAMPLE_LIMIT
            ]
            markdown = _build_mode_a_draft_markdown(label=label, example_questions=example_questions)
            return GapDraftResponse(source=source, gap_id=gap_id, title=label, markdown=markdown)

        cluster = (
            db.query(GapCluster)
            .filter(GapCluster.id == gap_id, GapCluster.tenant_id == tenant_id)
            .first()
        )
        if cluster is None:
            raise GapResourceNotFoundError("Gap cluster not found")
        sample_questions = _load_mode_b_question_samples(db, [cluster.id]).get(cluster.id, [])
        linked_mode_a_questions: list[str] = []
        if cluster.linked_doc_topic_id is not None and DraftGenerationPolicy().append_mode_a_example_questions:
            linked_topic = (
                db.query(GapDocTopic)
                .filter(GapDocTopic.id == cluster.linked_doc_topic_id, GapDocTopic.tenant_id == tenant_id)
                .first()
            )
            if linked_topic is not None:
                linked_mode_a_questions = _clean_questions(linked_topic.example_questions)[:_MODE_A_DRAFT_EXAMPLE_LIMIT]
        label = (cluster.label or "Untitled gap").strip()
        markdown = _build_mode_b_draft_markdown(
            label=label,
            sample_questions=sample_questions,
            linked_mode_a_questions=linked_mode_a_questions,
            coverage_score=cluster.coverage_score,
            signal_weight=cluster.aggregate_signal_weight,
        )
        return GapDraftResponse(source=source, gap_id=gap_id, title=label, markdown=markdown)

    async def request_recalculation(
        self,
        tenant_id: UUID,
        mode: GapRunMode,
    ) -> RecalculateCommandResult:
        return RecalculateCommandResult(
            tenant_id=tenant_id,
            mode=mode,
            status=GapCommandStatus.accepted,
            accepted_at=datetime.now(timezone.utc),
        )

    def _embed_mode_a_candidates(
        self,
        *,
        encrypted_api_key: str,
        candidates: list[ModeATopicCandidate],
    ) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        if not candidates:
            return {}, {}

        labels = [candidate.topic_label for candidate in candidates]
        coverage_queries = [_build_coverage_query(candidate) for candidate in candidates]
        label_vectors = embed_texts(encrypted_api_key=encrypted_api_key, texts=labels)
        coverage_vectors = embed_texts(encrypted_api_key=encrypted_api_key, texts=coverage_queries)
        return (
            {
                candidate.topic_label: label_vectors[index]
                for index, candidate in enumerate(candidates)
                if index < len(label_vectors)
            },
            {
                candidate.topic_label: coverage_vectors[index]
                for index, candidate in enumerate(candidates)
                if index < len(coverage_vectors)
            },
        )

    def _ensure_mode_b_question_embeddings(
        self,
        *,
        encrypted_api_key: str,
        questions: list[ModeBQuestionRecord],
    ) -> None:
        repository = self._require_repository()
        missing_questions = [question for question in questions if _vector_from_unknown(question.embedding) is None]
        if not missing_questions:
            return
        valid_missing_questions = [
            question
            for question in missing_questions
            if question.question_text.strip()
        ]
        if not valid_missing_questions:
            return
        vectors = embed_texts(
            encrypted_api_key=encrypted_api_key,
            texts=[question.question_text for question in valid_missing_questions],
        )
        repository.bulk_update_mode_b_question_embeddings(
            embeddings_by_question_id={
                question.question_id: vectors[index]
                for index, question in enumerate(valid_missing_questions)
                if index < len(vectors)
            }
        )

    def _require_repository(self) -> GapAnalyzerRepository:
        if self.repository is None:
            raise RuntimeError("Gap Analyzer repository is required for command execution")
        return self.repository

    def _require_sqlalchemy_repository(self) -> SqlAlchemyGapAnalyzerRepository:
        repository = self._require_repository()
        if not isinstance(repository, SqlAlchemyGapAnalyzerRepository):
            raise RuntimeError("Gap Analyzer Phase 5 read surfaces require the SQLAlchemy repository")
        return repository

    def _resolve_signal_weight(self, signal: GapSignal) -> float:
        return self._resolve_signal_weight_from_values(
            answer_confidence=signal.answer_confidence,
            had_fallback=signal.had_fallback,
            was_rejected=signal.was_rejected,
            was_escalated=signal.was_escalated,
            user_thumbed_down=signal.user_thumbed_down,
        )

    def _resolve_signal_weight_from_values(
        self,
        *,
        answer_confidence: float | None,
        had_fallback: bool,
        was_rejected: bool,
        was_escalated: bool,
        user_thumbed_down: bool,
    ) -> float:
        policy = SignalWeightPolicy()
        weight = policy.normal_weight
        if answer_confidence is not None and answer_confidence < policy.low_conf_threshold:
            weight = max(weight, policy.low_conf_weight)
        if was_rejected or had_fallback:
            weight = max(weight, policy.rejection_weight)
        if was_escalated:
            weight = max(weight, policy.escalation_weight)
        if user_thumbed_down:
            weight = max(weight, policy.thumbdown_weight)
        return weight


def _build_mode_a_items(
    *,
    db: Session,
    tenant_id: UUID,
    status_filter: ModeAStatusFilter,
    sort: ModeASort,
    suppressed_active_topic_ids: set[UUID] | None = None,
) -> list[GapItemResponse]:
    suppressed_ids = suppressed_active_topic_ids or set()
    dismissed_rows = (
        db.query(GapDismissal)
        .filter(GapDismissal.tenant_id == tenant_id, GapDismissal.source == GapSource.mode_a)
        .order_by(GapDismissal.dismissed_at.desc(), GapDismissal.id.desc())
        .all()
    )
    dismissed_by_id = {row.gap_id: row for row in dismissed_rows}
    topics = (
        db.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == tenant_id)
        .filter(GapDocTopic.topic_label.isnot(None))
        .all()
    )

    items: list[GapItemResponse] = []
    if status_filter in {"active", "all"}:
        for topic in topics:
            if topic.id in dismissed_by_id:
                continue
            if topic.status != GapDocTopicStatus.active:
                continue
            if topic.id in suppressed_ids:
                continue
            cleaned_questions = _clean_questions(topic.example_questions)
            items.append(
                GapItemResponse(
                    id=topic.id,
                    source=GapSource.mode_a,
                    label=(topic.topic_label or "Untitled gap").strip(),
                    coverage_score=topic.coverage_score,
                    classification=_classify_gap(topic.coverage_score),
                    status="active",
                    is_new=bool(topic.is_new),
                    question_count=len(cleaned_questions),
                    aggregate_signal_weight=None,
                    example_questions=cleaned_questions,
                    linked_source=None,
                    also_missing_in_docs=False,
                    last_updated=topic.extracted_at,
                )
            )
    if status_filter in {"dismissed", "archived", "all"}:
        topics_by_id = {topic.id: topic for topic in topics}
        for dismissal in dismissed_rows:
            topic = topics_by_id.get(dismissal.gap_id)
            cleaned_questions = _clean_questions(topic.example_questions if topic is not None else None)
            items.append(
                GapItemResponse(
                    id=dismissal.gap_id,
                    source=GapSource.mode_a,
                    label=((dismissal.topic_label or (topic.topic_label if topic else None)) or "Untitled gap").strip(),
                    coverage_score=topic.coverage_score if topic is not None else None,
                    classification=_classify_gap(topic.coverage_score if topic is not None else None),
                    status="dismissed",
                    is_new=False,
                    question_count=len(cleaned_questions),
                    aggregate_signal_weight=None,
                    example_questions=cleaned_questions,
                    linked_source=None,
                    also_missing_in_docs=False,
                    last_updated=dismissal.dismissed_at,
                )
            )

    if sort == "newest":
        items.sort(key=lambda item: (item.last_updated or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    else:
        items.sort(key=lambda item: (_sort_float(item.coverage_score, default=999.0), item.label.casefold()))
    return items


def _build_mode_b_items(
    *,
    db: Session,
    tenant_id: UUID,
    status_filter: ModeBStatusFilter,
    sort: ModeBSort,
) -> list[GapItemResponse]:
    allowed_statuses: tuple[GapClusterStatus, ...]
    if status_filter == "active":
        allowed_statuses = (GapClusterStatus.active,)
    elif status_filter == "archived":
        allowed_statuses = (GapClusterStatus.closed, GapClusterStatus.dismissed)
    elif status_filter == "closed":
        allowed_statuses = (GapClusterStatus.closed,)
    elif status_filter == "dismissed":
        allowed_statuses = (GapClusterStatus.dismissed,)
    else:
        allowed_statuses = (GapClusterStatus.active, GapClusterStatus.closed, GapClusterStatus.dismissed)

    clusters = (
        db.query(GapCluster)
        .filter(GapCluster.tenant_id == tenant_id)
        .filter(GapCluster.status.in_([status.value for status in allowed_statuses]))
        .filter(GapCluster.label.isnot(None))
        .all()
    )
    sample_questions = _load_mode_b_question_samples(db, [cluster.id for cluster in clusters])

    items = [
        GapItemResponse(
            id=cluster.id,
            source=GapSource.mode_b,
            label=(cluster.label or "Untitled gap").strip(),
            coverage_score=cluster.coverage_score,
            classification=_classify_gap(cluster.coverage_score),
            status=_cluster_status_value(cluster.status),
            is_new=bool(cluster.is_new),
            question_count=int(cluster.question_count or 0),
            aggregate_signal_weight=float(cluster.aggregate_signal_weight or 0.0),
            example_questions=sample_questions.get(cluster.id, []),
            linked_source=GapSource.mode_a if cluster.linked_doc_topic_id is not None else None,
            also_missing_in_docs=cluster.linked_doc_topic_id is not None,
            last_updated=cluster.last_computed_at or cluster.last_question_at or cluster.created_at,
        )
        for cluster in clusters
    ]

    if sort == "coverage_asc":
        items.sort(key=lambda item: (_sort_float(item.coverage_score, default=999.0), item.label.casefold()))
    elif sort == "newest":
        items.sort(key=lambda item: (item.last_updated or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    else:
        items.sort(
            key=lambda item: (
                -float(item.aggregate_signal_weight or 0.0),
                _sort_float(item.coverage_score, default=999.0),
                item.label.casefold(),
            )
        )
    return items


def _load_mode_b_question_samples(db: Session, cluster_ids: Iterable[UUID]) -> dict[UUID, list[str]]:
    ids = [cluster_id for cluster_id in cluster_ids if cluster_id is not None]
    if not ids:
        return {}
    ranked_rows = (
        db.query(
            GapQuestion.cluster_id.label("cluster_id"),
            GapQuestion.question_text.label("question_text"),
            func.row_number()
            .over(
                partition_by=GapQuestion.cluster_id,
                order_by=(GapQuestion.created_at.desc(), GapQuestion.id.desc()),
            )
            .label("row_number"),
        )
        .filter(GapQuestion.cluster_id.in_(ids))
        .subquery()
    )
    rows = (
        db.query(ranked_rows.c.cluster_id, ranked_rows.c.question_text)
        .filter(ranked_rows.c.row_number <= 3)
        .order_by(ranked_rows.c.cluster_id.asc(), ranked_rows.c.row_number.asc())
        .all()
    )
    grouped: dict[UUID, list[str]] = defaultdict(list)
    for cluster_id, question_text in rows:
        if cluster_id is None:
            continue
        cleaned = question_text.strip()
        if not cleaned:
            continue
        grouped[cluster_id].append(cleaned)
    return dict(grouped)


def _mode_b_question_record_from_row(row: GapQuestion) -> ModeBQuestionRecord:
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return ModeBQuestionRecord(
        question_id=row.id,
        question_text=row.question_text,
        embedding=row.embedding,
        gap_signal_weight=float(row.gap_signal_weight or 0.0),
        language=row.language,
        created_at=created_at,
    )


def _apply_mode_b_questions_to_clusters(
    *,
    repository: GapAnalyzerRepository,
    tenant_id: UUID,
    questions: Iterable[ModeBQuestionRecord],
    prepared_corpus: list[_PreparedCorpusChunk],
    clusters: list[_MutableModeBCluster],
    started_at: datetime,
    new_cluster_is_new: bool,
) -> None:
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
                question=question,
                question_embedding=question_embedding,
                question_norm=question_norm,
                corpus_chunks=prepared_corpus,
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
                corpus_chunks=prepared_corpus,
            )
        except _ModeBClusterUpdateRejectedError:
            new_cluster = _build_new_mode_b_cluster(
                question=question,
                question_embedding=question_embedding,
                question_norm=question_norm,
                corpus_chunks=prepared_corpus,
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
        repository.update_mode_b_cluster(
            cluster_id=target_cluster.cluster_id,
            centroid=target_cluster.centroid,
            question_count=target_cluster.question_count,
            aggregate_signal_weight=target_cluster.aggregate_signal_weight,
            coverage_score=target_cluster.coverage_score,
            status=target_cluster.status,
            last_question_at=target_cluster.last_question_at,
            last_computed_at=started_at,
        )


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


def _batched(values: list[UUID], batch_size: int) -> Iterable[list[UUID]]:
    for index in range(0, len(values), batch_size):
        yield values[index : index + batch_size]


def _build_gap_summary(
    *,
    mode_a_items: list[GapItemResponse],
    mode_b_items: list[GapItemResponse],
) -> GapSummaryResponse:
    uncovered_count = 0
    partial_count = 0
    total_active = 0
    new_badge_count = 0
    last_updated: datetime | None = None

    for item in [*mode_a_items, *mode_b_items]:
        if item.last_updated is not None and (last_updated is None or item.last_updated > last_updated):
            last_updated = item.last_updated
        if item.status != "active":
            continue
        total_active += 1
        if item.classification == "uncovered":
            uncovered_count += 1
        elif item.classification == "partial":
            partial_count += 1
        if item.is_new:
            new_badge_count += 1

    return GapSummaryResponse(
        total_active=total_active,
        uncovered_count=uncovered_count,
        partial_count=partial_count,
        impact_statement=_impact_statement(total_active=total_active, uncovered_count=uncovered_count, partial_count=partial_count),
        new_badge_count=new_badge_count,
        last_updated=last_updated,
    )


def _active_mode_a_ids_suppressed_by_mode_b(
    *,
    db: Session,
    tenant_id: UUID,
    mode_b_status: ModeBStatusFilter,
) -> set[UUID]:
    if mode_b_status not in {"active", "all"}:
        return set()
    rows = (
        db.query(GapCluster.linked_doc_topic_id)
        .filter(GapCluster.tenant_id == tenant_id)
        .filter(GapCluster.status == GapClusterStatus.active)
        .filter(GapCluster.linked_doc_topic_id.isnot(None))
        .all()
    )
    return {linked_topic_id for (linked_topic_id,) in rows if linked_topic_id is not None}


def _sync_mode_links(db: Session, *, tenant_id: UUID) -> None:
    all_topics = (
        db.query(GapDocTopic)
        .filter(GapDocTopic.tenant_id == tenant_id)
        .all()
    )
    all_clusters = (
        db.query(GapCluster)
        .filter(GapCluster.tenant_id == tenant_id)
        .all()
    )

    for topic in all_topics:
        topic.linked_cluster_id = None
    for cluster in all_clusters:
        cluster.linked_doc_topic_id = None
    db.flush()

    topics = [topic for topic in all_topics if topic.topic_label is not None]
    clusters = [
        cluster
        for cluster in all_clusters
        if cluster.status in {GapClusterStatus.active, GapClusterStatus.closed, GapClusterStatus.dismissed}
    ]

    prepared_clusters: list[tuple[list[float], float, GapCluster]] = []
    for cluster in clusters:
        cluster_vector = _vector_from_unknown(cluster.centroid)
        if cluster_vector is None:
            continue
        prepared_clusters.append((cluster_vector, _vector_norm(cluster_vector), cluster))

    scored_pairs: list[tuple[float, GapDocTopic, GapCluster]] = []
    link_threshold = ClusteringPolicy().link_threshold
    for topic in topics:
        topic_vector = _vector_from_unknown(topic.topic_embedding)
        if topic_vector is None:
            continue
        topic_norm = _vector_norm(topic_vector)
        for cluster_vector, cluster_norm, cluster in prepared_clusters:
            if len(topic_vector) != len(cluster_vector):
                continue
            similarity = _cosine_similarity(
                topic_vector,
                cluster_vector,
                first_norm=topic_norm,
                second_norm=cluster_norm,
            )
            if similarity >= link_threshold:
                scored_pairs.append((similarity, topic, cluster))

    linked_topic_ids: set[UUID] = set()
    linked_cluster_ids: set[UUID] = set()
    for _, topic, cluster in sorted(scored_pairs, key=lambda item: item[0], reverse=True):
        if topic.id in linked_topic_ids or cluster.id in linked_cluster_ids:
            continue
        topic.linked_cluster_id = cluster.id
        cluster.linked_doc_topic_id = topic.id
        linked_topic_ids.add(topic.id)
        linked_cluster_ids.add(cluster.id)
    db.flush()


def _impact_statement(*, total_active: int, uncovered_count: int, partial_count: int) -> str:
    if total_active == 0:
        return "No active gaps detected."
    if uncovered_count > 0:
        noun = "gap" if uncovered_count == 1 else "gaps"
        return f"{uncovered_count} uncovered {noun} need attention."
    if partial_count > 0:
        noun = "gap" if partial_count == 1 else "gaps"
        return f"{partial_count} partially covered {noun} are worth reviewing."
    noun = "gap" if total_active == 1 else "gaps"
    return f"{total_active} active {noun} are being tracked."


def _classify_gap(coverage_score: float | None) -> str:
    if coverage_score is None:
        return "unknown"
    coverage_policy = CoveragePolicy()
    if coverage_score >= coverage_policy.covered_threshold:
        return "covered"
    if coverage_score >= coverage_policy.mode_b_uncovered:
        return "partial"
    return "uncovered"


def _cluster_status_value(status: GapClusterStatus | str) -> str:
    if isinstance(status, GapClusterStatus):
        return status.value
    return str(status)


def _clean_questions(raw_questions: object) -> list[str]:
    if isinstance(raw_questions, str):
        try:
            raw_questions = json.loads(raw_questions)
        except json.JSONDecodeError:
            return []
    elif (
        isinstance(raw_questions, list)
        and raw_questions
        and all(isinstance(item, str) and len(item) == 1 for item in raw_questions)
    ):
        try:
            raw_questions = json.loads("".join(raw_questions))
        except json.JSONDecodeError:
            return []
    if not isinstance(raw_questions, list):
        return []
    return [question.strip() for question in raw_questions if isinstance(question, str) and question.strip()]


def _sort_float(value: float | None, *, default: float) -> float:
    return float(value) if value is not None else default


def _build_mode_a_draft_markdown(*, label: str, example_questions: list[str]) -> str:
    lines = [
        f"# {label}",
        "",
        "## Why this matters",
        "This docs gap was detected from the current knowledge base and needs explicit coverage.",
    ]
    if example_questions:
        lines.extend(["", "## Example questions"])
        lines.extend([f"- {question}" for question in example_questions])
    lines.extend(["", "## Draft notes", "- Add a concise overview", "- Explain the main workflow", "- Link related limits, edge cases, and troubleshooting"])
    return "\n".join(lines)


def _build_mode_b_draft_markdown(
    *,
    label: str,
    sample_questions: list[str],
    linked_mode_a_questions: list[str],
    coverage_score: float | None,
    signal_weight: float | None,
) -> str:
    lines = [
        f"# {label}",
        "",
        "## User signal",
        f"- Aggregate signal weight: {signal_weight or 0.0:.1f}",
        f"- Coverage score: {coverage_score:.2f}" if coverage_score is not None else "- Coverage score: unknown",
    ]
    if sample_questions:
        lines.extend(["", "## Sample user questions"])
        lines.extend([f"- {question}" for question in sample_questions])
    if linked_mode_a_questions:
        lines.extend(["", "## Also missing in docs"])
        lines.extend([f"- {question}" for question in linked_mode_a_questions])
    lines.extend(["", "## Draft notes", "- Start from the user pain in the questions above", "- Document the exact workflow or limitation", "- Include prerequisites, examples, and common failure cases"])
    return "\n".join(lines)


def _select_mode_a_sample(
    corpus_chunks: list[ModeACorpusChunk],
    *,
    max_chunks: int = 40,
) -> list[ModeACorpusChunk]:
    if not corpus_chunks:
        return []

    grouped: dict[str, list[ModeACorpusChunk]] = defaultdict(list)
    for chunk in corpus_chunks:
        grouped[_chunk_group_key(chunk)].append(chunk)

    selected: list[ModeACorpusChunk] = []
    selected_ids: set[UUID] = set()
    for group_key in sorted(grouped):
        best_in_group = sorted(
            grouped[group_key],
            key=lambda item: (-len(item.chunk_text), str(item.chunk_id)),
        )[0]
        selected.append(best_in_group)
        selected_ids.add(best_in_group.chunk_id)
        if len(selected) >= max_chunks:
            return selected

    remaining = [
        chunk
        for group_key in sorted(grouped)
        for chunk in grouped[group_key]
        if chunk.chunk_id not in selected_ids
    ]
    remaining.sort(
        key=lambda item: (
            -len(item.chunk_text),
            _chunk_group_key(item),
            str(item.chunk_id),
        )
    )
    for chunk in remaining:
        if len(selected) >= max_chunks:
            break
        selected.append(chunk)
    return selected


def _chunk_group_key(chunk: ModeACorpusChunk) -> str:
    for value in (
        chunk.section_title,
        chunk.page_title,
        chunk.filename,
        chunk.source_url,
    ):
        if value:
            return value.casefold()
    return f"{chunk.document_id}:{chunk.file_type}".casefold()


def _hash_sampled_chunks(chunks: list[ModeACorpusChunk]) -> str:
    joined = "|".join(sorted(str(chunk.chunk_id) for chunk in chunks))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _dedupe_candidates(candidates: list[ModeATopicCandidate]) -> list[ModeATopicCandidate]:
    deduped: list[ModeATopicCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.topic_label.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(candidate)
    return deduped


def _build_coverage_query(candidate: ModeATopicCandidate) -> str:
    parts = [candidate.topic_label.strip()]
    parts.extend(question.strip() for question in candidate.example_questions if question.strip())
    return "\n".join(parts)


def _compute_mode_a_coverage(
    *,
    query_text: str,
    query_embedding: list[float] | None,
    corpus_chunks: list[_PreparedCorpusChunk],
) -> float:
    query_tokens = _tokenize(query_text)
    query_embedding_norm = _vector_norm(query_embedding)
    best_semantic = 0.0
    best_lexical = 0.0
    for chunk in corpus_chunks:
        if query_embedding is not None:
            best_semantic = max(
                best_semantic,
                _cosine_similarity(
                    query_embedding,
                    chunk.vector,
                    first_norm=query_embedding_norm,
                    second_norm=chunk.vector_norm,
                ),
            )
        if query_tokens:
            best_lexical = max(best_lexical, _token_overlap(query_tokens, chunk.tokens))
    return max(best_semantic, best_lexical)


def _is_dismissed_candidate(
    *,
    candidate: ModeATopicCandidate,
    candidate_embedding: list[float] | None,
    candidate_embedding_norm: float,
    dismissals: list[_PreparedDismissal],
    similarity_threshold: float,
) -> bool:
    candidate_label = candidate.topic_label.strip().casefold()
    for dismissal in dismissals:
        if candidate_label == dismissal.normalized_label:
            return True
        if candidate_embedding is None or dismissal.vector is None:
            continue
        if _cosine_similarity(
            candidate_embedding,
            dismissal.vector,
            first_norm=candidate_embedding_norm,
            second_norm=dismissal.vector_norm,
        ) > similarity_threshold:
            return True
    return False


def _prepare_corpus_chunks(corpus_chunks: list[ModeACorpusChunk]) -> list[_PreparedCorpusChunk]:
    prepared: list[_PreparedCorpusChunk] = []
    for chunk in corpus_chunks:
        vector = _vector_from_unknown(chunk.vector)
        prepared.append(
            _PreparedCorpusChunk(
                tokens=_tokenize(chunk.chunk_text),
                vector=vector,
                vector_norm=_vector_norm(vector),
            )
        )
    return prepared


def _prepare_dismissals(dismissals: list[ModeADismissalRecord]) -> list[_PreparedDismissal]:
    prepared: list[_PreparedDismissal] = []
    for dismissal in dismissals:
        vector = _vector_from_unknown(dismissal.topic_label_embedding)
        prepared.append(
            _PreparedDismissal(
                normalized_label=dismissal.topic_label.strip().casefold(),
                vector=vector,
                vector_norm=_vector_norm(vector),
            )
        )
    return prepared


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
                last_question_at=cluster.last_question_at or datetime.now(timezone.utc),
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
    question: ModeBQuestionRecord,
    question_embedding: list[float],
    question_norm: float,
    corpus_chunks: list[_PreparedCorpusChunk],
) -> _MutableModeBCluster:
    coverage_score = _compute_mode_a_coverage(
        query_text=question.question_text,
        query_embedding=question_embedding,
        corpus_chunks=corpus_chunks,
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
    corpus_chunks: list[_PreparedCorpusChunk],
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
    cluster.coverage_score = _compute_mode_a_coverage(
        query_text=cluster.label,
        query_embedding=cluster.centroid,
        corpus_chunks=corpus_chunks,
    )
    cluster.status = _mode_b_status_from_coverage(cluster.coverage_score)


def _mode_b_status_from_coverage(coverage_score: float) -> GapClusterStatus:
    if coverage_score >= CoveragePolicy().covered_threshold:
        return GapClusterStatus.closed
    return GapClusterStatus.active


def _vector_from_unknown(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [float(value) for value in raw]
    if isinstance(raw, tuple):
        return [float(value) for value in raw]
    if hasattr(raw, "tolist"):
        try:
            parsed_list = raw.tolist()
        except Exception:
            parsed_list = None
        if isinstance(parsed_list, list):
            return [float(value) for value in parsed_list]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, list):
            return [float(value) for value in parsed]
    return None


def _vector_norm(vector: list[float] | None) -> float:
    if vector is None:
        return 0.0
    return math.sqrt(sum(value * value for value in vector))


def _cosine_similarity(
    first: list[float] | None,
    second: list[float] | None,
    *,
    first_norm: float,
    second_norm: float,
) -> float:
    if first is None or second is None or len(first) != len(second):
        return 0.0
    if first_norm == 0.0 or second_norm == 0.0:
        return 0.0
    dot = 0.0
    for left, right in zip(first, second):
        dot += left * right
    return max(0.0, min(1.0, dot / (first_norm * second_norm)))


def _token_overlap(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _tokenize(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.casefold()) if token}
