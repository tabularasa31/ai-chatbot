"""Tests for admin metrics API."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Chat, Document, DocumentStatus, DocumentType, Embedding, Message, MessageRole, PiiEvent, PiiEventDirection, User
from backend.scripts.cleanup_pii_events import run as cleanup_pii_events_run

from tests.conftest import register_and_verify_user


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/admin/metrics/summary", id="summary"),
        pytest.param("/admin/metrics/tenants", id="tenants"),
    ],
)
def test_admin_metrics_endpoint_requires_admin(
    tenant: TestClient, db_session: Session, path: str
) -> None:
    """Non-admin JWT → 403. Admin JWT → 200."""
    email = f"nonadmin-{path.rsplit('/', 1)[-1]}@example.com"
    token = register_and_verify_user(tenant, db_session, email=email)
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Non Admin Tenant"},
    )

    resp = tenant.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403

    user = db_session.query(User).filter(User.email == email).first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = tenant.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_admin_metrics_summary_and_tenants_reflect_fixtures(
    tenant: TestClient, db_session: Session
) -> None:
    """Summary totals and the per-tenant row both match created fixtures;
    users_count reflects every user with the tenant_id, not just the owner."""
    token = register_and_verify_user(tenant, db_session, email="admin@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Admin Tenant"},
    )
    cl_data = cl_resp.json()
    tenant_id = uuid.UUID(cl_data["id"])
    public_id = cl_data["public_id"]

    user = db_session.query(User).filter(User.email == "admin@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    from backend.auth.service import hash_password

    member = User(
        email="member@example.com",
        password_hash=hash_password("SecurePass1!"),
        tenant_id=tenant_id,
    )
    db_session.add(member)
    db_session.commit()

    doc = Document(
        tenant_id=tenant_id,
        filename="test.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    emb = Embedding(
        document_id=doc.id,
        chunk_text="chunk",
        metadata_json={},
    )
    db_session.add(emb)
    db_session.commit()

    sess_id = uuid.uuid4()
    chat = Chat(tenant_id=tenant_id, session_id=sess_id)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    chat.tokens_used = 250
    db_session.commit()

    msg1 = Message(chat_id=chat.id, role=MessageRole.user, content="Hi")
    msg2 = Message(chat_id=chat.id, role=MessageRole.assistant, content="Hello")
    db_session.add_all([msg1, msg2])
    db_session.commit()

    summary_resp = tenant.get(
        "/admin/metrics/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert summary_resp.status_code == 200
    data = summary_resp.json()
    assert data["total_users"] >= 1
    assert data["total_tenants"] >= 1
    assert data["active_tenants"] >= 1
    assert data["total_documents"] >= 1
    assert data["total_chat_sessions"] >= 1
    assert data["total_messages_user"] >= 1
    assert data["total_messages_assistant"] >= 1
    assert data["total_tokens_chat"] == 250

    tenants_resp = tenant.get(
        "/admin/metrics/tenants",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert tenants_resp.status_code == 200
    items = tenants_resp.json()["items"]
    our = next((i for i in items if i["tenant_id"] == str(tenant_id)), None)
    assert our is not None
    assert our["public_id"] == public_id
    assert our["owner_email"] == "admin@example.com"
    assert our["has_openai_key"] is False
    assert our["users_count"] == 2, "must count the member, not just the owner"
    assert our["documents_count"] >= 1
    assert our["embedded_documents_count"] >= 1
    assert our["chat_sessions_count"] >= 1
    assert our["messages_user_count"] >= 1
    assert our["messages_assistant_count"] >= 1
    assert our["tokens_used_chat"] == 250


def test_clients_me_includes_is_admin(tenant: TestClient, db_session: Session) -> None:
    """GET /tenants/me returns is_admin from user."""
    token = register_and_verify_user(tenant, db_session, email="meadmin@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Me Admin Tenant"},
    )

    resp = tenant.get("/tenants/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert "is_admin" in data
    assert data["is_admin"] is False

    user = db_session.query(User).filter(User.email == "meadmin@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = tenant.get("/tenants/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_admin"] is True


def test_admin_pii_events_list_and_cleanup(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(tenant, db_session, email="pii-admin@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "PII Admin Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    user = db_session.query(User).filter(User.email == "pii-admin@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    old_event = PiiEvent(
        tenant_id=tenant_id,
        actor_user_id=user.id,
        direction=PiiEventDirection.llm_request,
        entity_type="EMAIL",
        count=1,
        action_path="/chat/logs/session/demo",
        created_at=datetime.now(timezone.utc) - timedelta(days=400),
    )
    fresh_event = PiiEvent(
        tenant_id=tenant_id,
        actor_user_id=user.id,
        direction=PiiEventDirection.notification_email,
        entity_type="PHONE",
        count=1,
        action_path="/escalations/demo/notify",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add_all([old_event, fresh_event])
    db_session.commit()
    old_event_id = str(old_event.id)
    fresh_event_id = str(fresh_event.id)

    list_resp = tenant.get(
        "/admin/privacy/pii-events?direction=notification_email",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["direction"] == "notification_email"
    assert items[0]["actor_user_id"] == str(user.id)

    cleanup_resp = tenant.delete(
        "/admin/privacy/pii-events/retention?retention_days=365",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert cleanup_resp.status_code == 200
    assert cleanup_resp.json()["deleted_count"] >= 1

    remaining_ids = {str(row.id) for row in db_session.query(PiiEvent).all()}
    assert old_event_id not in remaining_ids
    assert fresh_event_id in remaining_ids


@pytest.mark.parametrize(
    ("method", "url"),
    [
        pytest.param("get", "/admin/privacy/pii-events?limit=-1", id="negative_limit"),
        pytest.param("get", "/admin/privacy/pii-events?offset=-1", id="negative_offset"),
        pytest.param("get", "/admin/privacy/pii-events?since_days=0", id="zero_since_days"),
        pytest.param(
            "delete",
            "/admin/privacy/pii-events/retention?retention_days=0",
            id="zero_retention_days",
        ),
    ],
)
def test_admin_pii_events_reject_invalid_query_params(
    tenant: TestClient,
    db_session: Session,
    method: str,
    url: str,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="pii-params@example.com")
    tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "PII Params Tenant"},
    )

    user = db_session.query(User).filter(User.email == "pii-params@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.commit()

    resp = getattr(tenant, method)(url, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_cleanup_pii_events_script_rejects_zero_retention() -> None:
    with pytest.raises(ValueError, match="retention_days must be >= 1"):
        cleanup_pii_events_run(0)
