"""Mode A ↔ Mode B link synchronisation helpers."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.gap_analyzer._classification import _effective_mode_b_status
from backend.gap_analyzer._math import _cosine_similarity, _vector_from_unknown, _vector_norm
from backend.gap_analyzer.domain import ClusteringPolicy
from backend.gap_analyzer.enums import GapClusterStatus
from backend.gap_analyzer.schemas import ModeBStatusFilter
from backend.models import GapCluster, GapDocTopic


def _active_mode_a_ids_suppressed_by_mode_b(
    *,
    db: Session,
    tenant_id: UUID,
    mode_b_status: ModeBStatusFilter,
) -> set[UUID]:
    if mode_b_status not in {"active", "all"}:
        return set()
    rows = (
        db.query(GapCluster)
        .filter(GapCluster.tenant_id == tenant_id)
        .filter(GapCluster.linked_doc_topic_id.isnot(None))
        .all()
    )
    return {
        cluster.linked_doc_topic_id
        for cluster in rows
        if cluster.linked_doc_topic_id is not None and _effective_mode_b_status(cluster) == GapClusterStatus.active
    }


def _score_mode_link_pairs_pgvector(
    *,
    db: Session,
    topics: list[GapDocTopic],
    clusters: list[GapCluster],
) -> list[tuple[float, GapDocTopic, GapCluster]]:
    cluster_by_id = {cluster.id: cluster for cluster in clusters if cluster.centroid is not None}
    if not cluster_by_id:
        return []
    scored_pairs: list[tuple[float, GapDocTopic, GapCluster]] = []
    clustering_policy = ClusteringPolicy()
    distance_cutoff = 1.0 - clustering_policy.link_threshold
    for topic in topics:
        topic_vector = _vector_from_unknown(topic.topic_embedding)
        if topic_vector is None:
            continue
        distance_expr = GapCluster.centroid.cosine_distance(topic_vector)
        rows = (
            db.query(GapCluster.id, distance_expr.label("distance"))
            .filter(GapCluster.id.in_(cluster_by_id.keys()))
            .filter(GapCluster.centroid.isnot(None))
            .order_by(distance_expr.asc(), GapCluster.id.asc())
            .limit(clustering_policy.pgvector_link_candidate_limit)
            .all()
        )
        for cluster_id, distance in rows:
            distance_value = float(distance)
            if distance_value > distance_cutoff:
                break
            cluster = cluster_by_id.get(cluster_id)
            if cluster is None:
                continue
            scored_pairs.append((max(0.0, min(1.0, 1.0 - distance_value)), topic, cluster))
    return scored_pairs


def _score_mode_link_pairs_python(
    *,
    topics: list[GapDocTopic],
    clusters: list[GapCluster],
) -> list[tuple[float, GapDocTopic, GapCluster]]:
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
    return scored_pairs


def _score_mode_link_pairs(
    *,
    db: Session,
    topics: list[GapDocTopic],
    clusters: list[GapCluster],
) -> list[tuple[float, GapDocTopic, GapCluster]]:
    if not topics or not clusters:
        return []
    if getattr(db.bind.dialect, "name", "") == "postgresql":
        scored_pairs = _score_mode_link_pairs_pgvector(
            db=db,
            topics=topics,
            clusters=clusters,
        )
        if scored_pairs:
            return scored_pairs
    return _score_mode_link_pairs_python(
        topics=topics,
        clusters=clusters,
    )


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

    scored_pairs = _score_mode_link_pairs(
        db=db,
        topics=topics,
        clusters=clusters,
    )

    linked_topic_ids: set[UUID] = set()
    linked_cluster_ids: set[UUID] = set()
    for _, topic, cluster in sorted(scored_pairs, key=lambda item: item[0], reverse=True):
        if topic.id in linked_topic_ids or cluster.id in linked_cluster_ids:
            continue
        topic.linked_cluster_id = cluster.id
        cluster.linked_doc_topic_id = topic.id
        linked_topic_ids.add(topic.id)
        linked_cluster_ids.add(cluster.id)
    # Persist the rebuilt link graph inside the current unit of work so callers that refresh
    # objects before the outer commit still observe the new associations deterministically.
    db.flush()
