"""Tests for internal /eval QA API."""

from __future__ import annotations

import datetime as dt
import uuid

import jwt
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.jwt_kinds import USER_ACCESS_JWT_TYP
from backend.core.security import ALGORITHM, decode_access_token
from backend.eval.tokens import EVAL_JWT_TYP
from backend.models import Client, Tester as EvalTesterRecord
from tests.conftest import register_and_verify_user, set_client_openai_key


def _eval_tester(
    db_session: Session,
    *,
    username: str = "eval-tester-1",
    password: str = "secret123",
    active: bool = True,
) -> EvalTesterRecord:
    t = EvalTesterRecord(username=username, password=password, is_active=active)
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


def _eval_auth(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post("/eval/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _eligible_bot_client(client: TestClient, db_session: Session, *, email_suffix: str) -> str:
    token = register_and_verify_user(client, db_session, email=f"eval-owner-{email_suffix}@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"Eval Bot {email_suffix}"},
    )
    assert cl.status_code == 201, cl.text
    set_client_openai_key(client, token)
    return cl.json()["public_id"]


def test_eval_login_success(client: TestClient, db_session: Session) -> None:
    _eval_tester(db_session, username="anna", password="pw")
    r = client.post("/eval/login", json={"username": "anna", "password": "pw"})
    assert r.status_code == 200
    assert r.json().get("token_type") == "bearer"
    assert "access_token" in r.json()


def test_eval_login_bad_password(client: TestClient, db_session: Session) -> None:
    _eval_tester(db_session, username="anna", password="pw")
    r = client.post("/eval/login", json={"username": "anna", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


def test_eval_login_inactive_tester(client: TestClient, db_session: Session) -> None:
    _eval_tester(db_session, username="anna", password="pw", active=False)
    r = client.post("/eval/login", json={"username": "anna", "password": "pw"})
    assert r.status_code == 401


def test_create_session_unknown_bot(client: TestClient, db_session: Session) -> None:
    _eval_tester(db_session)
    h = _eval_auth(client, "eval-tester-1", "secret123")
    r = client.post("/eval/sessions", headers=h, json={"bot_id": "ch_does_not_exist_xx"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Bot not found"


def test_create_session_inactive_client(client: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(client, db_session, email="eval-inactive@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Inactive Eval Co"},
    )
    assert cl.status_code == 201
    set_client_openai_key(client, token)
    public_id = cl.json()["public_id"]
    cid = uuid.UUID(cl.json()["id"])
    row = db_session.query(Client).filter(Client.id == cid).first()
    assert row is not None
    row.is_active = False
    db_session.commit()

    _eval_tester(db_session)
    h = _eval_auth(client, "eval-tester-1", "secret123")
    r = client.post("/eval/sessions", headers=h, json={"bot_id": public_id})
    assert r.status_code == 403
    assert r.json()["detail"] == "Client is not active"


def test_create_session_no_openai_key(client: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(client, db_session, email="eval-no-oai@example.com")
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No OAI Eval Co"},
    )
    assert cl.status_code == 201
    public_id = cl.json()["public_id"]

    _eval_tester(db_session)
    h = _eval_auth(client, "eval-tester-1", "secret123")
    r = client.post("/eval/sessions", headers=h, json={"bot_id": public_id})
    assert r.status_code == 400
    assert "OpenAI API key" in r.json()["detail"]


def test_create_session_success(client: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(client, db_session, email_suffix="ok")
    _eval_tester(db_session)
    h = _eval_auth(client, "eval-tester-1", "secret123")
    r = client.post("/eval/sessions", headers=h, json={"bot_id": public_id})
    assert r.status_code == 201
    data = r.json()
    assert data["bot_id"] == public_id
    assert "id" in data


def test_tester_cannot_access_other_tester_session(client: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(client, db_session, email_suffix="iso")
    _eval_tester(db_session, username="alice", password="a")
    _eval_tester(db_session, username="bob", password="b", active=True)
    # second tester needs different username - use default for first, bob for second
    h_alice = _eval_auth(client, "alice", "a")
    r = client.post("/eval/sessions", headers=h_alice, json={"bot_id": public_id})
    assert r.status_code == 201
    sid = r.json()["id"]

    h_bob = _eval_auth(client, "bob", "b")
    r2 = client.get(f"/eval/sessions/{sid}/results", headers=h_bob)
    assert r2.status_code == 404


def test_pass_verdict_rejects_error_category(client: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(client, db_session, email_suffix="val")
    _eval_tester(db_session)
    h = _eval_auth(client, "eval-tester-1", "secret123")
    sid = client.post("/eval/sessions", headers=h, json={"bot_id": public_id}).json()["id"]
    r = client.post(
        f"/eval/sessions/{sid}/results",
        headers=h,
        json={
            "question": "Q?",
            "bot_answer": "A.",
            "verdict": "pass",
            "error_category": "incomplete",
        },
    )
    assert r.status_code == 422


def test_fail_other_requires_comment(client: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(client, db_session, email_suffix="val2")
    _eval_tester(db_session)
    h = _eval_auth(client, "eval-tester-1", "secret123")
    sid = client.post("/eval/sessions", headers=h, json={"bot_id": public_id}).json()["id"]
    r = client.post(
        f"/eval/sessions/{sid}/results",
        headers=h,
        json={
            "question": "Q?",
            "bot_answer": "A.",
            "verdict": "fail",
            "error_category": "other",
            "comment": "  ",
        },
    )
    assert r.status_code == 422


def test_decode_access_token_rejects_eval_typ() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(hours=1)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": EVAL_JWT_TYP,
            "exp": exp,
            "iat": now,
        },
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    assert decode_access_token(token) is None


def test_user_jwt_includes_access_typ(client: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(client, db_session, email="typ-check@example.com")
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    assert payload.get("typ") == USER_ACCESS_JWT_TYP


def test_eval_protected_routes_reject_user_jwt(client: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(client, db_session, email_suffix="ujwt")
    _eval_tester(db_session)
    user_jwt = register_and_verify_user(client, db_session, email="regular@example.com")
    # Create a client for user so register path is valid — need second user for bot? use same flow
    cl = client.post(
        "/clients",
        headers={"Authorization": f"Bearer {user_jwt}"},
        json={"name": "User Co"},
    )
    assert cl.status_code == 201
    set_client_openai_key(client, user_jwt)

    r = client.post(
        "/eval/sessions",
        headers={"Authorization": f"Bearer {user_jwt}"},
        json={"bot_id": public_id},
    )
    assert r.status_code == 401
