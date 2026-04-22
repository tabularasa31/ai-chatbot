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
from backend.core.jwt_kinds import EVAL_TESTER_JWT_TYP
from backend.models import Tenant, Tester as EvalTesterRecord
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


def _eval_auth(tenant: TestClient, username: str, password: str) -> dict[str, str]:
    r = tenant.post("/eval/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _eligible_bot_client(tenant: TestClient, db_session: Session, *, email_suffix: str) -> str:
    token = register_and_verify_user(tenant, db_session, email=f"eval-owner-{email_suffix}@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"Eval Bot {email_suffix}"},
    )
    assert cl.status_code == 201, cl.text
    set_client_openai_key(tenant, token)
    return _bot_public_id(tenant, token)


def _bot_public_id(tenant: TestClient, token: str) -> str:
    bots = tenant.get("/bots", headers={"Authorization": f"Bearer {token}"})
    assert bots.status_code == 200, bots.text
    items = bots.json()["items"]
    assert items, "expected at least one bot after tenant bootstrap"
    return items[0]["public_id"]


def test_eval_login_success(tenant: TestClient, db_session: Session) -> None:
    _eval_tester(db_session, username="anna", password="pw")
    r = tenant.post("/eval/login", json={"username": "anna", "password": "pw"})
    assert r.status_code == 200
    assert r.json().get("token_type") == "bearer"
    assert "access_token" in r.json()


def test_eval_login_bad_password(tenant: TestClient, db_session: Session) -> None:
    _eval_tester(db_session, username="anna", password="pw")
    r = tenant.post("/eval/login", json={"username": "anna", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


def test_eval_login_inactive_tester(tenant: TestClient, db_session: Session) -> None:
    _eval_tester(db_session, username="anna", password="pw", active=False)
    r = tenant.post("/eval/login", json={"username": "anna", "password": "pw"})
    assert r.status_code == 401


def test_create_session_unknown_bot(tenant: TestClient, db_session: Session) -> None:
    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    r = tenant.post("/eval/sessions", headers=h, json={"bot_id": "ch_does_not_exist_xx"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Bot not found"


def test_create_session_inactive_client(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(tenant, db_session, email="eval-inactive@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Inactive Eval Co"},
    )
    assert cl.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _bot_public_id(tenant, token)
    cid = uuid.UUID(cl.json()["id"])
    row = db_session.query(Tenant).filter(Tenant.id == cid).first()
    assert row is not None
    row.is_active = False
    db_session.commit()

    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    r = tenant.post("/eval/sessions", headers=h, json={"bot_id": bot_public_id})
    assert r.status_code == 403
    assert r.json()["detail"] == "Tenant is not active"


def test_create_session_no_openai_key(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(tenant, db_session, email="eval-no-oai@example.com")
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No OAI Eval Co"},
    )
    assert cl.status_code == 201
    bot_public_id = _bot_public_id(tenant, token)

    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    r = tenant.post("/eval/sessions", headers=h, json={"bot_id": bot_public_id})
    assert r.status_code == 400
    assert "OpenAI API key" in r.json()["detail"]


def test_create_session_success(tenant: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(tenant, db_session, email_suffix="ok")
    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    r = tenant.post("/eval/sessions", headers=h, json={"bot_id": public_id})
    assert r.status_code == 201
    data = r.json()
    assert data["bot_id"] == public_id
    assert "id" in data


def test_tester_cannot_access_other_tester_session(tenant: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(tenant, db_session, email_suffix="iso")
    _eval_tester(db_session, username="alice", password="a")
    _eval_tester(db_session, username="bob", password="b", active=True)
    # second tester needs different username - use default for first, bob for second
    h_alice = _eval_auth(tenant, "alice", "a")
    r = tenant.post("/eval/sessions", headers=h_alice, json={"bot_id": public_id})
    assert r.status_code == 201
    sid = r.json()["id"]

    h_bob = _eval_auth(tenant, "bob", "b")
    r2 = tenant.get(f"/eval/sessions/{sid}/results", headers=h_bob)
    assert r2.status_code == 404


def test_pass_verdict_rejects_error_category(tenant: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(tenant, db_session, email_suffix="val")
    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    sid = tenant.post("/eval/sessions", headers=h, json={"bot_id": public_id}).json()["id"]
    r = tenant.post(
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


def test_fail_other_requires_comment(tenant: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(tenant, db_session, email_suffix="val2")
    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    sid = tenant.post("/eval/sessions", headers=h, json={"bot_id": public_id}).json()["id"]
    r = tenant.post(
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


def test_fail_verdict_allows_empty_bot_answer(tenant: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(tenant, db_session, email_suffix="empty-answer")
    _eval_tester(db_session)
    h = _eval_auth(tenant, "eval-tester-1", "secret123")
    sid = tenant.post("/eval/sessions", headers=h, json={"bot_id": public_id}).json()["id"]
    r = tenant.post(
        f"/eval/sessions/{sid}/results",
        headers=h,
        json={
            "question": "Q?",
            "bot_answer": "   ",
            "verdict": "fail",
            "error_category": "no_answer",
        },
    )
    assert r.status_code == 201, r.text

    results = tenant.get(f"/eval/sessions/{sid}/results", headers=h)
    assert results.status_code == 200, results.text
    items = results.json()["items"]
    assert len(items) == 1
    assert items[0]["bot_answer"] == ""


def test_decode_access_token_rejects_eval_typ() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    exp = now + dt.timedelta(hours=1)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": EVAL_TESTER_JWT_TYP,
            "exp": exp,
            "iat": now,
        },
        settings.jwt_secret,
        algorithm=ALGORITHM,
    )
    assert decode_access_token(token) is None


def test_user_jwt_includes_access_typ(tenant: TestClient, db_session: Session) -> None:
    token = register_and_verify_user(tenant, db_session, email="typ-check@example.com")
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    assert payload.get("typ") == USER_ACCESS_JWT_TYP


def test_eval_protected_routes_reject_user_jwt(tenant: TestClient, db_session: Session) -> None:
    public_id = _eligible_bot_client(tenant, db_session, email_suffix="ujwt")
    _eval_tester(db_session)
    user_jwt = register_and_verify_user(tenant, db_session, email="regular@example.com")
    # Create a tenant for user so register path is valid — need second user for bot? use same flow
    cl = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {user_jwt}"},
        json={"name": "User Co"},
    )
    assert cl.status_code == 201
    set_client_openai_key(tenant, user_jwt)

    r = tenant.post(
        "/eval/sessions",
        headers={"Authorization": f"Bearer {user_jwt}"},
        json={"bot_id": public_id},
    )
    assert r.status_code == 401
