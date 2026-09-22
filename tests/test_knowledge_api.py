from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Tenant, TenantFaq, TenantProfile
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client(http: TestClient, db: Session, *, email: str) -> tuple[str, Tenant]:
    token = register_and_verify_user(http, db, email=email)
    resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Knowledge API Tenant"},
    )
    assert resp.status_code in (200, 201), resp.text
    set_client_openai_key(http, token)
    client_row = db.get(Tenant, uuid.UUID(resp.json()["id"]))
    assert client_row is not None
    return token, client_row


def _kbase(_client_row: Tenant) -> str:
    return "/api/v1/knowledge"


@pytest.fixture
def faq_events(monkeypatch) -> list[dict]:
    """Every faq.reviewed event emitted during the test, in order."""
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.knowledge.events.capture_event", fake_capture)
    return events


def _update_profile(db: Session, tenant_id: uuid.UUID, **fields: object) -> TenantProfile:
    """Update the profile eager-created by create_tenant."""
    profile = db.get(TenantProfile, tenant_id)
    assert profile is not None, "create_tenant must eager-create the profile"
    for key, value in fields.items():
        setattr(profile, key, value)
    db.commit()
    return profile


def _make_faq(db: Session, tenant_id: uuid.UUID, **fields: object) -> TenantFaq:
    defaults = {"question": "Q", "answer": "A", "approved": False, "source": "docs"}
    defaults.update(fields)
    faq = TenantFaq(tenant_id=tenant_id, **defaults)
    db.add(faq)
    db.commit()
    db.refresh(faq)
    return faq


def test_get_profile_is_pure_read(tenant: TestClient, db_session: Session) -> None:
    """GET /knowledge/profile must never create DB rows (no lazy-create)."""
    token, client_row = _create_client(tenant, db_session, email="kapi-pure-read@example.com")

    # Profile is eager-created at tenant creation with pending defaults.
    profile = db_session.get(TenantProfile, client_row.id)
    assert profile is not None

    resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["extraction_status"] == "pending"

    # Without a profile row, GET returns 404 and does not create one.
    db_session.delete(profile)
    db_session.commit()

    resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404, resp.text
    db_session.expire_all()
    assert db_session.get(TenantProfile, client_row.id) is None


def test_profile_read_and_partial_update_journey(tenant: TestClient, db_session: Session) -> None:
    """Covers: GET reflects stored profile fields, PATCH updates only the given field."""
    token, client_row = _create_client(tenant, db_session, email="kapi-profile@example.com")
    _update_profile(
        db_session,
        client_row.id,
        product_name="Acme API",
        topics=["Payments"],
        support_email="help@acme.com",
        support_urls=["https://acme.com/docs"],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )

    get_resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 200, get_resp.text
    get_data = get_resp.json()
    assert get_data["product_name"] == "Acme API"
    assert get_data["extraction_status"] == "done"
    assert get_data["topics"] == ["Payments"]
    assert "modules" not in get_data

    patch_resp = tenant.patch(
        f"{_kbase(client_row)}/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"product_name": "Acme API v2"},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patch_data = patch_resp.json()
    assert patch_data["product_name"] == "Acme API v2"
    assert patch_data["topics"] == ["Payments"]


def test_get_faq_filters(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-faq@example.com")
    _make_faq(db_session, client_row.id, question="Q1", answer="A1", approved=False, source="docs")
    _make_faq(db_session, client_row.id, question="Q2", answer="A2", approved=True, source="logs")

    resp = tenant.get(
        f"{_kbase(client_row)}/faq?approved=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["approved"] is False
    assert data["pending_count"] == 1


def test_faq_approve_journey(
    tenant: TestClient, db_session: Session, faq_events: list[dict]
) -> None:
    """Covers: approve flips the flag, generates a missing embedding, emits faq.reviewed."""
    token, client_row = _create_client(tenant, db_session, email="kapi-approve@example.com")
    faq = _make_faq(
        db_session,
        client_row.id,
        question="Webhook retries?",
        answer="Up to 5 times",
        source="docs",
        question_embedding=None,
    )

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/{faq.id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(faq)
    assert faq.approved is True
    assert faq.question_embedding is not None

    assert len(faq_events) == 1
    event = faq_events[0]
    assert event["event"] == "faq.reviewed"
    assert event["distinct_id"] == str(client_row.public_id)
    assert event["tenant_id"] == str(client_row.public_id)
    assert event["groups"] == {"tenant": str(client_row.public_id)}
    assert event["properties"] == {"action": "approve", "count": 1, "faq_source": "docs"}


@pytest.mark.parametrize(
    "method, path_suffix, expected_properties",
    [
        pytest.param("post", "reject", {"action": "reject", "count": 1, "faq_source": "logs"}, id="reject"),
        pytest.param(
            "delete", "", {"action": "reject", "count": 1, "faq_source": "swagger"}, id="delete_as_reject"
        ),
    ],
)
def test_faq_reject_and_delete_emit_reviewed_event(
    tenant: TestClient,
    db_session: Session,
    faq_events: list[dict],
    method: str,
    path_suffix: str,
    expected_properties: dict,
) -> None:
    """DELETE shares the reject user intent (removing an FAQ candidate)."""
    token, client_row = _create_client(tenant, db_session, email=f"kapi-{method}{path_suffix}@example.com")
    faq = _make_faq(db_session, client_row.id, source=expected_properties["faq_source"])

    url = f"{_kbase(client_row)}/faq/{faq.id}"
    if path_suffix:
        url += f"/{path_suffix}"
    resp = getattr(tenant, method)(url, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert len(faq_events) == 1
    assert faq_events[0]["properties"] == expected_properties


def test_faq_approve_all_journey(
    tenant: TestClient, db_session: Session, faq_events: list[dict]
) -> None:
    """Covers: approve-all with no FAQs emits a zero count; with pending FAQs it
    approves all, backfills any missing embedding, and reports the true count."""
    token, client_row = _create_client(tenant, db_session, email="kapi-approve-all@example.com")

    noop_resp = tenant.post(
        f"{_kbase(client_row)}/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert noop_resp.status_code == 200, noop_resp.text
    assert noop_resp.json()["approved_count"] == 0
    assert faq_events[-1]["properties"] == {"action": "approve_all", "count": 0}

    faq1 = _make_faq(db_session, client_row.id, question="Q1", answer="A1", question_embedding=None)
    _make_faq(db_session, client_row.id, question="Q2", answer="A2")

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_count"] == 2

    db_session.refresh(faq1)
    assert faq1.approved is True
    assert faq1.question_embedding is not None
    assert faq_events[-1]["properties"] == {"action": "approve_all", "count": 2}


@pytest.mark.parametrize(
    "case_id, initial_question, initial_answer, new_question, new_answer, expect_content_changed, seed_embedding",
    [
        pytest.param(
            "question_changed", "How?", "Like this.", "How exactly?", "Like this.", True, False,
            id="question_changed",
        ),
        pytest.param(
            "answer_changed", "How exactly?", "Old answer", "How exactly?", "New answer", True, True,
            id="answer_changed",
        ),
        pytest.param(
            "unchanged_noop", "How?", "Like this.", "How?", "Like this.", False, False,
            id="unchanged_noop",
        ),
    ],
)
def test_faq_edit_resets_approval_and_reports_content_changed(
    tenant: TestClient,
    db_session: Session,
    faq_events: list[dict],
    case_id: str,
    initial_question: str,
    initial_answer: str,
    new_question: str,
    new_answer: str,
    expect_content_changed: bool,
    seed_embedding: bool,
) -> None:
    token, client_row = _create_client(tenant, db_session, email=f"kapi-edit-{case_id}@example.com")
    faq = _make_faq(
        db_session,
        client_row.id,
        question=initial_question,
        answer=initial_answer,
        approved=True,
        question_embedding=[0.1] * 1536 if seed_embedding else None,
    )

    resp = tenant.put(
        f"{_kbase(client_row)}/faq/{faq.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": new_question, "answer": new_answer},
    )
    assert resp.status_code == 200, resp.text
    # A content edit resets approval; a no-op edit leaves the prior approval untouched.
    assert resp.json()["approved"] is not expect_content_changed
    assert faq_events[0]["properties"]["content_changed"] is expect_content_changed


def test_tenant_isolation(tenant: TestClient, db_session: Session) -> None:
    token1, client1 = _create_client(tenant, db_session, email="kapi-owner-1@example.com")
    token2, client2 = _create_client(tenant, db_session, email="kapi-owner-2@example.com")

    _update_profile(
        db_session,
        client1.id,
        product_name="Tenant One Product",
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )

    resp1 = tenant.get("/api/v1/knowledge/profile", headers={"Authorization": f"Bearer {token1}"})
    resp2 = tenant.get("/api/v1/knowledge/profile", headers={"Authorization": f"Bearer {token2}"})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["product_name"] == "Tenant One Product"
    assert resp2.json()["product_name"] != "Tenant One Product"


def test_faq_reviewed_never_carries_question_or_answer_text(
    tenant: TestClient, db_session: Session, faq_events: list[dict]
) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-events-notext@example.com")
    faq = _make_faq(
        db_session,
        client_row.id,
        question="Very secret question text",
        answer="Very secret answer text",
    )

    tenant.post(
        f"{_kbase(client_row)}/faq/{faq.id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    for event in faq_events:
        for value in event["properties"].values():
            assert "secret" not in str(value)
