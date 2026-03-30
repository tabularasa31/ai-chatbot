from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Client, TenantFaq, TenantProfile
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client(http: TestClient, db: Session, *, email: str) -> tuple[str, Client]:
    token = register_and_verify_user(http, db, email=email)
    resp = http.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Knowledge API Client"},
    )
    assert resp.status_code in (200, 201), resp.text
    set_client_openai_key(http, token)
    client_row = db.get(Client, uuid.UUID(resp.json()["id"]))
    assert client_row is not None
    return token, client_row


def test_get_profile(client: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(client, db_session, email="kapi-profile@example.com")
    profile = TenantProfile(
        tenant_id=client_row.id,
        product_name="Acme API",
        modules=["Payments"],
        glossary=[],
        aliases=[],
        support_email="help@acme.com",
        support_urls=["https://acme.com/docs"],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile)
    db_session.commit()

    resp = client.get("/knowledge/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["product_name"] == "Acme API"
    assert data["extraction_status"] == "done"


def test_patch_profile_partial(client: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(client, db_session, email="kapi-patch@example.com")
    profile = TenantProfile(
        tenant_id=client_row.id,
        product_name="Acme API",
        modules=["Payments"],
        glossary=[],
        aliases=[],
        support_email="help@acme.com",
        support_urls=["https://acme.com/docs"],
        extraction_status="done",
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(profile)
    db_session.commit()

    resp = client.patch(
        "/knowledge/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"product_name": "Acme API v2"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["product_name"] == "Acme API v2"
    assert data["modules"] == ["Payments"]


def test_get_faq_filters(client: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(client, db_session, email="kapi-faq@example.com")
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

    resp = client.get(
        "/knowledge/faq?approved=false",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["approved"] is False
    assert data["pending_count"] == 1


def test_approve_all_count(client: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(client, db_session, email="kapi-approve-all@example.com")
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

    resp = client.post(
        "/knowledge/faq/approve-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_count"] == 2


def test_edit_resets_approved(client: TestClient, db_session: Session) -> None:
    token, client_row = _create_client(client, db_session, email="kapi-edit@example.com")
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

    resp = client.put(
        f"/knowledge/faq/{faq.id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "How exactly?", "answer": "Like this."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] is False

