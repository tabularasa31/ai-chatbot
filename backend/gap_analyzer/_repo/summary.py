"""Gap summary aggregation query."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer._classification import _classify_gap, _impact_statement
from backend.gap_analyzer._repo.capabilities import _aware_datetime
from backend.gap_analyzer.enums import GapClusterStatus, GapDocTopicStatus, GapSource
from backend.gap_analyzer.schemas import GapSummaryResponse
from backend.models import GapCluster, GapDismissal, GapDocTopic


class _SummaryOps:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_gap_summary(self, *, tenant_id: UUID) -> GapSummaryResponse:
        active_linked_mode_a_ids = {
            linked_doc_topic_id
            for (linked_doc_topic_id,) in (
                self._db.query(GapCluster.linked_doc_topic_id)
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
                self._db.query(GapDismissal.gap_id)
                .filter(GapDismissal.tenant_id == tenant_id)
                .filter(GapDismissal.source == GapSource.mode_a)
                .all()
            )
        }

        topic_query = (
            self._db.query(
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
            self._db.query(
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
