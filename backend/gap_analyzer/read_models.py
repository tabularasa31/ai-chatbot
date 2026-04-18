"""Response-shape builders: convert DB rows to API response objects."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.gap_analyzer._classification import (
    _classify_gap,
    _effective_mode_b_status,
    _impact_statement,
    _mode_b_status_matches_filter,
)
from backend.gap_analyzer.enums import GapDocTopicStatus, GapSource
from backend.gap_analyzer.repository import ModeBQuestionRecord
from backend.gap_analyzer.schemas import (
    GapItemResponse,
    GapSummaryResponse,
    ModeASort,
    ModeAStatusFilter,
    ModeBSort,
    ModeBStatusFilter,
)
from backend.models import GapCluster, GapDismissal, GapDocTopic, GapQuestion


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


def _mode_b_question_record_from_row(row: GapQuestion) -> ModeBQuestionRecord:
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return ModeBQuestionRecord(
        question_id=row.id,
        question_text=row.question_text,
        embedding=row.embedding,
        gap_signal_weight=float(row.gap_signal_weight or 0.0),
        language=row.language,
        created_at=created_at,
    )


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
    linked_cluster_ids = [topic.linked_cluster_id for topic in topics if topic.linked_cluster_id is not None]
    linked_clusters = (
        db.query(GapCluster)
        .filter(GapCluster.id.in_(linked_cluster_ids))
        .all()
        if linked_cluster_ids
        else []
    )
    linked_clusters_by_id = {cluster.id: cluster for cluster in linked_clusters}

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
            linked_cluster = linked_clusters_by_id.get(topic.linked_cluster_id) if topic.linked_cluster_id is not None else None
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
                    linked_source=GapSource.mode_b if linked_cluster is not None else None,
                    linked_label=(linked_cluster.label or "").strip() if linked_cluster is not None and linked_cluster.label else None,
                    also_missing_in_docs=False,
                    last_updated=topic.extracted_at,
                )
            )
    if status_filter in {"dismissed", "archived", "all"}:
        topics_by_id = {topic.id: topic for topic in topics}
        for dismissal in dismissed_rows:
            topic = topics_by_id.get(dismissal.gap_id)
            cleaned_questions = _clean_questions(topic.example_questions if topic is not None else None)
            linked_cluster = linked_clusters_by_id.get(topic.linked_cluster_id) if topic is not None and topic.linked_cluster_id is not None else None
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
                    linked_source=GapSource.mode_b if linked_cluster is not None else None,
                    linked_label=(linked_cluster.label or "").strip() if linked_cluster is not None and linked_cluster.label else None,
                    also_missing_in_docs=False,
                    last_updated=dismissal.dismissed_at,
                )
            )

    if sort == "newest":
        items.sort(key=lambda item: (item.last_updated or datetime.min.replace(tzinfo=UTC)), reverse=True)
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
    clusters = (
        db.query(GapCluster)
        .filter(GapCluster.tenant_id == tenant_id)
        .filter(GapCluster.label.isnot(None))
        .all()
    )
    sample_questions = _load_mode_b_question_samples(db, [cluster.id for cluster in clusters])
    linked_topic_ids = [cluster.linked_doc_topic_id for cluster in clusters if cluster.linked_doc_topic_id is not None]
    linked_topics = (
        db.query(GapDocTopic)
        .filter(GapDocTopic.id.in_(linked_topic_ids))
        .all()
        if linked_topic_ids
        else []
    )
    linked_topics_by_id = {topic.id: topic for topic in linked_topics}

    items: list[GapItemResponse] = []
    for cluster in clusters:
        effective_status = _effective_mode_b_status(cluster)
        if not _mode_b_status_matches_filter(status_filter, effective_status):
            continue
        linked_topic = linked_topics_by_id.get(cluster.linked_doc_topic_id) if cluster.linked_doc_topic_id is not None else None
        items.append(
            GapItemResponse(
                id=cluster.id,
                source=GapSource.mode_b,
                label=(cluster.label or "Untitled gap").strip(),
                coverage_score=cluster.coverage_score,
                classification=_classify_gap(cluster.coverage_score),
                status=effective_status.value,
                is_new=bool(cluster.is_new),
                question_count=int(cluster.question_count or 0),
                aggregate_signal_weight=float(cluster.aggregate_signal_weight or 0.0),
                example_questions=sample_questions.get(cluster.id, []),
                linked_source=GapSource.mode_a if linked_topic is not None else None,
                linked_label=(linked_topic.topic_label or "").strip() if linked_topic is not None and linked_topic.topic_label else None,
                linked_example_questions=_clean_questions(linked_topic.example_questions if linked_topic is not None else None),
                also_missing_in_docs=linked_topic is not None,
                last_updated=cluster.last_computed_at or cluster.last_question_at or cluster.created_at,
            )
        )

    if sort == "coverage_asc":
        items.sort(key=lambda item: (_sort_float(item.coverage_score, default=999.0), item.label.casefold()))
    elif sort == "newest":
        items.sort(key=lambda item: (item.last_updated or datetime.min.replace(tzinfo=UTC)), reverse=True)
    else:
        items.sort(
            key=lambda item: (
                -float(item.aggregate_signal_weight or 0.0),
                _sort_float(item.coverage_score, default=999.0),
                item.label.casefold(),
            )
        )
    return items
