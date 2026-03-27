from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user


def test_get_privacy_defaults(client: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(client, db_session, email="privacy-default@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Privacy Client"},
    )

    resp = client.get("/clients/me/privacy", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert sorted(resp.json()["optional_entity_types"]) == ["ID_DOC", "IP", "URL_TOKEN"]


def test_put_privacy_updates_optional_entity_types(client: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(client, db_session, email="privacy-update@example.com")
    client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Privacy Update Client"},
    )

    resp = client.put(
        "/clients/me/privacy",
        headers={"Authorization": f"Bearer {token}"},
        json={"optional_entity_types": ["IP"]},
    )
    assert resp.status_code == 200
    assert resp.json()["optional_entity_types"] == ["IP"]

    resp2 = client.get("/clients/me/privacy", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200
    assert resp2.json()["optional_entity_types"] == ["IP"]
