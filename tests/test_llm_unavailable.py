"""Tests for LLM-unavailable graceful degradation (ClickUp 86exdwn6a)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy.orm import Session

from backend.chat.llm_unavailable import (
    LlmFailureType,
    classify_llm_failure,
)
from backend.chat.llm_unavailable_copy import (
    fallback_text,
    support_notified_text,
)
from backend.models import (
    Chat,
    EscalationTicket,
    EscalationTrigger,
    Message,
    MessageRole,
    Tenant,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_request())


# -- classifier ---------------------------------------------------------------


def test_classify_timeout_is_retryable_provider_timeout() -> None:
    fs = classify_llm_failure(APITimeoutError(_request()))
    assert fs.type is LlmFailureType.provider_timeout
    assert fs.retryable is True


def test_classify_connection_error_is_retryable_provider_unavailable() -> None:
    fs = classify_llm_failure(APIConnectionError(request=_request()))
    assert fs.type is LlmFailureType.provider_unavailable
    assert fs.retryable is True


def test_classify_internal_server_error_is_retryable_provider_unavailable() -> None:
    fs = classify_llm_failure(
        InternalServerError("boom", response=_response(500), body=None)
    )
    assert fs.type is LlmFailureType.provider_unavailable
    assert fs.retryable is True


def test_classify_rate_limit_is_retryable() -> None:
    exc = RateLimitError("slow down", response=_response(429), body=None)
    fs = classify_llm_failure(exc)
    assert fs.type is LlmFailureType.rate_limited
    assert fs.retryable is True


def test_classify_quota_exhausted_is_not_retryable() -> None:
    """AC4: quota exhausted disables retry."""
    exc = RateLimitError(
        "you exceeded your current quota",
        response=_response(429),
        body={"error": {"code": "insufficient_quota"}},
    )
    fs = classify_llm_failure(exc)
    assert fs.type is LlmFailureType.quota_exhausted
    assert fs.retryable is False


def test_classify_invalid_api_key_is_not_retryable() -> None:
    exc = AuthenticationError("bad key", response=_response(401), body=None)
    fs = classify_llm_failure(exc)
    assert fs.type is LlmFailureType.invalid_api_key
    assert fs.retryable is False


def test_classify_permission_denied_is_invalid_api_key() -> None:
    exc = PermissionDeniedError("forbidden", response=_response(403), body=None)
    fs = classify_llm_failure(exc)
    assert fs.type is LlmFailureType.invalid_api_key
    assert fs.retryable is False


# -- copy table ---------------------------------------------------------------


def test_fallback_text_english_retryable() -> None:
    assert "try again" in fallback_text(language="en", retryable=True).lower()


def test_fallback_text_russian_not_retryable() -> None:
    text = fallback_text(language="ru", retryable=False)
    assert "поддержку" in text.lower()


def test_fallback_text_unknown_language_falls_back_to_english() -> None:
    assert fallback_text(language="ja", retryable=True) == fallback_text(
        language="en", retryable=True
    )


def test_support_notified_text_translates_for_russian() -> None:
    assert "Поддержка" in support_notified_text(language="ru-RU")


# -- AC1: SSE returns llm_unavailable on LLM failure --------------------------


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


def test_widget_chat_returns_llm_unavailable_on_apitimeout(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: LLM timeout returns outcome=llm_unavailable, no ticket created."""
    token = register_and_verify_user(
        tenant, db_session, email="llm-unavail-1@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "LLM Unavail Co"},
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
        f"/widget/chat?bot_id={bot_public_id}&locale=en-US",
        json={"message": "hello", "locale": "en-US"},
    )
    assert resp.status_code == 200
    event = _parse_done_event(resp.text)
    assert event["outcome"] == "llm_unavailable"
    assert event["failure_state"]["type"] == "provider_timeout"
    assert event["failure_state"]["retryable"] is True
    assert event["failure_state"]["can_escalate"] is True
    # AC5: text populated for backward compat
    assert isinstance(event.get("text"), str) and event["text"].strip()
    # AC1: no ticket created
    tickets = (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.tenant_id == tenant_id)
        .all()
    )
    assert tickets == []


def test_widget_chat_quota_exhausted_marks_not_retryable(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: quota exhausted ⇒ retryable=false in widget payload."""
    token = register_and_verify_user(
        tenant, db_session, email="llm-unavail-quota@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Quota Co"},
    )
    assert cl_resp.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

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
        json={"message": "hi", "locale": "en"},
    )
    assert resp.status_code == 200
    event = _parse_done_event(resp.text)
    assert event["outcome"] == "llm_unavailable"
    assert event["failure_state"]["type"] == "quota_exhausted"
    assert event["failure_state"]["retryable"] is False


# -- AC3: manual escalation with llm_unavailable creates ticket without LLM ---


def test_perform_manual_escalation_llm_unavailable_skips_openai_call(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: trigger=llm_unavailable ⇒ ticket created with original message,
    no LLM handoff call (LLM is the failing dependency).
    """
    from backend.escalation import service as esc_service

    sentinel = Mock(side_effect=AssertionError("LLM should not be called"))
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.complete_escalation_openai_turn",
        sentinel,
    )

    token = register_and_verify_user(
        tenant, db_session, email="manual-llm-unavail@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual LLM Unavail"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(tenant, token)

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"browser_locale": "en-US"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None
    msg, tnum = esc_service.perform_manual_escalation(
        db_session,
        cl,
        chat.session_id,
        api_key="sk-broken",
        user_note=None,
        trigger=EscalationTrigger.llm_unavailable,
        failure_type="provider_timeout",
        original_user_message="What are your office hours?",
    )

    sentinel.assert_not_called()
    assert tnum.startswith("ESC-")
    assert "support" in msg.lower() or "поддерж" in msg.lower()

    ticket = (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.chat_id == chat.id)
        .first()
    )
    assert ticket is not None
    assert ticket.trigger is EscalationTrigger.llm_unavailable
    assert ticket.primary_question == "What are your office hours?"
    assert ticket.user_note is not None
    assert "[llm_failure: provider_timeout]" in ticket.user_note

    # Persisted assistant bubble carries the canned support-notified text.
    messages = (
        db_session.query(Message)
        .filter(Message.chat_id == chat.id, Message.role == MessageRole.assistant)
        .all()
    )
    assert len(messages) == 1
    assert "support" in messages[0].content.lower()


def test_widget_escalate_endpoint_accepts_llm_unavailable(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: POST /widget/escalate?trigger=llm_unavailable creates ticket."""
    sentinel = Mock(side_effect=AssertionError("LLM should not be called"))
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.complete_escalation_openai_turn",
        sentinel,
    )

    token = register_and_verify_user(
        tenant, db_session, email="widget-llm-unavail@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Widget LLM Unavail"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(tenant, token)
    bot_public_id = _create_bot(tenant, token)

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"browser_locale": "en"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    resp = tenant.post(
        f"/widget/escalate?bot_id={bot_public_id}&session_id={chat.session_id}",
        json={
            "trigger": "llm_unavailable",
            "failure_type": "rate_limited",
            "original_user_message": "Help me track my order",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ticket_number"].startswith("ESC-")

    sentinel.assert_not_called()
    ticket = (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.tenant_id == tenant_id)
        .first()
    )
    assert ticket is not None
    assert ticket.trigger is EscalationTrigger.llm_unavailable
    assert ticket.primary_question == "Help me track my order"
    assert "[llm_failure: rate_limited]" in (ticket.user_note or "")
