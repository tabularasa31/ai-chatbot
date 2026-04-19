from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.gap_analyzer.enums import GapSource
from backend.gap_analyzer.orchestrator import GapAnalyzerOrchestrator
from backend.gap_analyzer.repository import SqlAlchemyGapAnalyzerRepository
from backend.models import (
    GapAnalyzerJob,
    GapCluster,
    GapClusterStatus,
    GapDismissal,
    GapDocTopic,
    GapDocTopicStatus,
    GapQuestion,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


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


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------

def test_list_returns_summary_and_two_sections(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-list@example.com", name="Gap API List Tenant"
    )
    mode_a_topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        is_new=True,
        extracted_at=datetime.now(timezone.utc),
    )
    mode_b_cluster = GapCluster(
        tenant_id=tenant_id,
        label="How do invoice exports work?",
        question_count=2,
        aggregate_signal_weight=3.5,
        coverage_score=0.3,
        status=GapClusterStatus.active,
        is_new=True,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([mode_a_topic, mode_b_cluster])
    db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            GapQuestion(
                tenant_id=tenant_id,
                question_text="How do invoice exports work?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=1),
            ),
            GapQuestion(
                tenant_id=tenant_id,
                question_text="Can finance export invoices by month?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=2),
            ),
            GapQuestion(
                tenant_id=tenant_id,
                question_text="Where do invoice export files appear?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=3),
            ),
            GapQuestion(
                tenant_id=tenant_id,
                question_text="Are invoice exports available in CSV?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=4),
            ),
        ]
    )
    db_session.commit()

    response = tenant.get("/gap-analyzer", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["total_active"] == 2
    assert data["summary"]["new_badge_count"] == 2
    assert len(data["mode_a_items"]) == 1
    assert len(data["mode_b_items"]) == 1
    assert data["mode_a_items"][0]["label"] == "Billing exports"
    assert data["mode_b_items"][0]["label"] == "How do invoice exports work?"
    assert data["mode_b_items"][0]["example_questions"] == [
        "Are invoice exports available in CSV?",
        "Where do invoice export files appear?",
        "Can finance export invoices by month?",
    ]


def test_list_dedupes_linked_mode_a_when_mode_b_is_active(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-linked-active@example.com", name="Gap API Linked Active Tenant"
    )
    topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="How do invoice exports work?",
        question_count=1,
        aggregate_signal_weight=4.0,
        coverage_score=0.25,
        status=GapClusterStatus.active,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([topic, cluster])
    db_session.flush()
    topic.linked_cluster_id = cluster.id
    cluster.linked_doc_topic_id = topic.id
    db_session.add_all([topic, cluster])
    db_session.commit()

    response = tenant.get("/gap-analyzer", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["mode_a_items"] == []
    assert len(data["mode_b_items"]) == 1
    assert data["mode_b_items"][0]["linked_source"] == "mode_a"
    assert data["mode_b_items"][0]["linked_label"] == "Billing exports"
    assert data["mode_b_items"][0]["also_missing_in_docs"] is True


def test_dismissed_mode_b_does_not_hide_linked_mode_a(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-linked-dismissed@example.com", name="Gap API Linked Dismissed Tenant"
    )
    topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="How do invoice exports work?",
        question_count=1,
        aggregate_signal_weight=4.0,
        coverage_score=0.25,
        status=GapClusterStatus.dismissed,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([topic, cluster])
    db_session.flush()
    topic.linked_cluster_id = cluster.id
    cluster.linked_doc_topic_id = topic.id
    db_session.add_all([topic, cluster])
    db_session.commit()

    response = tenant.get(
        "/gap-analyzer?mode_b_status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["mode_a_items"]) == 1
    assert data["mode_a_items"][0]["label"] == "Billing exports"
    assert len(data["mode_b_items"]) == 1
    assert data["mode_b_items"][0]["status"] == "dismissed"


# ---------------------------------------------------------------------------
# Dismiss / reactivate
# ---------------------------------------------------------------------------

def test_dismiss_and_reactivate_mode_a_topic(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-dismiss-mode-a@example.com", name="Gap API Dismiss Mode A Tenant"
    )
    topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="SAML setup",
        coverage_score=0.1,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    db_session.add(topic)
    db_session.commit()
    db_session.refresh(topic)

    dismiss = tenant.post(
        f"/gap-analyzer/mode_a/{topic.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    assert dismiss.status_code == 200, dismiss.text
    assert dismiss.json()["status"] == "dismissed"

    dismissed_list = tenant.get(
        "/gap-analyzer?mode_a_status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dismissed_list.json()["mode_a_items"][0]["status"] == "dismissed"

    reactivate = tenant.post(
        f"/gap-analyzer/mode_a/{topic.id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reactivate.status_code == 200, reactivate.text
    assert reactivate.json()["status"] == "active"

    active_list = tenant.get("/gap-analyzer", headers={"Authorization": f"Bearer {token}"})
    assert active_list.json()["mode_a_items"][0]["status"] == "active"


def test_dismiss_and_reactivate_mode_b_cluster(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-dismiss-mode-b@example.com", name="Gap API Dismiss Mode B Tenant"
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="Webhook retry policy",
        question_count=2,
        aggregate_signal_weight=4.0,
        coverage_score=0.15,
        status=GapClusterStatus.active,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add(cluster)
    db_session.commit()
    db_session.refresh(cluster)

    dismiss = tenant.post(
        f"/gap-analyzer/mode_b/{cluster.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    assert dismiss.status_code == 200, dismiss.text
    assert dismiss.json()["status"] == "dismissed"

    dismissed_list = tenant.get(
        "/gap-analyzer?mode_b_status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dismissed_list.json()["mode_b_items"][0]["status"] == "dismissed"

    reactivate = tenant.post(
        f"/gap-analyzer/mode_b/{cluster.id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reactivate.status_code == 200, reactivate.text
    assert reactivate.json()["status"] == "active"

    active_list = tenant.get(
        "/gap-analyzer?mode_b_status=active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert active_list.json()["mode_b_items"][0]["status"] == "active"


def test_repeated_dismiss_is_idempotent(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-idempotent@example.com", name="Gap API Idempotent Tenant"
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="Webhook signatures",
        question_count=1,
        aggregate_signal_weight=2.0,
        coverage_score=0.1,
        status=GapClusterStatus.active,
    )
    db_session.add(cluster)
    db_session.commit()
    db_session.refresh(cluster)

    first = tenant.post(
        f"/gap-analyzer/mode_b/{cluster.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    second = tenant.post(
        f"/gap-analyzer/mode_b/{cluster.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "dismissed"
    assert second.json()["status"] == "dismissed"


def test_missing_resource_returns_404(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, _client_id = _create_client_and_token(
        tenant, db_session, email="gap-api-missing@example.com", name="Gap API Missing Tenant"
    )
    missing_id = uuid.uuid4()

    mode_a_reactivate = tenant.post(
        f"/gap-analyzer/mode_a/{missing_id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    mode_b_dismiss = tenant.post(
        f"/gap-analyzer/mode_b/{missing_id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    mode_b_reactivate = tenant.post(
        f"/gap-analyzer/mode_b/{missing_id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    mode_b_draft = tenant.post(
        f"/gap-analyzer/mode_b/{missing_id}/draft",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert mode_a_reactivate.status_code == 404, mode_a_reactivate.text
    assert mode_a_reactivate.json()["detail"] == "Gap topic not found"
    assert mode_b_dismiss.status_code == 404, mode_b_dismiss.text
    assert mode_b_dismiss.json()["detail"] == "Gap cluster not found"
    assert mode_b_reactivate.status_code == 404, mode_b_reactivate.text
    assert mode_b_draft.status_code == 404, mode_b_draft.text


# ---------------------------------------------------------------------------
# Filters and sorting
# ---------------------------------------------------------------------------

def test_filters_and_sorts_mode_b_items(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-filters@example.com", name="Gap API Filters Tenant"
    )
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            GapCluster(
                tenant_id=tenant_id,
                label="CSV export retention",
                question_count=1,
                aggregate_signal_weight=2.5,
                coverage_score=0.4,
                status=GapClusterStatus.active,
                last_computed_at=now.replace(microsecond=2),
            ),
            GapCluster(
                tenant_id=tenant_id,
                label="Audit log webhooks",
                question_count=1,
                aggregate_signal_weight=6.0,
                coverage_score=0.2,
                status=GapClusterStatus.active,
                last_computed_at=now.replace(microsecond=1),
            ),
            GapCluster(
                tenant_id=tenant_id,
                label="SAML metadata refresh",
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.85,
                status=GapClusterStatus.closed,
                last_computed_at=now.replace(microsecond=3),
            ),
        ]
    )
    db_session.commit()

    closed_only = tenant.get(
        "/gap-analyzer?mode_b_status=closed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert closed_only.status_code == 200, closed_only.text
    assert [item["label"] for item in closed_only.json()["mode_b_items"]] == ["SAML metadata refresh"]

    signal_sorted = tenant.get(
        "/gap-analyzer?mode_b_sort=signal_desc",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert [item["label"] for item in signal_sorted.json()["mode_b_items"]] == [
        "Audit log webhooks",
        "CSV export retention",
    ]

    newest_sorted = tenant.get(
        "/gap-analyzer?mode_b_status=all&mode_b_sort=newest",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert [item["label"] for item in newest_sorted.json()["mode_b_items"]] == [
        "SAML metadata refresh",
        "CSV export retention",
        "Audit log webhooks",
    ]


def test_inactive_filter_surfaces_archived_clusters(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-inactive@example.com", name="Gap API Inactive Tenant"
    )
    db_session.add_all(
        [
            GapCluster(
                tenant_id=tenant_id,
                label="Legacy exports",
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.9,
                status=GapClusterStatus.closed,
                last_computed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            GapCluster(
                tenant_id=tenant_id,
                label="Fresh dismissed cluster",
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.2,
                status=GapClusterStatus.dismissed,
                last_computed_at=datetime.now(timezone.utc),
            ),
        ]
    )
    db_session.commit()

    response = tenant.get(
        "/gap-analyzer?mode_b_status=inactive",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["label"] for item in data["mode_b_items"]] == ["Legacy exports"]
    assert data["mode_b_items"][0]["status"] == "inactive"


def test_archived_filter_is_source_specific(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-archived@example.com", name="Gap API Archived Tenant"
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


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------

def test_draft_for_mode_b_cluster_returns_markdown(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-draft-mode-b@example.com", name="Gap API Draft Mode B Tenant"
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="Invoice exports for finance",
        question_count=1,
        aggregate_signal_weight=2.0,
        coverage_score=0.25,
        status=GapClusterStatus.active,
    )
    db_session.add(cluster)
    db_session.flush()
    db_session.add(
        GapQuestion(
            tenant_id=tenant_id,
            question_text="How do invoice exports work for finance?",
            cluster_id=cluster.id,
            gap_signal_weight=2.0,
        )
    )
    db_session.commit()
    db_session.refresh(cluster)

    response = tenant.post(
        f"/gap-analyzer/mode_b/{cluster.id}/draft",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title"] == "Invoice exports for finance"
    assert "# Invoice exports for finance" in data["markdown"]
    assert "How do invoice exports work for finance?" in data["markdown"]


def test_draft_for_linked_mode_b_appends_mode_a_examples(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    cluster_id = uuid.uuid4()
    topic = SimpleNamespace(
        id=topic_id,
        tenant_id=tenant_id,
        topic_label="Invoice exports",
        example_questions=[
            "How do invoice exports work for accounting?",
            "Can I export invoices by month?",
        ],
    )
    cluster = SimpleNamespace(
        id=cluster_id,
        tenant_id=tenant_id,
        label="Invoice exports for finance",
        linked_doc_topic_id=topic_id,
        coverage_score=0.25,
        aggregate_signal_weight=2.0,
    )

    class _FakeQuery:
        def __init__(self, model):
            self._model = model

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            if self._model is GapCluster:
                return cluster
            if self._model is GapDocTopic:
                return topic
            return None

    class _FakeDB:
        def query(self, model):
            return _FakeQuery(model)

    orchestrator = GapAnalyzerOrchestrator()
    monkeypatch.setattr(
        orchestrator,
        "_require_sqlalchemy_repository",
        lambda: SqlAlchemyGapAnalyzerRepository(db=_FakeDB()),
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.orchestrator._load_mode_b_question_samples",
        lambda db, cluster_ids: {cluster_id: ["How do invoice exports work for finance?"]},
    )

    draft = orchestrator.build_draft(tenant_id=tenant_id, source=GapSource.mode_b, gap_id=cluster_id)

    assert "## Also missing in docs" in draft.markdown
    assert "How do invoice exports work for accounting?" in draft.markdown
    assert "Can I export invoices by month?" in draft.markdown


def test_draft_for_mode_a_limits_example_questions(
    monkeypatch,
) -> None:
    topic_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    topic = SimpleNamespace(
        id=topic_id,
        tenant_id=tenant_id,
        topic_label="Webhook retries",
        example_questions=[
            "How many retries do webhooks get?",
            "Can I change the webhook retry delay?",
            "Do failed webhooks retry automatically?",
            "Where can I see webhook retry history?",
            "What status codes trigger webhook retries?",
            "Can I disable webhook retries per endpoint?",
        ],
    )

    class _FakeQuery:
        def __init__(self, result):
            self._result = result

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self._result

    class _FakeDB:
        def query(self, model):
            if model is GapDocTopic:
                return _FakeQuery(topic)
            if model is GapDismissal:
                return _FakeQuery(None)
            raise AssertionError(f"Unexpected model query: {model}")

    orchestrator = GapAnalyzerOrchestrator()
    monkeypatch.setattr(
        orchestrator,
        "_require_sqlalchemy_repository",
        lambda: SqlAlchemyGapAnalyzerRepository(db=_FakeDB()),
    )

    draft = orchestrator.build_draft(tenant_id=tenant_id, source=GapSource.mode_a, gap_id=topic_id)

    assert draft.title == "Webhook retries"
    assert "How many retries do webhooks get?" in draft.markdown
    assert "What status codes trigger webhook retries?" in draft.markdown
    assert "Can I disable webhook retries per endpoint?" not in draft.markdown


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------

def test_summary_returns_badge_payload_only(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-summary@example.com", name="Gap API Summary Tenant"
    )
    db_session.add(
        GapDocTopic(
            tenant_id=tenant_id,
            topic_label="SAML setup",
            coverage_score=0.2,
            status=GapDocTopicStatus.active,
            is_new=True,
            extracted_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    response = tenant.get("/gap-analyzer/summary", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data.keys()) == {"summary"}
    assert data["summary"]["new_badge_count"] == 1
    assert "mode_a_items" not in data
    assert "mode_b_items" not in data


def test_summary_uses_lightweight_repository_path(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    token, _client_id = _create_client_and_token(
        tenant, db_session, email="gap-api-summary-lightweight@example.com", name="Gap API Summary Lightweight Tenant"
    )
    summary = {
        "total_active": 7,
        "uncovered_count": 3,
        "partial_count": 2,
        "impact_statement": "3 uncovered gaps need attention.",
        "new_badge_count": 4,
        "last_updated": None,
    }

    class _FakeRepository:
        def get_gap_summary(self, *, tenant_id):
            return summary

    monkeypatch.setattr(
        "backend.gap_analyzer.routes._resolve_gap_analyzer_repository",
        lambda *, db: _FakeRepository(),
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.routes._resolve_gap_analyzer_orchestrator",
        lambda *, db: (_ for _ in ()).throw(AssertionError("summary route should not build full gap payload")),
    )

    response = tenant.get("/gap-analyzer/summary", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    assert response.json() == {"summary": summary}


def test_summary_dedupes_mode_a_suppressed_by_linked_mode_b(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, tenant_id = _create_client_and_token(
        tenant, db_session, email="gap-api-summary-linked@example.com", name="Gap API Summary Linked Tenant"
    )
    topic = GapDocTopic(
        tenant_id=tenant_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        is_new=True,
        extracted_at=datetime.now(timezone.utc),
    )
    cluster = GapCluster(
        tenant_id=tenant_id,
        label="How do invoice exports work?",
        question_count=2,
        aggregate_signal_weight=3.0,
        coverage_score=0.2,
        status=GapClusterStatus.active,
        is_new=True,
        last_computed_at=datetime.now(timezone.utc),
    )
    db_session.add_all([topic, cluster])
    db_session.flush()
    topic.linked_cluster_id = cluster.id
    cluster.linked_doc_topic_id = topic.id
    db_session.add_all([topic, cluster])
    db_session.commit()

    response = tenant.get("/gap-analyzer/summary", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    data = response.json()["summary"]
    assert data["total_active"] == 1
    assert data["new_badge_count"] == 1
    assert data["uncovered_count"] == 1


# ---------------------------------------------------------------------------
# Recalculate
# ---------------------------------------------------------------------------

def test_recalculate_returns_accepted_and_starts_jobs(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    token, _client_id = _create_client_and_token(
        tenant, db_session, email="gap-api-recalc@example.com", name="Gap API Recalc Tenant"
    )
    runner_calls: list[str] = []
    monkeypatch.setattr(
        "backend.gap_analyzer.routes.start_gap_analyzer_job_runner",
        lambda: runner_calls.append("started"),
    )

    response = tenant.post(
        "/gap-analyzer/recalculate?mode=both",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 202, response.text
    data = response.json()
    assert data["status"] == "accepted"
    assert data["command_kind"] == "orchestration"
    assert data["mode"] == "both"
    assert runner_calls == ["started"]
    queued_jobs = (
        db_session.query(GapAnalyzerJob)
        .filter(GapAnalyzerJob.tenant_id == _client_id)
        .all()
    )
    assert len(queued_jobs) == 2
