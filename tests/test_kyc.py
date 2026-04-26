"""Tests for KYC widget identity (HMAC tokens, session init, secret endpoints)."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.core.security import (
    generate_kyc_token,
    validate_kyc_token,
    validate_kyc_token_detail,
)
from backend.models import Chat, ContactSession
from tests.conftest import register_and_verify_user


def test_generate_kyc_token_validate_returns_user_context() -> None:
    secret = secrets.token_hex(32)
    payload = {
        "user_id": "user-1",
        "plan_tier": "pro",
        "locale": "en",
    }
    token = generate_kyc_token(payload, secret)
    out = validate_kyc_token(token, secret)
    assert out is not None
    assert out["user_id"] == "user-1"
    assert out["plan_tier"] == "pro"
    assert out["locale"] == "en"
    assert "exp" not in out


def test_validate_kyc_token_expired_returns_none() -> None:
    secret = secrets.token_hex(32)
    token = generate_kyc_token(
        {"user_id": "u"},
        secret,
        ttl_seconds=-120,
    )
    assert validate_kyc_token(token, secret) is None
    _ctx, reason = validate_kyc_token_detail(token, secret)
    assert reason == "expired"


def test_validate_kyc_token_tampered_payload_returns_none() -> None:
    secret = secrets.token_hex(32)
    token = generate_kyc_token({"user_id": "u"}, secret)
    b64, sig = token.split(".", 1)
    flip = "b" if b64[-1] != "b" else "a"
    bad = f"{b64[:-1]}{flip}.{sig}"
    assert validate_kyc_token(bad, secret) is None
    _ctx, reason = validate_kyc_token_detail(bad, secret)
    assert reason == "bad_signature"


def test_validate_kyc_token_missing_user_id_returns_none() -> None:
    secret = secrets.token_hex(32)
    now = __import__("time").time()
    raw = json.dumps(
        {"exp": int(now) + 300, "iat": int(now)},
        sort_keys=True,
    )
    import base64
    import hashlib
    import hmac

    b64 = (
        base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    )
    sig = hmac.new(
        secret.encode("utf-8"),
        b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = f"{b64}.{sig}"
    assert validate_kyc_token(token, secret) is None


def test_kyc_secret_endpoint_returns_key_once(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-secret@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "KYC Co"},
    )
    assert cr.status_code == 201

    r1 = tenant.post("/tenants/me/kyc/secret", headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200
    body = r1.json()
    assert "secret_key" in body
    assert len(body["secret_key"]) == 64

    r2 = tenant.post("/tenants/me/kyc/secret", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 409


def test_widget_session_init_identified_and_anonymous(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-widget@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget KYC Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    client_uuid = uuid.UUID(cr.json()["id"])

    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]

    id_token = generate_kyc_token(
        {"user_id": "ext-42", "plan_tier": "growth"},
        secret_hex,
    )
    init_ok = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert init_ok.status_code == 200
    data_ok = init_ok.json()
    assert data_ok["mode"] == "identified"
    sid = uuid.UUID(data_ok["session_id"])
    chat = (
        db_session.query(Chat)
        .filter(Chat.session_id == sid, Chat.tenant_id == client_uuid)
        .first()
    )
    assert chat is not None
    assert chat.user_context is not None
    assert chat.user_context.get("user_id") == "ext-42"
    row = (
        db_session.query(ContactSession)
        .filter(ContactSession.tenant_id == client_uuid, ContactSession.contact_id == "ext-42")
        .first()
    )
    assert row is not None
    assert row.session_ended_at is None
    assert row.conversation_turns == 0

    init_anon = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key},
    )
    assert init_anon.status_code == 200
    assert init_anon.json()["mode"] == "anonymous"
    sid_anon = uuid.UUID(init_anon.json()["session_id"])
    assert (
        db_session.query(Chat)
        .filter(Chat.session_id == sid_anon)
        .first()
        is None
    )

    bad_token = generate_kyc_token(
        {"user_id": "x"},
        secret_hex,
    )
    b64, sig = bad_token.split(".", 1)
    init_bad = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": f"{b64[:-2]}xx.{sig}"},
    )
    assert init_bad.status_code == 200
    assert init_bad.json()["mode"] == "anonymous"


def test_widget_session_init_invalid_token_falls_back_anonymous_logs(
    tenant: TestClient,
    db_session: Session,
    caplog,
) -> None:
    import logging

    token = register_and_verify_user(tenant, db_session, email="kyc-fallback@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Fallback Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]
    bad = generate_kyc_token(
        {"user_id": "u"},
        secret_hex,
    )
    b64, sig = bad.split(".", 1)
    tampered = f"{b64[:-1]}X.{sig}"

    with caplog.at_level(logging.INFO, logger="backend.widget.routes"):
        r = tenant.post(
            "/widget/session/init",
            json={"api_key": api_key, "identity_token": tampered},
        )
    assert r.status_code == 200
    assert r.json()["mode"] == "anonymous"
    assert any("kyc_validation_failed" in rec.message for rec in caplog.records)


def test_widget_session_init_creates_new_identified_session_and_patches_context(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-resume@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Resume Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]

    first_token = generate_kyc_token(
        {
            "user_id": "ext-42",
            "plan_tier": "growth",
        },
        secret_hex,
    )
    r1 = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": first_token, "locale": "en-US"},
    )
    assert r1.status_code == 200
    first_sid = uuid.UUID(r1.json()["session_id"])

    second_token = generate_kyc_token(
        {
            "user_id": "ext-42",
            "email": "person@example.com",
            "plan_tier": "enterprise",
        },
        secret_hex,
    )
    r2 = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": second_token, "locale": "de-DE"},
    )
    assert r2.status_code == 200
    second_sid = uuid.UUID(r2.json()["session_id"])
    assert second_sid != first_sid

    first_chat = db_session.query(Chat).filter(Chat.session_id == first_sid).first()
    second_chat = db_session.query(Chat).filter(Chat.session_id == second_sid).first()
    assert first_chat is not None
    assert second_chat is not None
    assert first_chat.user_context is not None
    assert second_chat.user_context is not None
    assert first_chat.user_context.get("plan_tier") == "growth"
    assert second_chat.user_context.get("user_id") == "ext-42"
    assert second_chat.user_context.get("email") == "person@example.com"
    assert second_chat.user_context.get("plan_tier") == "enterprise"
    assert second_chat.user_context.get("browser_locale") == "de-DE"
    rows = (
        db_session.query(ContactSession)
        .filter(ContactSession.tenant_id == uuid.UUID(cr.json()["id"]), ContactSession.contact_id == "ext-42")
        .order_by(ContactSession.session_started_at.asc())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].session_ended_at is not None
    assert rows[1].session_ended_at is None
    assert rows[1].email == "person@example.com"
    assert rows[1].plan_tier == "enterprise"


def test_widget_session_init_closed_identified_chat_gets_new_session(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-closed@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Closed Resume Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]

    id_token = generate_kyc_token(
        {"user_id": "ext-42"},
        secret_hex,
    )
    r1 = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert r1.status_code == 200
    first_sid = uuid.UUID(r1.json()["session_id"])
    first_chat = db_session.query(Chat).filter(Chat.session_id == first_sid).first()
    assert first_chat is not None
    first_chat.ended_at = datetime.now(timezone.utc)
    db_session.add(first_chat)
    db_session.commit()

    r2 = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert r2.status_code == 200
    second_sid = uuid.UUID(r2.json()["session_id"])
    assert second_sid != first_sid
    rows = (
        db_session.query(ContactSession)
        .filter(ContactSession.tenant_id == uuid.UUID(cr.json()["id"]), ContactSession.contact_id == "ext-42")
        .order_by(ContactSession.session_started_at.asc())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].session_ended_at is not None
    assert rows[1].session_ended_at is None


def test_widget_session_init_expired_identified_chat_gets_new_session(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-expired-resume@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Expired Resume Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]

    id_token = generate_kyc_token(
        {"user_id": "ext-42"},
        secret_hex,
    )
    r1 = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert r1.status_code == 200
    first_sid = uuid.UUID(r1.json()["session_id"])
    first_chat = db_session.query(Chat).filter(Chat.session_id == first_sid).first()
    assert first_chat is not None
    first_chat.updated_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db_session.add(first_chat)
    db_session.commit()

    r2 = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert r2.status_code == 200
    assert uuid.UUID(r2.json()["session_id"]) != first_sid
    rows = (
        db_session.query(ContactSession)
        .filter(ContactSession.tenant_id == uuid.UUID(cr.json()["id"]), ContactSession.contact_id == "ext-42")
        .order_by(ContactSession.session_started_at.asc())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].session_ended_at is not None
    assert rows[1].session_ended_at is None


def test_widget_session_init_ignores_existing_identified_sessions(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-latest@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Latest Resume Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    client_uuid = uuid.UUID(cr.json()["id"])

    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]

    older_sid = uuid.uuid4()
    newer_sid = uuid.uuid4()
    older_chat = Chat(
        tenant_id=client_uuid,
        session_id=older_sid,
        user_context={"user_id": "ext-42", "plan_tier": "growth"},
    )
    newer_chat = Chat(
        tenant_id=client_uuid,
        session_id=newer_sid,
        user_context={"user_id": "ext-42", "plan_tier": "pro"},
    )
    db_session.add_all([older_chat, newer_chat])
    db_session.commit()

    older_chat.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    newer_chat.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.add(older_chat)
    db_session.add(newer_chat)
    db_session.commit()

    id_token = generate_kyc_token(
        {"user_id": "ext-42"},
        secret_hex,
    )
    r = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert r.status_code == 200
    fresh_sid = uuid.UUID(r.json()["session_id"])
    assert fresh_sid not in {older_sid, newer_sid}


def test_widget_session_init_does_not_reuse_open_or_closed_sessions(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-open-over-closed@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Open Over Closed Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]
    client_uuid = uuid.UUID(cr.json()["id"])

    sk_resp = tenant.post(
        "/tenants/me/kyc/secret",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sk_resp.status_code == 200
    secret_hex = sk_resp.json()["secret_key"]

    open_sid = uuid.uuid4()
    closed_sid = uuid.uuid4()
    open_chat = Chat(
        tenant_id=client_uuid,
        session_id=open_sid,
        user_context={"user_id": "ext-42", "plan_tier": "growth"},
    )
    closed_chat = Chat(
        tenant_id=client_uuid,
        session_id=closed_sid,
        user_context={"user_id": "ext-42", "plan_tier": "enterprise"},
        ended_at=datetime.now(timezone.utc),
    )
    db_session.add_all([open_chat, closed_chat])
    db_session.commit()

    open_chat.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    closed_chat.updated_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.add(open_chat)
    db_session.add(closed_chat)
    db_session.commit()

    id_token = generate_kyc_token(
        {"user_id": "ext-42"},
        secret_hex,
    )
    r = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": id_token},
    )
    assert r.status_code == 200
    fresh_sid = uuid.UUID(r.json()["session_id"])
    assert fresh_sid not in {open_sid, closed_sid}


def test_kyc_rotate_returns_new_secret(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="kyc-rotate@example.com")
    cr = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Rotate Co"},
    )
    assert cr.status_code == 201
    api_key = cr.json()["api_key"]

    r1 = tenant.post("/tenants/me/kyc/secret", headers={"Authorization": f"Bearer {token}"})
    assert r1.status_code == 200
    old_secret = r1.json()["secret_key"]
    r2 = tenant.post("/tenants/me/kyc/rotate", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    new_secret = r2.json()["secret_key"]
    assert new_secret != old_secret

    tok_old = generate_kyc_token(
        {"user_id": "u"},
        old_secret,
    )
    tok_new = generate_kyc_token(
        {"user_id": "u"},
        new_secret,
    )
    assert validate_kyc_token(tok_new, new_secret) is not None
    assert validate_kyc_token(tok_old, new_secret) is None
    assert validate_kyc_token(tok_old, old_secret) is not None

    overlap = tenant.post(
        "/widget/session/init",
        json={"api_key": api_key, "identity_token": tok_old},
    )
    assert overlap.status_code == 200
    assert overlap.json()["mode"] == "identified"
