"""tenant.created: fired once, from one place, on every creation path.

The frontend never calls ``POST /tenants`` — the live path is e-mail
verification, which calls ``ensure_tenant_for_user`` -> ``create_tenant``.
These tests pin that the emit lives in ``create_tenant`` itself, so both
callers get exactly one ``tenant.created`` and one ``group_identify``, and
an already-provisioned user gets neither.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Tenant, User
from backend.tenants.service import ensure_tenant_for_user
from tests.conftest import register_and_verify_user


@pytest.fixture
def tenant_events(monkeypatch) -> dict[str, list[dict]]:
    """Every ``tenant.created`` / ``group_identify`` call made through the
    tenants service during the test, in order."""
    captured: list[dict] = []
    identified: list[dict] = []

    def fake_capture(event, **kwargs):
        captured.append({"event": event, **kwargs})

    def fake_group_identify(group_type, group_key, properties=None):
        identified.append(
            {"group_type": group_type, "group_key": group_key, "properties": properties}
        )

    monkeypatch.setattr("backend.tenants.service.capture_event", fake_capture)
    monkeypatch.setattr("backend.tenants.service.group_identify", fake_group_identify)
    return {"captured": captured, "identified": identified}


def _public_id(db: Session, tenant_id: uuid.UUID) -> str:
    return str(db.query(Tenant).filter(Tenant.id == tenant_id).one().public_id)


def test_ensure_tenant_for_user_emits_once(
    tenant: TestClient, db_session: Session, tenant_events: dict[str, list[dict]]
) -> None:
    token = register_and_verify_user(db_session=db_session, test_client=tenant, email="ensure1@example.com")
    user = db_session.query(User).filter(User.email == "ensure1@example.com").one()

    result = ensure_tenant_for_user(user.id, db_session, name="Ensure Co")

    public_id = _public_id(db_session, result.id)
    created = tenant_events["captured"]
    assert len(created) == 1, created
    assert created[0]["event"] == "tenant.created"
    assert created[0]["distinct_id"] == public_id
    assert created[0]["tenant_id"] == public_id
    assert created[0]["groups"] == {"tenant": public_id}

    identified = tenant_events["identified"]
    assert len(identified) == 1, identified
    assert identified[0]["group_type"] == "tenant"
    assert identified[0]["group_key"] == public_id
    assert identified[0]["properties"] == {"name": "Ensure Co"}
    assert token  # sanity: user was actually registered


def test_ensure_tenant_for_user_existing_tenant_emits_nothing(
    tenant: TestClient, db_session: Session, tenant_events: dict[str, list[dict]]
) -> None:
    register_and_verify_user(db_session=db_session, test_client=tenant, email="ensure2@example.com")
    user = db_session.query(User).filter(User.email == "ensure2@example.com").one()

    ensure_tenant_for_user(user.id, db_session, name="Ensure Co")
    assert len(tenant_events["captured"]) == 1

    ensure_tenant_for_user(user.id, db_session, name="Ensure Co")
    assert len(tenant_events["captured"]) == 1
    assert len(tenant_events["identified"]) == 1


def test_create_tenant_route_emits_once(
    tenant: TestClient, db_session: Session, tenant_events: dict[str, list[dict]]
) -> None:
    token = register_and_verify_user(db_session=db_session, test_client=tenant, email="route1@example.com")
    resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Route Co"},
    )
    assert resp.status_code == 201, resp.text

    public_id = resp.json()["public_id"]
    created = tenant_events["captured"]
    assert len(created) == 1, created
    assert created[0]["event"] == "tenant.created"
    assert created[0]["distinct_id"] == public_id
    assert created[0]["tenant_id"] == public_id
    assert created[0]["groups"] == {"tenant": public_id}
    assert len(tenant_events["identified"]) == 1
