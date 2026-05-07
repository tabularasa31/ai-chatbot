"""Tests for tenant-level LLM-failure alerts (ClickUp 86exdwjtv)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

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


def test_apply_llm_failure_sets_state_and_emails(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    tenant_row, owner = _bootstrap_tenant(
        tenant, db_session, email="alert-record@example.com", name="Alert Record"
    )
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


def test_apply_llm_failure_throttles_email_within_24h(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    tenant_row, _ = _bootstrap_tenant(
        tenant, db_session, email="alert-throttle@example.com", name="Throttle Co"
    )
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    assert len(sent) == 1


def test_apply_llm_failure_resends_after_throttle_window(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    tenant_row, _ = _bootstrap_tenant(
        tenant, db_session, email="alert-reemit@example.com", name="Re-emit Co"
    )
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    # Backdate the last-sent timestamp past the throttle window.
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    tenant_row.llm_alert_last_email_at = (
        tenant_row.llm_alert_last_email_at - alerts.EMAIL_THROTTLE - timedelta(minutes=1)
    )
    db_session.commit()
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    assert len(sent) == 2


def test_apply_llm_failure_emails_immediately_when_type_changes(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switching from quota_exhausted to invalid_api_key bypasses throttle —
    it's a different problem the admin needs to know about right away."""
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    tenant_row, _ = _bootstrap_tenant(
        tenant, db_session, email="alert-typechange@example.com", name="Type Change"
    )
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.invalid_api_key)
    assert len(sent) == 2
    assert "quota" in sent[0]["subject"].lower()
    assert "invalid" in sent[1]["subject"].lower()


def test_apply_llm_failure_ignores_non_actionable_types(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    tenant_row, _ = _bootstrap_tenant(
        tenant, db_session, email="alert-noop@example.com", name="Noop Co"
    )
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.provider_timeout)
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.rate_limited)
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type is None
    assert sent == []


def test_apply_clear_alert_resets_state(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.tenants.llm_alerts.send_email", lambda **_: None)

    tenant_row, _ = _bootstrap_tenant(
        tenant, db_session, email="alert-clear@example.com", name="Clear Co"
    )
    alerts.apply_llm_failure(tenant_row.id, LlmFailureType.quota_exhausted)
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_row.id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "quota_exhausted"
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


def test_llm_alert_endpoint_returns_null_when_no_alert(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="alert-api-empty@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Empty Alert"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)

    r = tenant.get(
        "/tenants/me/llm-alert",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json() == {"type": None, "since": None}


def test_llm_alert_endpoint_returns_active_alert(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.tenants.llm_alerts.send_email", lambda **_: None)
    token = register_and_verify_user(
        tenant, db_session, email="alert-api-active@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Active Alert"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    alerts.apply_llm_failure(tenant_id, LlmFailureType.invalid_api_key)

    r = tenant.get(
        "/tenants/me/llm-alert",
        headers={"Authorization": f"Bearer {token}"},
    )
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


def test_widget_quota_exhausted_raises_tenant_alert(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    token = register_and_verify_user(
        tenant, db_session, email="widget-alert-quota@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget Alert Quota"},
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

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message", _raise_quota
    )

    resp = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}",
        json={"message": "hi"},
    )
    assert resp.status_code == 200
    event = _parse_done_event(resp.text)
    assert event["failure_state"]["type"] == "quota_exhausted"

    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "quota_exhausted"
    assert len(sent) == 1


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


def test_widget_invalid_api_key_raises_alert(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )

    token = register_and_verify_user(
        tenant, db_session, email="widget-alert-invalidkey@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Invalid Key Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    async def _raise_auth(*args, **kwargs):
        raise AuthenticationError("bad key", response=_response(401), body=None)

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message", _raise_auth
    )

    resp = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}",
        json={"message": "hi"},
    )
    assert resp.status_code == 200
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "invalid_api_key"
    assert len(sent) == 1
    assert "invalid" in sent[0]["subject"].lower()


def test_widget_greeting_does_not_clear_alert(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful turn that didn't actually call the LLM (greeting,
    canned small-talk: tokens_used == 0) is not evidence the broken key
    is back, so the alert must not be cleared."""
    monkeypatch.setattr("backend.tenants.llm_alerts.send_email", lambda **_: None)
    from backend.chat.service import ChatTurnOutcome

    token = register_and_verify_user(
        tenant, db_session, email="widget-greeting-no-clear@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Greeting Test"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    alerts.apply_llm_failure(tenant_id, LlmFailureType.quota_exhausted)

    async def _greeting(*args, **kwargs):
        return ChatTurnOutcome(
            text="Hello!",
            document_ids=[],
            tokens_used=0,  # canned greeting, no LLM call
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message", _greeting
    )

    resp = tenant.post(
        f"/widget/chat?bot_id={bot_public_id}",
        json={"message": "hi"},
    )
    assert resp.status_code == 200
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    # Alert remains — greeting didn't exercise the LLM, so we have no
    # evidence the broken key is back.
    assert tenant_row.llm_alert_type == "quota_exhausted"


def test_widget_success_after_failure_clears_alert(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        "backend.tenants.llm_alerts.send_email",
        lambda **kwargs: sent.append(kwargs),
    )
    from backend.chat.service import ChatTurnOutcome

    token = register_and_verify_user(
        tenant, db_session, email="widget-alert-clear@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Clear Flow"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    alerts.apply_llm_failure(tenant_id, LlmFailureType.quota_exhausted)
    db_session.expire_all()
    tenant_row = db_session.get(Tenant, tenant_id)
    assert tenant_row is not None
    assert tenant_row.llm_alert_type == "quota_exhausted"

    async def _success(*args, **kwargs):
        return ChatTurnOutcome(
            text="answer with tokens",
            document_ids=[],
            tokens_used=10,
            chat_ended=False,
        )

    monkeypatch.setattr(
        "backend.widget.routes.async_process_chat_message", _success
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
