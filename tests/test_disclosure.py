"""Disclosure controls: bot-level level + API."""

from __future__ import annotations

import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.disclosure_config import resolve_level
from backend.models import Bot
from tests.conftest import register_and_verify_user


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(None, "standard", id="none_falls_back_to_standard"),
        pytest.param({}, "standard", id="empty_dict_falls_back_to_standard"),
        pytest.param({"level": "corporate"}, "corporate", id="valid_level_wins"),
        pytest.param({"level": "nope"}, "standard", id="invalid_level_falls_back"),
    ],
)
def test_resolve_level(raw: dict | None, expected: str) -> None:
    assert resolve_level(raw) == expected


def _setup(tenant: TestClient, db_session: Session, email: str, name: str) -> tuple[str, str]:
    """Register user, create tenant (auto-creates default bot), return (token, bot_id)."""
    token = register_and_verify_user(tenant, db_session, email=email)
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert cr.status_code == 201
    bots_r = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"})
    assert bots_r.status_code == 200
    bot_id = bots_r.json()["items"][0]["id"]
    return token, bot_id


def test_disclosure_level_lifecycle_through_the_api(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """One bot's disclosure level through default, round-trip, DB drift, and rejection.

    Guards: GET with no config stored defaults to standard; PUT persists and
    round-trips through GET; an unsupported/legacy key stored directly in the
    DB is ignored rather than surfaced; PUT rejects a level outside the
    allowed set with 422 (does not silently coerce it).
    """
    token, bot_id = _setup(tenant, db_session, "disc-lifecycle@example.com", "Disc Tenant")
    headers = {"Authorization": f"Bearer {token}"}

    r = tenant.get(f"/bots/{bot_id}/disclosure", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"level": "standard"}

    r = tenant.put(f"/bots/{bot_id}/disclosure", headers=headers, json={"level": "corporate"})
    assert r.status_code == 200
    assert r.json() == {"level": "corporate"}
    r = tenant.get(f"/bots/{bot_id}/disclosure", headers=headers)
    assert r.json() == {"level": "corporate"}

    bot = db_session.query(Bot).filter(Bot.id == _uuid.UUID(bot_id)).first()
    assert bot is not None
    bot.disclosure_config = {"legacy_level": "detailed"}
    db_session.commit()
    r = tenant.get(f"/bots/{bot_id}/disclosure", headers=headers)
    assert r.status_code == 200
    assert r.json() == {"level": "standard"}

    r = tenant.put(f"/bots/{bot_id}/disclosure", headers=headers, json={"level": "mega"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    ("disclosure_config", "expected"),
    [
        pytest.param(
            {"level": "corporate"},
            "[Response level: corporate]",
            id="corporate_level_instruction",
        ),
        pytest.param(None, "[Response level: standard]", id="none_equals_standard_block"),
    ],
)
def test_build_rag_prompt_disclosure_block(disclosure_config: dict | None, expected: str) -> None:
    from backend.chat.prompts import build_rag_prompt

    p = build_rag_prompt("Q?", ["c"], disclosure_config=disclosure_config)
    assert expected in p
    if disclosure_config is not None:
        assert "non-technical" in p.lower() or "polished" in p.lower()
        assert "Hard limits" in p
