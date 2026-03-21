"""Disclosure controls: tenant-wide level + API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.disclosure_config import public_config_dict, resolve_level
from backend.models import Client
from tests.conftest import register_and_verify_user


def test_resolve_level_none() -> None:
    assert resolve_level(None) == "standard"


def test_resolve_level_empty_dict() -> None:
    assert resolve_level({}) == "standard"


def test_resolve_level_primary_key() -> None:
    assert resolve_level({"level": "corporate"}) == "corporate"


def test_resolve_level_default_level_alias() -> None:
    assert resolve_level({"default_level": "detailed"}) == "detailed"


def test_resolve_level_prefers_level_over_alias() -> None:
    assert resolve_level({"level": "standard", "default_level": "detailed"}) == "standard"


def test_resolve_level_invalid_falls_back() -> None:
    assert resolve_level({"level": "nope"}) == "standard"


def test_public_config_dict() -> None:
    assert public_config_dict({"default_level": "corporate"}) == {"level": "corporate"}


def test_get_disclosure_defaults(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="disc-get@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Disc Client"},
    )
    r = client.get("/clients/me/disclosure", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"level": "standard"}


def test_put_and_get_disclosure(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="disc-put@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Disc Put"},
    )
    r = client.put(
        "/clients/me/disclosure",
        headers={"Authorization": f"Bearer {token}"},
        json={"level": "corporate"},
    )
    assert r.status_code == 200
    assert r.json() == {"level": "corporate"}
    r2 = client.get("/clients/me/disclosure", headers={"Authorization": f"Bearer {token}"})
    assert r2.json() == {"level": "corporate"}


def test_get_disclosure_default_level_alias_from_db(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="disc-alias@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Alias Co"},
    )
    cl = db_session.query(Client).filter(Client.name == "Alias Co").first()
    assert cl is not None
    cl.disclosure_config = {"default_level": "detailed"}
    db_session.commit()

    r = client.get("/clients/me/disclosure", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"level": "detailed"}


def test_put_disclosure_invalid_level(
    client: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(client, db_session, email="disc-bad@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bad Level Co"},
    )
    r = client.put(
        "/clients/me/disclosure",
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
