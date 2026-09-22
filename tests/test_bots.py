"""Tests for Bot CRUD API."""

from __future__ import annotations

import uuid

import pytest
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


def _first_bot_id(client: TestClient, token: str) -> str:
    return client.get("/bots", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["id"]


def test_bot_crud_journey(tenant: TestClient, db_session: Session) -> None:
    """create_tenant auto-creates a default bot; create/get/update a new bot;
    public_id is 21 chars and unique across bots; preset_text mirrors preset."""
    from backend.chat.presets import PRESET_SUPPORT_AGENT

    token, _ = _auth(tenant, db_session, "crud-bot@example.com")

    list_resp = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    default_items = list_resp.json()["items"]
    assert len(default_items) == 1
    assert default_items[0]["name"] == "Bot Test Tenant"
    assert default_items[0]["link_safety_enabled"] is False
    assert default_items[0]["allowed_domains"] == []

    create_resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "My Support Bot"},
    )
    assert create_resp.status_code == 201
    bot = create_resp.json()
    assert bot["name"] == "My Support Bot"
    assert bot["is_active"] is True
    assert len(bot["public_id"]) == 21
    assert bot["preset"] == "support_agent"
    assert bot["preset_text"] == PRESET_SUPPORT_AGENT

    get_resp = tenant.get(f"/bots/{bot['id']}", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == bot["id"]

    patch_resp = tenant.patch(
        f"/bots/{bot['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "New Name", "is_active": False, "preset": None},
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["name"] == "New Name"
    assert updated["is_active"] is False
    assert updated["preset_text"] is None

    ids = {bot["public_id"]}
    for i in range(4):
        extra = tenant.post(
            "/bots",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": f"Bot {i}"},
        ).json()
        ids.add(extra["public_id"])
    assert len(ids) == 5


def test_update_bot_link_safety_normalizes_allowed_domains(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, _ = _auth(tenant, db_session, "link-safety-bot@example.com")
    bot_id = _first_bot_id(tenant, token)

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


def test_bot_not_accessible_by_other_tenant(tenant: TestClient, db_session: Session) -> None:
    """Authz boundary: a tenant cannot fetch another tenant's bot by id."""
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


@pytest.mark.parametrize(
    "payload,expected_status,expected_fields",
    [
        pytest.param(
            {"name": "Preset Bot"},
            201,
            {"preset": "support_agent", "custom_instructions": None, "instructions_source": "preset"},
            id="default_preset_applied",
        ),
        pytest.param(
            {"name": "No Preset Bot", "preset": None},
            201,
            {"preset": None, "custom_instructions": None, "instructions_source": "none"},
            id="explicit_null_preset_stays_unset",
        ),
        pytest.param(
            {"name": "Bot", "custom_instructions": "x" * 3001},
            422,
            None,
            id="custom_instructions_too_long_rejected",
        ),
        pytest.param(
            {"name": "Bot", "preset": "not_a_real_preset"},
            422,
            None,
            id="unknown_preset_rejected",
        ),
    ],
)
def test_create_bot_preset_variants(
    tenant: TestClient,
    db_session: Session,
    payload: dict,
    expected_status: int,
    expected_fields: dict | None,
) -> None:
    slug = payload["name"].lower().replace(" ", "-")
    token, _ = _auth(tenant, db_session, f"preset-{expected_status}-{slug}@example.com")

    resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.status_code == expected_status
    if expected_fields is not None:
        body = resp.json()
        for key, value in expected_fields.items():
            assert body[key] == value


def test_bot_instructions_layering_journey(tenant: TestClient, db_session: Session) -> None:
    """preset+custom layering, and a whitespace-only custom_instructions PATCH
    normalizes to NULL instead of being stored as an empty string."""
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

    resp = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"custom_instructions": ""},
    )
    body = resp.json()
    assert body["custom_instructions"] is None
    assert body["instructions_source"] == "preset"


def test_bot_settings_update_events_journey(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    """bot.settings_updated fires only for actually-changed fields, carries
    a changed_fields diff (not the values), and creation emits nothing."""
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.bots.events.capture_event",
        lambda event, **kwargs: events.append({"event": event, **kwargs}),
    )

    token, _ = _auth(tenant, db_session, "settings-updated@example.com")

    create_resp = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "New Bot"},
    )
    assert create_resp.status_code == 201
    assert events == []

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
    assert "Renamed Bot" not in str(event["properties"])

    events.clear()
    resp2 = tenant.patch(
        f"/bots/{bot_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Renamed Bot", "is_active": bot["is_active"]},
    )
    assert resp2.status_code == 200
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


@pytest.mark.parametrize(
    "preset_custom_instructions,expected_custom_instructions,expected_preset",
    [
        pytest.param(None, "Acme sells widgets.", "support_agent", id="enrichment_stores_extracted_description"),
        pytest.param("set by tenant", "set by tenant", None, id="enrichment_leaves_meanwhile_set_instructions_unchanged"),
    ],
)
def test_onboarding_enrichment_precedence(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
    preset_custom_instructions: str | None,
    expected_custom_instructions: str,
    expected_preset: str | None,
) -> None:
    from backend.bots import routes as bots_routes
    from backend.models import Bot

    token, _ = _auth(tenant, db_session, f"onboarding-{expected_preset}@example.com")
    bot_id = tenant.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Onboarded Bot"},
    ).json()["id"]

    if preset_custom_instructions is not None:
        tenant.patch(
            f"/bots/{bot_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"custom_instructions": preset_custom_instructions, "preset": None},
        )

    monkeypatch.setattr(
        "backend.onboarding.extractor.extract_company_description",
        lambda url, api_key: "Acme sells widgets.",
    )

    bot = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    bots_routes._enrich_bot_instructions(bot.id, bot.tenant_id, "https://acme.example", "sk-fake")

    db_session.expire_all()
    refreshed = db_session.query(Bot).filter(Bot.id == uuid.UUID(bot_id)).first()
    assert refreshed.custom_instructions == expected_custom_instructions
    assert refreshed.preset == expected_preset


@pytest.mark.parametrize(
    "action,multiple_bots,expected_status",
    [
        pytest.param("delete", False, 409, id="delete_blocked_when_last"),
        pytest.param("delete", True, 204, id="delete_allowed_when_multiple"),
        pytest.param("deactivate", False, 409, id="deactivate_blocked_when_last_active"),
        pytest.param("deactivate", True, 200, id="deactivate_allowed_when_another_active"),
    ],
)
def test_bot_lifecycle_guard_last_active(
    tenant: TestClient,
    db_session: Session,
    action: str,
    multiple_bots: bool,
    expected_status: int,
) -> None:
    """Deleting or deactivating the only (active) bot is blocked with 409;
    it's allowed once another bot exists to take its place."""
    token, _ = _auth(tenant, db_session, f"guard-{action}-{multiple_bots}@example.com")
    first_id = _first_bot_id(tenant, token)

    if multiple_bots:
        tenant.post(
            "/bots",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Second Bot"},
        )

    if action == "delete":
        resp = tenant.delete(f"/bots/{first_id}", headers={"Authorization": f"Bearer {token}"})
    else:
        resp = tenant.patch(
            f"/bots/{first_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_active": False},
        )
    assert resp.status_code == expected_status
