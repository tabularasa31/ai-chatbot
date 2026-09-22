"""Tests for tenant-level LLM-failure alerts (ClickUp 86exdwjtv)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, AuthenticationError, RateLimitError
from sqlalchemy.orm import Session

from backend.chat.llm_unavailable import LlmFailureType
from backend.models import Tenant, User
from backend.tenants import llm_alerts as alerts
from tests.conftest import register_and_verify_user, set_client_openai_key


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_request())


def _bootstrap_tenant(
    tenant_client: TestClient,
    db_session: Session,
    *,
    email: str,
    name: str,
) -> tuple[Tenant, User]:
    token = register_and_verify_user(tenant_client, db_session, email=email)
    cl_resp = tenant_client.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert cl_resp.status_code == 201, cl_resp.text
    set_client_openai_key(tenant_client, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    tenant = db_session.get(Tenant, tenant_id)
    owner = (
        db_session.query(User)
        .filter(User.tenant_id == tenant_id)
        .order_by(User.created_at.asc())
        .first()
    )
    assert tenant is not None
    assert owner is not None
    return tenant, owner


# --- service: record/clear, throttle ----------------------------------------


def test_apply_llm_failure_state_machine(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drives apply_llm_failure through its full lifecycle on one tenant.

    Failure modes asserted, in order:
    - non-actionable failure types (provider_timeout, rate_limited) never set alert state or email
    - first actionable failure sets alert state and sends exactly one email
    - repeated same-type failures within the throttle window send no more email
    - a same-type failure after the throttle window elapses resends the email
    - switching failure type bypasses the throttle and emails immediately
    - apply_clear_alert resets all alert fields to None
    """
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    tenant_row, owner = _bootstrap_tenant(
        tenant, db_session, email="alert-lifecycle@example.com", name="Alert Lifecycle"
    )

    # Non-actionable types short-circuit before any state change or email.
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.provider_timeout)
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.rate_limited)
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type is None
    assert sent == []

    # First actionable failure sets state and emails once.
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "quota_exhausted"
    assert tenant_row.llm_alert_first_at is not None
    assert tenant_row.llm_alert_last_email_at is not None
    assert len(sent) == 1
    assert sent[0]["to"] == owner.email
    assert "quota" in sent[0]["subject"].lower()

    # Same type again, within the throttle window: no additional email.
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    assert len(sent) == 1

    # Backdate past the throttle window: same type resends.
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    tenant_row.llm_alert_last_email_at = (
        tenant_row.llm_alert_last_email_at - alerts.EMAIL_THROTTLE - timedelta(minutes=1)
    )
    db_session.commit()
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    assert len(sent) == 2

    # A different failure type bypasses the throttle and emails immediately.
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.invalid_api_key)
    assert len(sent) == 3
    assert "quota" in sent[0]["subject"].lower()
    assert "invalid" in sent[2]["subject"].lower()

    # Clearing resets every alert field.
    alerts.apply_clear_alert(tenant_row.id)
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type is None
    assert tenant_row.llm_alert_first_at is None
    assert tenant_row.llm_alert_last_email_at is None


def test_record_llm_failure_returns_should_email_bool(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Direct unit check on the lower-level service: bool return drives
    whether the caller dispatches the email side-effect."""
    tenant_row, _ = _bootstrap_tenant(
        tenant, db_session, email="alert-bool@example.com", name="Bool Co"
    )
    assert (
        alerts.record_llm_failure(
            db_session, tenant_row.id, LlmFailureType.quota_exhausted
        )
        is True
    )
    # Within throttle window — same type, recently emailed.
    assert (
        alerts.record_llm_failure(
            db_session, tenant_row.id, LlmFailureType.quota_exhausted
        )
        is False
    )
    # Non-actionable types short-circuit before any DB write.
    assert (
        alerts.record_llm_failure(
            db_session, tenant_row.id, LlmFailureType.provider_timeout
        )
        is False
    )


# --- API: GET /tenants/me/llm-alert -----------------------------------------


def test_llm_alert_endpoint_reflects_alert_state(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /tenants/me/llm-alert returns null before any failure and the
    active alert (type + since) once one has been recorded."""
    monkeypatch.setattr("backend.tenants.llm_alerts.send_email", lambda **_: None)
    token = register_and_verify_user(
        tenant, db_session, email="alert-api@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Alert API"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    r = tenant.get("/tenants/me/llm-alert", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"type": None, "since": None}

    alerts.apply_llm_failure(tenant_id, LlmFailureType.invalid_api_key)

    r = tenant.get("/tenants/me/llm-alert", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "invalid_api_key"
    assert body["since"] is not None


# --- widget pipeline integration --------------------------------------------


def _parse_done_event(raw_body: str) -> dict:
    for frame in raw_body.split("\n\n"):
        data = "\n".join(
            line[len("data:"):].strip()
            for line in frame.splitlines()
            if line.startswith("data:")
        )
        if not data:
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "done":
            return event
    raise AssertionError(f"no done event in stream: {raw_body[:500]}")


def _create_bot(client: TestClient, token: str) -> str:
    resp = client.post(
        "/bots",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Bot"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["public_id"]


def test_widget_llm_failure_alert_lifecycle(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drives the widget chat pipeline through a full alert lifecycle.

    Failure modes asserted, in order:
    - a quota-exhausted turn raises the tenant alert and emails once
    - a subsequent auth failure switches the alert type and emails again
    - a canned greeting turn (tokens_used == 0) does not clear the alert
    - a real successful turn (tokens_used > 0) clears the alert
    """
    from backend.chat.service import (
        ChatTurnOutcome,
    )

    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    token = register_and_verify_user(
        tenant, db_session, email="widget-alert-lifecycle@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Alert Lifecycle"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    async def _raise_quota(*args, **kwargs):
        raise RateLimitError(
            "insufficient_quota: out of credits",
            response=_response(429),
            body={"error": {"code": "insufficient_quota"}},
        )

    monkeypatch.setattr("backend.widget.routes.async_process_chat_message", _raise_quota)
    resp = tenant.post(f"/widget/chat?bot_id={bot_public_id}", json={"message": "hi"})
    assert resp.status_code == 200
    event = _parse_done_event(resp.text)
    assert event["failure_state"]["type"] == "quota_exhausted"
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "quota_exhausted"
    assert len(sent) == 1

    async def _raise_auth(*args, **kwargs):
        raise AuthenticationError("bad key", response=_response(401), body=None)

    monkeypatch.setattr("backend.widget.routes.async_process_chat_message", _raise_auth)
    resp = tenant.post(f"/widget/chat?bot_id={bot_public_id}", json={"message": "hi"})
    assert resp.status_code == 200
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "invalid_api_key"
    assert len(sent) == 2
    assert "invalid" in sent[1]["subject"].lower()

    async def _greeting(*args, **kwargs):
        return ChatTurnOutcome(
            text="Hello!", document_ids=[], tokens_used=0
        )

    monkeypatch.setattr("backend.widget.routes.async_process_chat_message", _greeting)
    resp = tenant.post(f"/widget/chat?bot_id={bot_public_id}", json={"message": "hi"})
    assert resp.status_code == 200
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    # Alert remains — greeting didn't exercise the LLM, so we have no
    # evidence the broken key is back.
    assert tenant_row.llm_alert_type == "invalid_api_key"

    async def _success(*args, **kwargs):
        return ChatTurnOutcome(
            text="answer with tokens", document_ids=[], tokens_used=10
        )

    monkeypatch.setattr("backend.widget.routes.async_process_chat_message", _success)
    resp = tenant.post(f"/widget/chat?bot_id={bot_public_id}", json={"message": "hi"})
    assert resp.status_code == 200
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type is None


def test_widget_provider_timeout_does_not_raise_tenant_alert(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient provider failures don't produce a tenant-action banner —
    nothing for the admin to do, and the issue auto-resolves."""
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    token = register_and_verify_user(
        tenant, db_session, email="widget-alert-timeout@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Timeout Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    async def _raise_timeout(*args, **kwargs):
        raise APITimeoutError(_request())

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message", _raise_timeout
    )

    resp = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}",
        json={"message": "hi"},
    )
    assert resp.status_code == 200
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type is None
    assert sent == []
