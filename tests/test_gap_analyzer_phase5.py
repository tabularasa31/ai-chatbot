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
    GapDismissal,
    GapCluster,
    GapClusterStatus,
    GapDocTopic,
    GapDocTopicStatus,
    GapQuestion,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client_and_token(
    client: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(client, db_session, email=email)
    response = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert response.status_code == 201, response.json()
    client_id = uuid.UUID(response.json()["id"])
    set_client_openai_key(client, token)
    return token, client_id


def test_gap_analyzer_list_returns_summary_and_two_sections(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-list@example.com",
        name="Gap Phase 5 List Client",
    )
    mode_a_topic = GapDocTopic(
        tenant_id=client_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        is_new=True,
        extracted_at=datetime.now(timezone.utc),
    )
    mode_b_cluster = GapCluster(
        tenant_id=client_id,
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
                tenant_id=client_id,
                question_text="How do invoice exports work?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=1),
            ),
            GapQuestion(
                tenant_id=client_id,
                question_text="Can finance export invoices by month?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=2),
            ),
            GapQuestion(
                tenant_id=client_id,
                question_text="Where do invoice export files appear?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=3),
            ),
            GapQuestion(
                tenant_id=client_id,
                question_text="Are invoice exports available in CSV?",
                cluster_id=mode_b_cluster.id,
                gap_signal_weight=2.0,
                created_at=now.replace(microsecond=4),
            ),
        ]
    )
    db_session.commit()

    response = client.get(
        "/gap-analyzer",
        headers={"Authorization": f"Bearer {token}"},
    )

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


def test_gap_analyzer_active_list_dedupes_linked_mode_a_when_mode_b_is_active(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase6-linked-active@example.com",
        name="Gap Phase 6 Linked Active Client",
    )
    topic = GapDocTopic(
        tenant_id=client_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    cluster = GapCluster(
        tenant_id=client_id,
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

    response = client.get(
        "/gap-analyzer",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["mode_a_items"] == []
    assert len(data["mode_b_items"]) == 1
    assert data["mode_b_items"][0]["linked_source"] == "mode_a"
    assert data["mode_b_items"][0]["also_missing_in_docs"] is True


def test_gap_analyzer_dismissed_mode_b_does_not_hide_active_mode_a(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase6-linked-dismissed@example.com",
        name="Gap Phase 6 Linked Dismissed Client",
    )
    topic = GapDocTopic(
        tenant_id=client_id,
        topic_label="Billing exports",
        coverage_score=0.2,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    cluster = GapCluster(
        tenant_id=client_id,
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

    response = client.get(
        "/gap-analyzer?mode_b_status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["mode_a_items"]) == 1
    assert data["mode_a_items"][0]["label"] == "Billing exports"
    assert len(data["mode_b_items"]) == 1
    assert data["mode_b_items"][0]["status"] == "dismissed"


def test_gap_analyzer_dismiss_and_reactivate_mode_a_topic(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-dismiss@example.com",
        name="Gap Phase 5 Dismiss Client",
    )
    topic = GapDocTopic(
        tenant_id=client_id,
        topic_label="SAML setup",
        coverage_score=0.1,
        status=GapDocTopicStatus.active,
        extracted_at=datetime.now(timezone.utc),
    )
    db_session.add(topic)
    db_session.commit()
    db_session.refresh(topic)

    dismiss_response = client.post(
        f"/gap-analyzer/mode_a/{topic.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    assert dismiss_response.status_code == 200, dismiss_response.text
    assert dismiss_response.json()["status"] == "dismissed"

    dismissed_list = client.get(
        "/gap-analyzer?mode_a_status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dismissed_list.status_code == 200, dismissed_list.text
    assert dismissed_list.json()["mode_a_items"][0]["status"] == "dismissed"

    reactivate_response = client.post(
        f"/gap-analyzer/mode_a/{topic.id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reactivate_response.status_code == 200, reactivate_response.text
    assert reactivate_response.json()["status"] == "active"

    active_list = client.get(
        "/gap-analyzer",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert active_list.status_code == 200, active_list.text
    assert active_list.json()["mode_a_items"][0]["status"] == "active"


def test_gap_analyzer_dismiss_and_reactivate_mode_b_cluster(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-modeb-dismiss@example.com",
        name="Gap Phase 5 Mode B Dismiss Client",
    )
    cluster = GapCluster(
        tenant_id=client_id,
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

    dismiss_response = client.post(
        f"/gap-analyzer/mode_b/{cluster.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    assert dismiss_response.status_code == 200, dismiss_response.text
    assert dismiss_response.json()["status"] == "dismissed"

    dismissed_list = client.get(
        "/gap-analyzer?mode_b_status=dismissed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dismissed_list.status_code == 200, dismissed_list.text
    assert dismissed_list.json()["mode_b_items"][0]["status"] == "dismissed"

    reactivate_response = client.post(
        f"/gap-analyzer/mode_b/{cluster.id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reactivate_response.status_code == 200, reactivate_response.text
    assert reactivate_response.json()["status"] == "active"

    active_list = client.get(
        "/gap-analyzer?mode_b_status=active",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert active_list.status_code == 200, active_list.text
    assert active_list.json()["mode_b_items"][0]["status"] == "active"


def test_gap_analyzer_repeated_dismiss_is_idempotent_for_mode_b_cluster(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-repeat-dismiss@example.com",
        name="Gap Phase 5 Repeat Dismiss Client",
    )
    cluster = GapCluster(
        tenant_id=client_id,
        label="Webhook signatures",
        question_count=1,
        aggregate_signal_weight=2.0,
        coverage_score=0.1,
        status=GapClusterStatus.active,
    )
    db_session.add(cluster)
    db_session.commit()
    db_session.refresh(cluster)

    first = client.post(
        f"/gap-analyzer/mode_b/{cluster.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    second = client.post(
        f"/gap-analyzer/mode_b/{cluster.id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "dismissed"
    assert second.json()["status"] == "dismissed"


def test_gap_analyzer_returns_not_found_for_missing_mode_b_cluster_actions(
    client: TestClient,
    db_session: Session,
) -> None:
    token, _client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-missing-cluster@example.com",
        name="Gap Phase 5 Missing Cluster Client",
    )
    missing_cluster_id = uuid.uuid4()

    dismiss_response = client.post(
        f"/gap-analyzer/mode_b/{missing_cluster_id}/dismiss",
        headers={"Authorization": f"Bearer {token}"},
        json={"reason": "other"},
    )
    reactivate_response = client.post(
        f"/gap-analyzer/mode_b/{missing_cluster_id}/reactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    draft_response = client.post(
        f"/gap-analyzer/mode_b/{missing_cluster_id}/draft",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert dismiss_response.status_code == 404, dismiss_response.text
    assert dismiss_response.json()["detail"] == "Gap cluster not found"
    assert reactivate_response.status_code == 404, reactivate_response.text
    assert reactivate_response.json()["detail"] == "Gap cluster not found"
    assert draft_response.status_code == 404, draft_response.text
    assert draft_response.json()["detail"] == "Gap cluster not found"


def test_gap_analyzer_filters_and_sorts_mode_b_items(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-modeb-filters@example.com",
        name="Gap Phase 5 Mode B Filters Client",
    )
    oldest = datetime(2026, 1, 1, tzinfo=timezone.utc)
    middle = datetime(2026, 1, 2, tzinfo=timezone.utc)
    newest = datetime(2026, 1, 3, tzinfo=timezone.utc)
    db_session.add_all(
        [
            GapCluster(
                tenant_id=client_id,
                label="CSV export retention",
                question_count=1,
                aggregate_signal_weight=2.5,
                coverage_score=0.4,
                status=GapClusterStatus.active,
                last_computed_at=middle,
            ),
            GapCluster(
                tenant_id=client_id,
                label="Audit log webhooks",
                question_count=1,
                aggregate_signal_weight=6.0,
                coverage_score=0.2,
                status=GapClusterStatus.active,
                last_computed_at=oldest,
            ),
            GapCluster(
                tenant_id=client_id,
                label="SAML metadata refresh",
                question_count=1,
                aggregate_signal_weight=1.0,
                coverage_score=0.85,
                status=GapClusterStatus.closed,
                last_computed_at=newest,
            ),
        ]
    )
    db_session.commit()

    closed_only = client.get(
        "/gap-analyzer?mode_b_status=closed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert closed_only.status_code == 200, closed_only.text
    assert [item["label"] for item in closed_only.json()["mode_b_items"]] == ["SAML metadata refresh"]

    signal_sorted = client.get(
        "/gap-analyzer?mode_b_sort=signal_desc",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert signal_sorted.status_code == 200, signal_sorted.text
    assert [item["label"] for item in signal_sorted.json()["mode_b_items"]] == [
        "Audit log webhooks",
        "CSV export retention",
    ]

    newest_sorted = client.get(
        "/gap-analyzer?mode_b_status=all&mode_b_sort=newest",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert newest_sorted.status_code == 200, newest_sorted.text
    assert [item["label"] for item in newest_sorted.json()["mode_b_items"]] == [
        "SAML metadata refresh",
        "CSV export retention",
        "Audit log webhooks",
    ]


def test_gap_analyzer_draft_for_mode_b_cluster_returns_transient_markdown(
    client: TestClient,
    db_session: Session,
) -> None:
    token, client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-draft@example.com",
        name="Gap Phase 5 Draft Client",
    )
    cluster = GapCluster(
        tenant_id=client_id,
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
            tenant_id=client_id,
            question_text="How do invoice exports work for finance?",
            cluster_id=cluster.id,
            gap_signal_weight=2.0,
        )
    )
    db_session.commit()
    db_session.refresh(cluster)

    response = client.post(
        f"/gap-analyzer/mode_b/{cluster.id}/draft",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title"] == "Invoice exports for finance"
    assert "# Invoice exports for finance" in data["markdown"]
    assert "How do invoice exports work for finance?" in data["markdown"]


def test_gap_analyzer_draft_for_linked_mode_b_cluster_appends_mode_a_examples(
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
        lambda db, cluster_ids: {
            cluster_id: ["How do invoice exports work for finance?"]
        },
    )

    draft = orchestrator.build_draft(
        tenant_id=tenant_id,
        source=GapSource.mode_b,
        gap_id=cluster_id,
    )

    assert "## Also missing in docs" in draft.markdown
    assert "How do invoice exports work for accounting?" in draft.markdown
    assert "Can I export invoices by month?" in draft.markdown


def test_gap_analyzer_draft_for_mode_a_limits_example_questions(
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

    draft = orchestrator.build_draft(
        tenant_id=tenant_id,
        source=GapSource.mode_a,
        gap_id=topic_id,
    )

    assert draft.title == "Webhook retries"
    assert "How many retries do webhooks get?" in draft.markdown
    assert "What status codes trigger webhook retries?" in draft.markdown
    assert "Can I disable webhook retries per endpoint?" not in draft.markdown


def test_gap_analyzer_recalculate_returns_accepted_and_starts_tasks(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    token, _client_id = _create_client_and_token(
        client,
        db_session,
        email="gap-phase5-recalc@example.com",
        name="Gap Phase 5 Recalc Client",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.gap_analyzer.routes.run_mode_a_for_tenant_best_effort",
        lambda tenant_id: calls.append(f"mode_a:{tenant_id}"),
    )
    monkeypatch.setattr(
        "backend.gap_analyzer.routes.run_mode_b_for_tenant_best_effort",
        lambda tenant_id: calls.append(f"mode_b:{tenant_id}"),
    )

    response = client.post(
        "/gap-analyzer/recalculate?mode=both",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 202, response.text
    data = response.json()
    assert data["status"] == "accepted"
    assert data["command_kind"] == "orchestration"
    assert data["mode"] == "both"
    assert len(calls) == 2
