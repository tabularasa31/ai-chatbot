"""Tests for Bot CRUD API."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Bot, Tenant
from tests.conftest import register_and_verify_user


def _auth(client: TestClient, db: Session, email: str = "bot-owner@example.com") -> tuple[str, uuid.UUID]:
    token = register_and_verify_user(client, db, email=email)
    resp = client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot Test Tenant"},
    )
    assert resp.status_code == 201
    tenant_id = uuid.UUID(resp.json()["id"])
    return token, tenant_id


def test_list_bots_returns_default_bot(tenant: TestClient, db_session: Session) -> None:
    """create_tenant auto-creates one default bot; list returns it immediately."""
    token, _ = _auth(tenant, db_session, "list-bots@example.com")

    resp = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Bot Test Tenant"


def test_create_and_get_bot(tenant: TestClient, db_session: Session) -> None:
    token, tenant_id = _auth(tenant, db_session, "create-bot@example.com")

    create_resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Support Bot"},
    )
    assert create_resp.status_code == 201
    bot = create_resp.json()
    assert bot["name"] == "My Support Bot"
    assert bot["is_active"] is True
    assert "public_id" in bot
    assert len(bot["public_id"]) == 21

    get_resp = tenant.get(
        f"/bots/{bot['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == bot["id"]


def test_update_bot(tenant: TestClient, db_session: Session) -> None:
    token, _ = _auth(tenant, db_session, "update-bot@example.com")

    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Old Name"},
    ).json()["id"]

    patch_resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "New Name", "is_active": False},
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["name"] == "New Name"
    assert updated["is_active"] is False


def test_delete_bot_blocked_when_last(tenant: TestClient, db_session: Session) -> None:
    """Deleting the only bot (the auto-created default) should return 409."""
    token, _ = _auth(tenant, db_session, "del-bot@example.com")

    bot_id = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["id"]

    del_resp = tenant.delete(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 409


def test_delete_bot_allowed_when_multiple(tenant: TestClient, db_session: Session) -> None:
    token, tenant_id = _auth(tenant, db_session, "del-multi-bot@example.com")

    bot1_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot 1"},
    ).json()["id"]

    tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot 2"},
    )

    del_resp = tenant.delete(
        f"/bots/{bot1_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 204


def test_bot_not_accessible_by_other_tenant(tenant: TestClient, db_session: Session) -> None:
    token1, _ = _auth(tenant, db_session, "tenant-a@example.com")
    token2, _ = _auth(tenant, db_session, "tenant-b@example.com")

    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token1}"},
        json={"name": "Private Bot"},
    ).json()["id"]

    resp = tenant.get(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert resp.status_code == 404


def test_bot_public_id_is_unique(tenant: TestClient, db_session: Session) -> None:
    token, _ = _auth(tenant, db_session, "uniq-bot@example.com")

    ids = set()
    for i in range(5):
        bot = tenant.post(
            "/bots",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": f"Bot {i}"},
        ).json()
        ids.add(bot["public_id"])

    assert len(ids) == 5
