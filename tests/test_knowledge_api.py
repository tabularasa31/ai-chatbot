from __future__ import annotations

import uuid
from datetime import datetime, timezone

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


def test_get_profile(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-profile@example.com")
    profile = TenantProfile(
        tenant_id=client_row.id,
        product_name="Acme API",
        topics=["Payments"],
        glossary=[],
        aliases=[],
        support_email="help@acme.com",
        support_urls=["https://acme.com/docs"],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile)
    db_session.commit()

    resp = tenant.get(f"{_kbase(client_row)}/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["product_name"] == "Acme API"
    assert data["extraction_status"] == "done"
    assert data["topics"] == ["Payments"]
    assert "modules" not in data


def test_patch_profile_partial(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-patch@example.com")
    profile = TenantProfile(
        tenant_id=client_row.id,
        product_name="Acme API",
        topics=["Payments"],
        glossary=[],
        aliases=[],
        support_email="help@acme.com",
        support_urls=["https://acme.com/docs"],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile)
    db_session.commit()

    resp = tenant.patch(
        f"{_kbase(client_row)}/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"product_name": "Acme API v2"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["product_name"] == "Acme API v2"
    assert data["topics"] == ["Payments"]


def test_get_faq_filters(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-faq@example.com")
    db_session.add_all(
        [
            TenantFaq(
                tenant_id=client_row.id,
                question="Q1",
                answer="A1",
                approved=False,
                source="docs",
            ),
            TenantFaq(
                tenant_id=client_row.id,
                question="Q2",
                answer="A2",
                approved=True,
                source="logs",
            ),
        ]
    )
    db_session.commit()

    resp = tenant.get(
        f"{_kbase(client_row)}/faq?approved=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["approved"] is False
    assert data["pending_count"] == 1


def test_approve_all_count(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-approve-all@example.com")
    db_session.add_all(
        [
            TenantFaq(
                tenant_id=client_row.id,
                question="Q1",
                answer="A1",
                approved=False,
                source="docs",
            ),
            TenantFaq(
                tenant_id=client_row.id,
                question="Q2",
                answer="A2",
                approved=False,
                source="docs",
            ),
        ]
    )
    db_session.commit()

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_count"] == 2


def test_edit_resets_approved(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-edit@example.com")
    faq = TenantFaq(
        tenant_id=client_row.id,
        question="How?",
        answer="Like this.",
        approved=True,
        source="docs",
    )
    db_session.add(faq)
    db_session.commit()
    db_session.refresh(faq)

    resp = tenant.put(
        f"{_kbase(client_row)}/faq/{faq.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "How exactly?", "answer": "Like this."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is False


def test_edit_answer_only_resets_approved(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-edit-answer@example.com")
    faq = TenantFaq(
        tenant_id=client_row.id,
        question="How exactly?",
        answer="Old answer",
        approved=True,
        source="docs",
        question_embedding=[0.1] * 1536,
    )
    db_session.add(faq)
    db_session.commit()
    db_session.refresh(faq)

    resp = tenant.put(
        f"{_kbase(client_row)}/faq/{faq.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "How exactly?", "answer": "New answer"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is False


def test_approve_all_generates_embedding_for_missing(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-approve-all-embed@example.com")
    faq = TenantFaq(
        tenant_id=client_row.id,
        question="How retries work?",
        answer="They retry up to 5 times.",
        approved=False,
        source="docs",
        question_embedding=None,
    )
    db_session.add(faq)
    db_session.commit()
    db_session.refresh(faq)

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(faq)
    assert faq.approved is True
    assert faq.question_embedding is not None


def test_approve_generates_embedding(tenant: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(tenant, db_session, email="kapi-approve-embed@example.com")
    faq = TenantFaq(
        tenant_id=client_row.id,
        question="Webhook retries?",
        answer="Up to 5 times",
        approved=False,
        source="docs",
        question_embedding=None,
    )
    db_session.add(faq)
    db_session.commit()
    db_session.refresh(faq)

    resp = tenant.post(
        f"{_kbase(client_row)}/faq/{faq.id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(faq)
    assert faq.approved is True
    assert faq.question_embedding is not None


def test_tenant_isolation(tenant: TestClient, db_session: Session) -> None:
    token1, client1 = _create_client(tenant, db_session, email="kapi-owner-1@example.com")
    token2, client2 = _create_client(tenant, db_session, email="kapi-owner-2@example.com")

    profile1 = TenantProfile(
        tenant_id=client1.id,
        product_name="Tenant One Product",
        topics=[],
        glossary=[],
        aliases=[],
        support_urls=[],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile1)
    db_session.commit()

    resp1 = tenant.get("/api/v1/knowledge/profile", headers={"Authorization": f"Bearer {token1}"})
    resp2 = tenant.get("/api/v1/knowledge/profile", headers={"Authorization": f"Bearer {token2}"})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["product_name"] == "Tenant One Product"
    assert resp2.json()["product_name"] != "Tenant One Product"
