"""Tests for Bot CRUD API."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key


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


def test_update_bot_emits_settings_updated_for_changed_fields_only(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.bots.events.capture_event",
        lambda event, **kwargs: events.append({"event": event, **kwargs}),
    )

    token, _ = _auth(tenant, db_session, "settings-updated@example.com")
    bot = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]
    bot_id = bot["id"]

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Renamed Bot",
            "link_safety_enabled": True,
            "is_active": bot["is_active"],
        },
    )
    assert resp.status_code == 200

    assert len(events) == 1
    event = events[0]
    assert event["event"] == "bot.settings_updated"
    assert event["distinct_id"] == bot["public_id"]
    assert event["bot_id"] == bot["public_id"]
    assert event["properties"] == {
        "changed_fields": ["link_safety_enabled", "name"],
        "changed_count": 2,
    }

    properties_text = str(event["properties"])
    assert "Renamed Bot" not in properties_text


def test_update_bot_emits_nothing_when_nothing_changes(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.bots.events.capture_event",
        lambda event, **kwargs: events.append({"event": event, **kwargs}),
    )

    token, _ = _auth(tenant, db_session, "settings-unchanged@example.com")
    bot = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]
    bot_id = bot["id"]

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": bot["name"], "is_active": bot["is_active"]},
    )
    assert resp.status_code == 200
    assert events == []


def test_put_bot_disclosure_emits_settings_updated_for_changed_level(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.bots.events.capture_event",
        lambda event, **kwargs: events.append({"event": event, **kwargs}),
    )

    token = register_and_verify_user(tenant, db_session, email="disclosure-event@example.com")
    tenant_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Disclosure Event Tenant"},
    )
    assert tenant_resp.status_code == 201
    tenant_public_id = tenant_resp.json()["public_id"]

    bot = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]

    resp = tenant.put(
        f"/bots/{bot['id']}/disclosure",
        headers={"Authorization": f"Bearer {token}"},
        json={"level": "corporate"},
    )
    assert resp.status_code == 200

    assert len(events) == 1
    event = events[0]
    assert event["event"] == "bot.settings_updated"
    assert event["distinct_id"] == bot["public_id"]
    assert event["tenant_id"] == tenant_public_id
    assert event["bot_id"] == bot["public_id"]
    assert event["groups"] == {"tenant": tenant_public_id}
    assert event["properties"] == {
        "changed_fields": ["disclosure_level"],
        "changed_count": 1,
    }

    events.clear()
    resp2 = tenant.put(
        f"/bots/{bot['id']}/disclosure",
        headers={"Authorization": f"Bearer {token}"},
        json={"level": "corporate"},
    )
    assert resp2.status_code == 200
    assert events == []


def test_create_bot_emits_no_bot_created_event(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.bots.events.capture_event",
        lambda event, **kwargs: events.append({"event": event, **kwargs}),
    )

    token, _ = _auth(tenant, db_session, "create-no-event@example.com")

    resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "New Bot"},
    )
    assert resp.status_code == 201
    assert events == []


def test_create_bot_without_instructions_defaults_to_support_agent_preset(
    tenant: TestClient, db_session: Session
) -> None:
    from backend.chat.presets import PRESET_SUPPORT_AGENT

    token, _ = _auth(tenant, db_session, "create-preset@example.com")

    bot = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Preset Bot"},
    ).json()

    assert bot["preset"] == "support_agent"
    assert bot["custom_instructions"] is None
    assert bot["instructions_source"] == "preset"
    assert bot["effective_instructions"] == PRESET_SUPPORT_AGENT


def test_create_bot_with_explicit_null_preset_stays_unset(
    tenant: TestClient, db_session: Session
) -> None:
    token, _ = _auth(tenant, db_session, "create-null-preset@example.com")

    bot = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Preset Bot", "preset": None},
    ).json()

    assert bot["preset"] is None
    assert bot["custom_instructions"] is None
    assert bot["instructions_source"] == "none"


def test_update_bot_instructions_layering(tenant: TestClient, db_session: Session) -> None:
    from backend.chat.presets import PRESET_SUPPORT_AGENT

    token, _ = _auth(tenant, db_session, "layering-bot@example.com")
    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Layered Bot"},
    ).json()["id"]

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"custom_instructions": "X"},
    )
    body = resp.json()
    assert body["instructions_source"] == "preset+custom"
    assert body["effective_instructions"].endswith("X")
    assert body["effective_instructions"].startswith(PRESET_SUPPORT_AGENT.split("\n")[0])

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"preset": None},
    )
    assert resp.json()["instructions_source"] == "custom"

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"custom_instructions": None, "preset": "support_agent"},
    )
    assert resp.json()["instructions_source"] == "preset"


def test_empty_custom_instructions_normalized(
    tenant: TestClient, db_session: Session
) -> None:
    """A whitespace-only custom_instructions PATCH clears the column instead of
    being stored as a non-null empty string."""
    token, _ = _auth(tenant, db_session, "empty-custom@example.com")
    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot", "custom_instructions": "Custom text."},
    ).json()["id"]

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"custom_instructions": ""},
    )
    body = resp.json()
    assert body["custom_instructions"] is None
    assert body["instructions_source"] == "preset"


def test_custom_instructions_too_long_rejected(tenant: TestClient, db_session: Session) -> None:
    token, _ = _auth(tenant, db_session, "too-long@example.com")

    resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot", "custom_instructions": "x" * 3001},
    )
    assert resp.status_code == 422


def test_unknown_preset_rejected(tenant: TestClient, db_session: Session) -> None:
    token, _ = _auth(tenant, db_session, "bad-preset@example.com")

    resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot", "preset": "not_a_real_preset"},
    )
    assert resp.status_code == 422


def test_onboarding_enrichment_stores_custom_instructions_not_preset_snapshot(
    tenant: TestClient, db_session: Session, monkeypatch
) -> None:
    from backend.bots import routes as bots_routes
    from backend.models import Bot

    token, _ = _auth(tenant, db_session, "onboarding-bot@example.com")
    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Onboarded Bot"},
    ).json()["id"]

    monkeypatch.setattr(
        "backend.onboarding.extractor.extract_company_description",
        lambda url, api_key: "Acme sells widgets.",
    )

    bot = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    bots_routes._enrich_bot_instructions(bot.id, bot.tenant_id, "https://acme.example", "sk-fake")

    db_session.expire_all()
    refreshed = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    assert refreshed.custom_instructions == "Acme sells widgets."
    assert refreshed.preset == "support_agent"


def test_create_with_custom_instructions_and_website_url_skips_enrichment(
    tenant: TestClient, db_session: Session, monkeypatch
) -> None:
    """A create carrying its own custom_instructions must not be overwritten
    by the onboarding-enrichment background task."""
    from backend.bots import routes as bots_routes

    token, _ = _auth(tenant, db_session, "skip-enrichment@example.com")
    set_client_openai_key(tenant, token)

    calls: list[object] = []
    monkeypatch.setattr(
        bots_routes, "_enrich_bot_instructions", lambda *a, **kw: calls.append(a)
    )

    bot = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot", "custom_instructions": "mine", "website_url": "https://acme.example"},
    ).json()

    assert calls == []
    assert bot["custom_instructions"] == "mine"


def test_enrichment_task_leaves_meanwhile_set_custom_instructions_unchanged(
    tenant: TestClient, db_session: Session, monkeypatch
) -> None:
    """If the tenant sets custom_instructions after the task was scheduled but
    before it runs, the task must not clobber it."""
    from backend.bots import routes as bots_routes
    from backend.models import Bot

    token, _ = _auth(tenant, db_session, "race-enrichment@example.com")
    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot"},
    ).json()["id"]

    tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"custom_instructions": "set by tenant", "preset": None},
    )

    monkeypatch.setattr(
        "backend.onboarding.extractor.extract_company_description",
        lambda url, api_key: "Acme sells widgets.",
    )

    bot = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    bots_routes._enrich_bot_instructions(bot.id, bot.tenant_id, "https://acme.example", "sk-fake")

    db_session.expire_all()
    refreshed = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    assert refreshed.custom_instructions == "set by tenant"
    assert refreshed.preset is None


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


def test_preset_text_reflects_bot_preset(tenant: TestClient, db_session: Session) -> None:
    from backend.chat.presets import PRESET_SUPPORT_AGENT

    token, _ = _auth(tenant, db_session, "preset-text@example.com")
    resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot"},
    )
    assert resp.status_code == 201
    bot = resp.json()
    assert bot["preset"] == "support_agent"
    assert bot["preset_text"] == PRESET_SUPPORT_AGENT

    resp = tenant.patch(
        f"/bots/{bot['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"preset": None},
    )
    assert resp.status_code == 200
    assert resp.json()["preset_text"] is None
