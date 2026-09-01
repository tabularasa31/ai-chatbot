"""Tests for Bot CRUD API."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

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
    assert data["items"][0]["link_safety_enabled"] is False
    assert data["items"][0]["allowed_domains"] == []


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


def test_update_bot_link_safety_normalizes_allowed_domains(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, _ = _auth(tenant, db_session, "link-safety-bot@example.com")

    bot_id = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["id"]

    patch_resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "link_safety_enabled": True,
            "allowed_domains": [
                "https://Example.com/docs",
                "*.help.example.com",
                "example.com",
                "invalid",
            ],
        },
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["link_safety_enabled"] is True
    assert updated["allowed_domains"] == ["example.com", "help.example.com"]


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


def test_deactivate_last_active_bot_blocked(tenant: TestClient, db_session: Session) -> None:
    """Deactivating the only active bot should return 409."""
    token, _ = _auth(tenant, db_session, "deact-last@example.com")
    bot_id = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["id"]

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 409


def test_deactivate_bot_allowed_when_another_active(tenant: TestClient, db_session: Session) -> None:
    token, _ = _auth(tenant, db_session, "deact-ok@example.com")
    tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Bot"},
    ).json()["id"]

    first_id = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["id"]
    resp = tenant.patch(
        f"/bots/{first_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


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


def _instructions_bot(session_local, instructions: str | None) -> uuid.UUID:
    from backend.models import Bot, Tenant

    with session_local() as db:
        tenant = Tenant(name="Refresh Tenant")
        db.add(tenant)
        db.flush()
        bot = Bot(tenant_id=tenant.id, name="Refresh Bot", agent_instructions=instructions)
        db.add(bot)
        db.commit()
        return bot.id


def test_refresh_keeps_text_written_around_the_preset_block() -> None:
    from backend.chat.presets import PRESET_SUPPORT_AGENT
    from scripts.refresh_bot_instructions import REFRESHED, _PRESET_GEN_2, plan_refresh

    description = "Acme ships industrial widgets to 40 countries."
    owner_rules = "Refund window is 14 days. Never mention competitors."
    stored = f"{description}\n\n{_PRESET_GEN_2.strip()}\n\n{owner_rules}"

    value, outcome = plan_refresh(stored, force=False)

    assert outcome == REFRESHED
    assert value == f"{description}\n\n{PRESET_SUPPORT_AGENT.strip()}\n\n{owner_rules}"


def test_refresh_replaces_the_oldest_preset_generation_whole() -> None:
    from scripts.refresh_bot_instructions import REFRESHED, _PRESET_GEN_1, plan_refresh

    value, outcome = plan_refresh(_PRESET_GEN_1, force=False)

    assert outcome == REFRESHED
    assert "Follow the internal reasoning steps" not in value
    assert "Keep it concise" not in value


def test_refresh_leaves_cleared_and_customized_instructions_alone() -> None:
    from backend.chat.presets import PRESET_SUPPORT_AGENT
    from scripts.refresh_bot_instructions import CLEARED, CURRENT, CUSTOMIZED, plan_refresh

    assert plan_refresh(None, force=False) == (None, CLEARED)
    assert plan_refresh("   ", force=False) == (None, CLEARED)
    assert plan_refresh("Always answer in haiku.", force=False) == (None, CUSTOMIZED)
    assert plan_refresh(PRESET_SUPPORT_AGENT, force=False) == (None, CURRENT)


def test_force_rewrites_an_edited_preset_but_keeps_the_description() -> None:
    from backend.chat.presets import PRESET_SUPPORT_AGENT
    from scripts.refresh_bot_instructions import OVERWRITTEN, plan_refresh

    description = "Acme ships industrial widgets to 40 countries."
    edited = f"{description}\n\nYou are a support assistant for {{product_name}}. Reworded by the owner."

    value, outcome = plan_refresh(edited, force=True)

    assert outcome == OVERWRITTEN
    assert value == f"{description}\n\n{PRESET_SUPPORT_AGENT.strip()}"


def test_run_refresh_dry_run_reports_without_writing(engine) -> None:
    from sqlalchemy.orm import sessionmaker

    from backend.models import Bot
    from scripts.refresh_bot_instructions import _PRESET_GEN_2, run_refresh

    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _instructions_bot(session_local, _PRESET_GEN_2)

    stats = run_refresh(dry_run=True, session_factory=session_local)

    assert stats.refreshed == 1
    with session_local() as verify:
        assert verify.get(Bot, bot_id).agent_instructions == _PRESET_GEN_2


def test_run_refresh_writes_once_and_then_reports_current(engine) -> None:
    from sqlalchemy.orm import sessionmaker

    from backend.chat.presets import PRESET_SUPPORT_AGENT
    from backend.models import Bot
    from scripts.refresh_bot_instructions import _PRESET_GEN_2, run_refresh

    session_local = sessionmaker(bind=engine, class_=Session, future=True)
    bot_id = _instructions_bot(session_local, _PRESET_GEN_2)

    first = run_refresh(dry_run=False, session_factory=session_local)
    second = run_refresh(dry_run=False, session_factory=session_local)

    assert (first.refreshed, second.refreshed) == (1, 0)
    assert second.current == 1
    with session_local() as verify:
        assert verify.get(Bot, bot_id).agent_instructions == PRESET_SUPPORT_AGENT


def test_dashboard_preset_matches_the_backend_preset() -> None:
    """The settings page ships its own copy; a drifted copy writes the old text back."""
    from pathlib import Path

    from backend.chat.presets import PRESET_SUPPORT_AGENT

    repo_root = Path(__file__).resolve().parents[1]
    page = (repo_root / "frontend/app/(app)/settings/page.tsx").read_text(encoding="utf-8")
    start = page.index("content: `") + len("content: `")
    shipped = page[start : page.index("`,", start)]

    assert shipped == PRESET_SUPPORT_AGENT.strip()
