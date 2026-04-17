"""Public orchestrator for Gap Analyzer command flows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_

from backend.gap_analyzer._classification import (
    _mode_b_status_from_coverage,
)
from backend.gap_analyzer._math import (
    _tokenize as _tokenize,
)
from backend.gap_analyzer._math import _vector_from_unknown, _vector_norm
from backend.gap_analyzer.domain import (
    CoveragePolicy,
    DocumentScopePolicy,
    DraftGenerationPolicy,
    GapLifecyclePolicy,
    SignalWeightPolicy,
)
from backend.gap_analyzer.enums import (
    GapClusterStatus,
    GapCommandStatus,
    GapDismissReason,
    GapRunMode,
    GapSource,
)
from backend.gap_analyzer.events import GapSignal
from backend.gap_analyzer.pipelines.drafts import (
    _build_mode_a_draft_markdown,
    _build_mode_b_draft_markdown,
)
from backend.gap_analyzer.pipelines.link_sync import (
    _active_mode_a_ids_suppressed_by_mode_b,
    _sync_mode_links,
)
from backend.gap_analyzer.pipelines.mode_a import (
    _batched,
    _build_coverage_query,
    _compute_gap_coverage,
    _dedupe_candidates,
    _hash_sampled_chunks,
    _is_dismissed_candidate,
    _prepare_dismissals,
    _select_mode_a_sample,
)
from backend.gap_analyzer.pipelines.mode_b import (
    _apply_mode_b_questions_to_clusters,
    _prepare_mode_b_clusters,
)
from backend.gap_analyzer.pipelines.mode_b import (
    _ModeBClusterUpdateRejectedError as _ModeBClusterUpdateRejectedError,
)
from backend.gap_analyzer.pipelines.mode_b import (
    _MutableModeBCluster as _MutableModeBCluster,
)
from backend.gap_analyzer.pipelines.mode_b import (
    _update_mode_b_cluster as _update_mode_b_cluster,
)
from backend.gap_analyzer.prompts import ModeATopicCandidate, embed_texts, extract_mode_a_candidates
from backend.gap_analyzer.read_models import (
    _build_gap_summary,
    _build_mode_a_items,
    _build_mode_b_items,
    _clean_questions,
    _load_mode_b_question_samples,
    _mode_b_question_record_from_row,
)
from backend.gap_analyzer.repository import (
    GapAnalyzerRepository,
    ModeBQuestionRecord,
    SqlAlchemyGapAnalyzerRepository,
)
from backend.gap_analyzer.schemas import (
    GapActionResponse,
    GapAnalyzerResponse,
    GapDraftResponse,
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

_MODE_A_DRAFT_EXAMPLE_LIMIT = 5
_MODE_B_WEEKLY_RECLUSTER_DAYS = 30
_BULK_ID_BATCH_SIZE = 1000


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
        started_at = datetime.now(UTC)

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
            coverage_score = _compute_gap_coverage(
                repository=repository,
                tenant_id=tenant_id,
                query_text=coverage_query,
                query_embedding=coverage_embeddings.get(candidate.topic_label),
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
        started_at = datetime.now(UTC)

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

        clusters = _prepare_mode_b_clusters(repository.list_mode_b_clusters(tenant_id))
        _apply_mode_b_questions_to_clusters(
            repository=repository,
            tenant_id=tenant_id,
            questions=refreshed_questions,
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
        started_at = datetime.now(UTC)
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
        cluster.last_computed_at = datetime.now(UTC)
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
            topic = (
                db.query(GapDocTopic)
                .filter(GapDocTopic.id == gap_id, GapDocTopic.tenant_id == tenant_id)
                .first()
            )
            if topic is None:
                raise GapResourceNotFoundError("Gap topic not found")
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
        cluster.last_computed_at = datetime.now(UTC)
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
        if self.repository is None:
            # This branch is only for skeleton/foundation-style tests that exercise the
            # orchestration contract without a live persistence layer behind it.
            enqueue_result = RecalculateCommandResult(
                tenant_id=tenant_id,
                mode=mode,
                status=GapCommandStatus.accepted,
                accepted_at=datetime.now(UTC),
            )
            return enqueue_result
        enqueue_result = self._require_repository().enqueue_recalculation(tenant_id, mode)
        return RecalculateCommandResult(
            tenant_id=tenant_id,
            mode=mode,
            status=enqueue_result.status,
            accepted_at=datetime.now(UTC),
            retry_after_seconds=enqueue_result.retry_after_seconds,
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
