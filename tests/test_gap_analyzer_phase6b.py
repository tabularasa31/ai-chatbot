from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import GapCluster, GapClusterStatus, GapDocTopic, GapDocTopicStatus, GapQuestion
from tests.conftest import register_and_verify_user, set_client_openai_key


def _vector(*values: float) -> list[float]:
    padded = list(values)[:1536]
    return padded + [0.0] * (1536 - len(padded))


def _create_client_and_token(
    tenant: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(tenant, db_session, email=email)
    response = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.json()
    tenant_id = uuid.UUID(response.json()["id"])
    set_client_openai_key(tenant, token)
    return token, tenant_id


def test_run_mode_b_weekly_reclustering_merges_recent_duplicate_clusters(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-phase6b-recluster@example.com",
        name="Gap Phase 6B Reclustering Tenant",
    )
    now = datetime.now(timezone.utc)
    active_cluster_a = GapCluster(
        tenant_id=tenant_id,
        label="Invoice exports",
        centroid=_vector(1.0, 0.0, 0.0),
        question_count=1,
        aggregate_signal_weight=1.5,
        coverage_score=0.2,
        status=GapClusterStatus.active,
        is_new=False,
        last_question_at=now - timedelta(days=8),
        last_computed_at=now - timedelta(days=8),
    )
    active_cluster_b = GapCluster(
        tenant_id=tenant_id,
        label="Invoice export workflow",
        centroid=_vector(0.99, 0.01, 0.0),
        question_count=2,
        aggregate_signal_weight=3.0,
        coverage_score=0.25,
        status=GapClusterStatus.active,
        is_new=False,
        last_question_at=now - timedelta(days=3),
        last_computed_at=now - timedelta(days=3),
    )
    dismissed_cluster = GapCluster(
        tenant_id=tenant_id,
        label="Legacy exports",
        centroid=_vector(0.0, 1.0, 0.0),
        question_count=1,
        aggregate_signal_weight=4.0,
        coverage_score=0.2,
        status=GapClusterStatus.dismissed,
        is_new=False,
        question_count_at_dismissal=1,
        last_question_at=now - timedelta(days=2),
        last_computed_at=now - timedelta(days=2),
    )
    db_session.add_all([active_cluster_a, active_cluster_b, dismissed_cluster])
    db_session.flush()
    active_cluster_a_id = active_cluster_a.id
    active_cluster_b_id = active_cluster_b.id
    dismissed_cluster_id = dismissed_cluster.id

    old_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="Can I export invoices by month?",
        embedding=_vector(1.0, 0.0, 0.0),
        cluster_id=active_cluster_a_id,
        gap_signal_weight=1.5,
        created_at=now - timedelta(days=45),
    )
    recent_question_a = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work?",
        embedding=_vector(1.0, 0.0, 0.0),
        cluster_id=active_cluster_a_id,
        gap_signal_weight=2.0,
        created_at=now - timedelta(days=6),
    )
    recent_question_b = GapQuestion(
        tenant_id=tenant_id,
        question_text="Where is the invoice export workflow documented?",
        embedding=_vector(0.99, 0.01, 0.0),
        cluster_id=active_cluster_b_id,
        gap_signal_weight=3.0,
        created_at=now - timedelta(days=2),
    )
    dismissed_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="Legacy export schedule",
        embedding=_vector(0.0, 1.0, 0.0),
        cluster_id=dismissed_cluster_id,
        gap_signal_weight=4.0,
        created_at=now - timedelta(days=1),
    )
    db_session.add_all([old_question, recent_question_a, recent_question_b, dismissed_question])
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    result = orchestrator.run_mode_b_weekly_reclustering(tenant_id)

    assert result.tenant_id == tenant_id

    clusters = (
        db_session.query(GapCluster)
        .filter(GapCluster.tenant_id == tenant_id)
        .order_by(GapCluster.created_at.asc(), GapCluster.id.asc())
        .all()
    )
    active_or_closed_clusters = [cluster for cluster in clusters if cluster.status in {GapClusterStatus.active, GapClusterStatus.closed}]
    assert len(active_or_closed_clusters) == 1

    rebuilt_cluster = active_or_closed_clusters[0]
    assert rebuilt_cluster.id not in {active_cluster_a_id, active_cluster_b_id}
    assert rebuilt_cluster.question_count == 3
    assert rebuilt_cluster.is_new is False

    db_session.refresh(old_question)
    db_session.refresh(recent_question_a)
    db_session.refresh(recent_question_b)
    db_session.refresh(dismissed_question)

    assert old_question.cluster_id == rebuilt_cluster.id
    assert recent_question_a.cluster_id == rebuilt_cluster.id
    assert recent_question_b.cluster_id == rebuilt_cluster.id
    assert dismissed_question.cluster_id == dismissed_cluster_id
    assert db_session.get(GapCluster, dismissed_cluster_id) is not None


def test_gap_analyzer_archived_filters_preserve_source_specific_archive_truth(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-phase6b-archive@example.com",
        name="Gap Phase 6B Archive Tenant",
    )
    active_topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    dismissed_topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Legacy billing exports",
        coverage_score=0.3,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    active_cluster = GapCluster(
        tenant_id=tenant_id,
        label="Invoice export workflow",
        question_count=1,
        aggregate_signal_weight=2.0,
        coverage_score=0.2,
        status=GapClusterStatus.active,
        last_computed_at=datetime.now(timezone.utc),
    )
    closed_cluster = GapCluster(
        tenant_id=tenant_id,
        label="SAML metadata refresh",
        question_count=1,
        aggregate_signal_weight=1.0,
        coverage_score=0.9,
        status=GapClusterStatus.closed,
        last_computed_at=datetime.now(timezone.utc),
    )
    dismissed_cluster = GapCluster(
        tenant_id=tenant_id,
        label="Legacy invoice exports",
        question_count=1,
        aggregate_signal_weight=3.0,
        coverage_score=0.4,
        status=GapClusterStatus.dismissed,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([active_topic, dismissed_topic, active_cluster, closed_cluster, dismissed_cluster])
    db_session.flush()
    active_topic.linked_cluster_id = dismissed_cluster.id
    dismissed_cluster.linked_doc_topic_id = active_topic.id
    db_session.add_all([active_topic, dismissed_cluster])
    db_session.commit()

    tenant.post(
        f"/gap-analyzer/mode_a/{dismissed_topic.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )

    response = tenant.get(
        "/gap-analyzer?mode_a_status=archived&mode_b_status=archived",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["label"] for item in data["mode_a_items"]] == ["Legacy billing exports"]
    assert {item["label"] for item in data["mode_b_items"]} == {
        "SAML metadata refresh",
        "Legacy invoice exports",
    }
    assert all(item["status"] in {"closed", "dismissed"} for item in data["mode_b_items"])

    mixed_response = tenant.get(
        "/gap-analyzer?mode_a_status=all&mode_b_status=archived",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert mixed_response.status_code == 200, mixed_response.text
    mixed_data = mixed_response.json()
    assert {item["label"] for item in mixed_data["mode_a_items"]} == {
        "Billing exports",
        "Legacy billing exports",
    }
    assert {item["status"] for item in mixed_data["mode_b_items"]} == {"closed", "dismissed"}


def test_run_mode_b_weekly_reclustering_preserves_clusters_with_blank_unembedded_questions(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, tenant_id = _create_client_and_token(
        tenant,
        db_session,
        email="gap-phase6b-blank@example.com",
        name="Gap Phase 6B Blank Question Tenant",
    )
    now = datetime.now(timezone.utc)
    protected_cluster = GapCluster(
        tenant_id=tenant_id,
        label="Invoice exports",
        centroid=_vector(1.0, 0.0, 0.0),
        question_count=2,
        aggregate_signal_weight=3.0,
        coverage_score=0.25,
        status=GapClusterStatus.active,
        is_new=False,
        last_question_at=now - timedelta(days=2),
        last_computed_at=now - timedelta(days=2),
    )
    db_session.add(protected_cluster)
    db_session.flush()
    protected_cluster_id = protected_cluster.id

    blank_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="",
        embedding=None,
        cluster_id=protected_cluster_id,
        gap_signal_weight=1.0,
        created_at=now - timedelta(days=1),
    )
    valid_question = GapQuestion(
        tenant_id=tenant_id,
        question_text="How do invoice exports work?",
        embedding=_vector(1.0, 0.0, 0.0),
        cluster_id=protected_cluster_id,
        gap_signal_weight=2.0,
        created_at=now - timedelta(days=1),
    )
    db_session.add_all([blank_question, valid_question])
    db_session.commit()

    orchestrator = GapAnalyzerOrchestrator(repository=SqlAlchemyGapAnalyzerRepository(db_session))
    result = orchestrator.run_mode_b_weekly_reclustering(tenant_id)

    assert result.tenant_id == tenant_id
    assert db_session.get(GapCluster, protected_cluster_id) is not None

    db_session.refresh(blank_question)
    db_session.refresh(valid_question)
    assert blank_question.cluster_id == protected_cluster_id
    assert valid_question.cluster_id == protected_cluster_id
