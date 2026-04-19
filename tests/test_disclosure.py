"""Disclosure controls: bot-level level + API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import uuid as _uuid

from backend.disclosure_config import public_config_dict, resolve_level
from backend.models import Bot
from tests.conftest import register_and_verify_user


def test_resolve_level_none() -> None:
    assert resolve_level(None) == "standard"


def test_resolve_level_empty_dict() -> None:
    assert resolve_level({}) == "standard"


def test_resolve_level_primary_key() -> None:
    assert resolve_level({"level": "corporate"}) == "corporate"


def test_resolve_level_invalid_falls_back() -> None:
    assert resolve_level({"level": "nope"}) == "standard"


def test_public_config_dict() -> None:
    assert public_config_dict({"level": "corporate"}) == {"level": "corporate"}


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


def test_get_disclosure_defaults(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, bot_id = _setup(tenant, db_session, "disc-get@example.com", "Disc Tenant")
    r = tenant.get(f"/bots/{bot_id}/disclosure", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"level": "standard"}


def test_put_and_get_disclosure(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, bot_id = _setup(tenant, db_session, "disc-put@example.com", "Disc Put")
    r = tenant.put(
        f"/bots/{bot_id}/disclosure",
        headers={"Authorization": f"Bearer {token}"},
        json={"level": "corporate"},
    )
    assert r.status_code == 200
    assert r.json() == {"level": "corporate"}
    r2 = tenant.get(f"/bots/{bot_id}/disclosure", headers={"Authorization": f"Bearer {token}"})
    assert r2.json() == {"level": "corporate"}


def test_get_disclosure_ignores_unsupported_keys_in_db(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, bot_id = _setup(tenant, db_session, "disc-alias@example.com", "Alias Co")
    bot = db_session.query(Bot).filter(Bot.id == _uuid.UUID(bot_id)).first()
    assert bot is not None
    bot.disclosure_config = {"legacy_level": "detailed"}
    db_session.commit()

    r = tenant.get(f"/bots/{bot_id}/disclosure", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"level": "standard"}


def test_put_disclosure_invalid_level(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token, bot_id = _setup(tenant, db_session, "disc-bad@example.com", "Bad Level Co")
    r = tenant.put(
        f"/bots/{bot_id}/disclosure",
        headers={"Authorization": f"Bearer {token}"},
        json={"level": "mega"},
    )
    assert r.status_code == 422


def test_build_rag_prompt_corporate_contains_instruction() -> None:
    from backend.chat.service import build_rag_prompt

    p = build_rag_prompt(
        "Q?",
        ["c1"],
        disclosure_config={"level": "corporate"},
    )
    assert "[Response level: corporate]" in p
    assert "non-technical" in p.lower() or "polished" in p.lower()
    assert "Hard limits" in p


def test_build_rag_prompt_disclosure_none_equals_standard_block() -> None:
    from backend.chat.service import build_rag_prompt

    p = build_rag_prompt("Q?", ["c"], disclosure_config=None)
    assert "[Response level: standard]" in p
