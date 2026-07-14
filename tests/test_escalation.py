"""FI-ESC: escalation helper unit tests."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.orm import Session

from backend.escalation.service import (
    _FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS,
    _clear_escalation_clarify_flag,
    _escalation_clarify_already_asked,
    _notify_tenant_new_ticket,
    _notify_tenant_ticket_update,
    _set_escalation_clarify_flag,
    advance_notification_marker_to_current,
    apply_collected_contact_email,
    compute_priority,
    create_escalation_ticket,
    detect_human_request,
    generate_ticket_number,
    parse_contact_email,
    perform_manual_escalation,
    should_escalate,
)
from backend.models import (
    Chat,
    Tenant,
    EscalationPriority,
    EscalationTicket,
    EscalationTrigger,
    EscalationStatus,
    Message,
    MessageRole,
    ContactSession,
    User,
)
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_should_escalate_low_similarity() -> None:
    esc, trig = should_escalate(0.3, 3)
    assert esc is True
    assert trig == EscalationTrigger.low_similarity


def test_should_escalate_no_documents() -> None:
    esc, trig = should_escalate(None, 0)
    assert esc is True
    assert trig == EscalationTrigger.no_documents


def test_should_escalate_ok() -> None:
    esc, trig = should_escalate(0.9, 2)
    assert esc is False
    assert trig is None


def _mock_llm_human_request(result: bool):
    """Patch the OpenAI call inside detect_human_request to return a fixed result."""
    import json
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, MagicMock, patch

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({"human_request": result})

    class _Stack:
        def __enter__(self):
            self._stack = ExitStack()
            self._stack.enter_context(
                patch(
                    "backend.escalation.service.get_async_openai_client",
                    return_value=MagicMock(),
                )
            )
            self._stack.enter_context(
                patch(
                    "backend.escalation.service.async_call_openai_with_retry",
                    new=AsyncMock(return_value=response),
                )
            )
            return self

        def __exit__(self, *args):
            return self._stack.__exit__(*args)

    return _Stack()


@pytest.mark.asyncio
async def test_detect_human_request_english() -> None:
    with _mock_llm_human_request(True):
        result = await detect_human_request("I need to talk to a human please", "sk-test")
        assert result.human_request is True
    with _mock_llm_human_request(True):
        result = await detect_human_request(
            "connect me to support, this is useless", "sk-test"
        )
        assert result.human_request is True


@pytest.mark.asyncio
async def test_detect_human_request_russian() -> None:
    with _mock_llm_human_request(True):
        result = await detect_human_request("хочу поговорить с человеком", "sk-test")
        assert result.human_request is True


def _mock_llm_support_contact(result: bool):
    """Patch the OpenAI call inside detect_support_contact_question."""
    import json
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, MagicMock, patch

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({"support_contact": result})

    class _Stack:
        def __enter__(self):
            self._stack = ExitStack()
            self._stack.enter_context(
                patch(
                    "backend.escalation.service.get_async_openai_client",
                    return_value=MagicMock(),
                )
            )
            self._stack.enter_context(
                patch(
                    "backend.escalation.service.async_call_openai_with_retry",
                    new=AsyncMock(return_value=response),
                )
            )
            return self

        def __exit__(self, *args):
            return self._stack.__exit__(*args)

    return _Stack()


@pytest.mark.asyncio
async def test_detect_support_contact_question_true_for_contact_question() -> None:
    from backend.escalation.service import (
        _support_contact_cache,
        detect_support_contact_question,
    )

    _support_contact_cache.clear()
    with _mock_llm_support_contact(True):
        assert (
            await detect_support_contact_question(
                "how can i write to the support?", "sk-test"
            )
            is True
        )


@pytest.mark.asyncio
async def test_detect_support_contact_question_false_for_ordinary_question() -> None:
    from backend.escalation.service import (
        _support_contact_cache,
        detect_support_contact_question,
    )

    _support_contact_cache.clear()
    with _mock_llm_support_contact(False):
        assert (
            await detect_support_contact_question(
                "how do I configure DNS records?", "sk-test"
            )
            is False
        )


@pytest.mark.asyncio
async def test_detect_human_request_cache_isolated_per_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same message must not leak a cached classification across tenants."""
    from unittest.mock import MagicMock

    import backend.escalation.service as escalation_service

    escalation_service._human_request_cache.clear()

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    message = "please help me"

    call_count = {"n": 0}

    async def _fake_call(_label, fn, **_kwargs):
        call_count["n"] += 1
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = (
            '{"human_request": true}'
            if call_count["n"] == 1
            else '{"human_request": false}'
        )
        return response

    monkeypatch.setattr(
        "backend.escalation.service.get_async_openai_client",
        lambda _api_key: MagicMock(),
    )
    monkeypatch.setattr(
        "backend.escalation.service.async_call_openai_with_retry",
        _fake_call,
    )

    # Tenant A — first call hits LLM, returns True, gets cached.
    assert (await detect_human_request(message, "sk-test", tenant_a)).human_request is True
    # Same message, different tenant — must NOT reuse A's cached True; must
    # call the LLM again (returns False per the mock).
    assert (await detect_human_request(message, "sk-test", tenant_b)).human_request is False
    assert call_count["n"] == 2

    # Tenant A again — served from cache, no extra LLM call.
    assert (await detect_human_request(message, "sk-test", tenant_a)).human_request is True
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_detect_human_request_uses_human_request_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = '{"human_request": true}'
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    monkeypatch.setattr(
        "backend.escalation.service.get_async_openai_client",
        lambda _api_key: mock_client,
    )
    monkeypatch.setattr(
        "backend.escalation.service.settings.human_request_model",
        "gpt-test-human-guard",
    )

    result = await detect_human_request(
        "please connect me to an operator now", "sk-test"
    )
    assert result.human_request is True
    assert mock_client.chat.completions.create.call_args.kwargs["model"] == "gpt-test-human-guard"


def test_compute_priority_t3_enterprise() -> None:
    p = compute_priority(
        EscalationTrigger.user_request,
        "enterprise",
        {"plan_tier": "enterprise"},
    )
    assert p == EscalationPriority.critical


def test_compute_priority_t3_default() -> None:
    p = compute_priority(EscalationTrigger.user_request, None, {})
    assert p == EscalationPriority.high


def test_parse_contact_email() -> None:
    assert (
        parse_contact_email("reach me at user@example.com thanks") == "user@example.com"
    )
    assert parse_contact_email("no email here") is None


def test_generate_ticket_number_sequential(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="esc-seq@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc Seq"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    assert generate_ticket_number(tenant_id, db_session) == "ESC-0001"

    t = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="test",
        trigger=EscalationTrigger.low_similarity,
        status=EscalationStatus.open,
    )
    db_session.add(t)
    db_session.commit()

    assert generate_ticket_number(tenant_id, db_session) == "ESC-0002"


def test_generate_ticket_number_concurrent_reads_return_same(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Two reads before any commit both return ESC-0001.

    This documents the race condition: generate_ticket_number is not atomic
    on its own. The retry loop in create_escalation_ticket is responsible for
    handling the resulting IntegrityError.
    """
    token = register_and_verify_user(
        tenant, db_session, email="esc-concurrent@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Concurrent Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    first = generate_ticket_number(tenant_id, db_session)
    second = generate_ticket_number(tenant_id, db_session)
    assert first == "ESC-0001"
    assert second == "ESC-0001"


def test_create_escalation_ticket_retries_on_integrity_error(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """create_escalation_ticket retries once when the first commit raises IntegrityError."""
    token = register_and_verify_user(tenant, db_session, email="esc-retry@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Retry Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    real_commit = db_session.commit
    call_count = [0]

    def commit_once_then_succeed():
        call_count[0] += 1
        if call_count[0] == 1:
            raise SAIntegrityError("stmt", {}, Exception("unique constraint violation"))
        return real_commit()

    with patch.object(db_session, "commit", side_effect=commit_once_then_succeed):
        ticket = create_escalation_ticket(
            tenant_id,
            "test retry question",
            EscalationTrigger.low_similarity,
            db_session,
        )

    assert ticket.ticket_number.startswith("ESC-")
    assert call_count[0] == 2


def test_create_escalation_ticket_stores_redacted_and_encrypted_question(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="esc-redact@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Redaction Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        tenant_id,
        "my email is user@example.com",
        EscalationTrigger.low_similarity,
        db_session,
    )

    assert ticket.primary_question == "my email is [EMAIL]"
    assert ticket.primary_question_redacted == "my email is [EMAIL]"
    assert ticket.primary_question_original_encrypted is not None


def test_create_escalation_ticket_raises_after_max_retries(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """After 3 failed commit attempts create_escalation_ticket re-raises IntegrityError."""
    token = register_and_verify_user(
        tenant, db_session, email="esc-maxretry@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Max Retry Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    def always_integrity_error():
        raise SAIntegrityError("stmt", {}, Exception("unique constraint violation"))

    with patch.object(db_session, "commit", side_effect=always_integrity_error):
        with pytest.raises(SAIntegrityError):
            create_escalation_ticket(
                tenant_id,
                "test max retry question",
                EscalationTrigger.low_similarity,
                db_session,
            )


def test_escalation_clarify_flags_roundtrip(db_session: Session) -> None:
    from backend.core.security import hash_password

    user = User(
        email="clarify@example.com", password_hash=hash_password("SecurePass1!")
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    cl = Tenant(name="Clarify Tenant")
    db_session.add(cl)
    db_session.commit()
    db_session.refresh(cl)

    chat = Chat(tenant_id=cl.id, session_id=uuid.uuid4(), user_context={})
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    assert _escalation_clarify_already_asked(chat) is False
    _set_escalation_clarify_flag(chat)
    assert _escalation_clarify_already_asked(chat) is True
    _clear_escalation_clarify_flag(chat)
    assert _escalation_clarify_already_asked(chat) is False


def test_apply_collected_contact_email_updates_chat_ticket_and_user_session(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="apply-email@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Apply Email Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-123", "email": None},
        escalation_followup_pending=False,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    row = ContactSession(tenant_id=tenant_id, contact_id="u-123", email=None)
    db_session.add(row)
    db_session.commit()

    with patch("backend.escalation.service.send_email"):
        apply_collected_contact_email(ticket.id, chat.id, "user@example.com", db_session)

    db_session.refresh(ticket)
    db_session.refresh(chat)
    db_session.refresh(row)
    assert ticket.user_email == "user@example.com"
    assert chat.user_context.get("email") == "user@example.com"
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is True
    assert row.email == "user@example.com"


def test_apply_collected_contact_email_rolls_back_when_user_session_sync_fails(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="apply-email-rollback@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Apply Email Rollback Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-rollback", "email": None},
        escalation_followup_pending=False,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0002",
        primary_question="need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()

    with patch(
        "backend.escalation.service.sync_user_session_identity",
        side_effect=RuntimeError("sync failed"),
    ):
        with pytest.raises(RuntimeError, match="sync failed"):
            apply_collected_contact_email(
                ticket.id, chat.id, "user@example.com", db_session
            )

    db_session.rollback()
    db_session.refresh(ticket)
    db_session.refresh(chat)
    assert ticket.user_email is None
    assert chat.user_context.get("email") is None
    assert chat.escalation_awaiting_ticket_id == ticket.id
    assert chat.escalation_followup_pending is False


def test_notify_tenant_new_ticket_uses_l2_email_when_configured(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="owner-l2@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "L2 Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    support_resp = tenant.put(
        "/tenants/me/support-settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"l2_email": "l2@example.com"},
    )
    assert support_resp.status_code == 200

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0010",
        primary_question="need help",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        user_email="enduser@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_called_once()
    assert send_email_mock.call_args.args[0] == "l2@example.com"


def test_notify_tenant_new_ticket_falls_back_to_owner_email(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="owner-only@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Owner Fallback Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0011",
        primary_question="need help",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        user_email="enduser@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_called_once()
    assert send_email_mock.call_args.args[0] == "owner-only@example.com"


def _make_tenant_for_email_test(
    tenant: TestClient, db_session: Session, *, owner_email: str
) -> Tenant:
    token = register_and_verify_user(tenant, db_session, email=owner_email)
    resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Email Body Tenant"},
    )
    assert resp.status_code == 201
    tenant_id = uuid.UUID(resp.json()["id"])
    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None
    return cl


def test_notify_email_body_contains_full_context_and_reply_to(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="ctx-owner@example.com"
    )

    chat = Chat(
        tenant_id=cl.id,
        session_id=uuid.uuid4(),
        user_context={
            "email": "enduser@acme.io",
            "name": "Ivan Petrov",
            "plan_tier": "pro",
            "user_id": "u_18422",
            "audience_tag": "paying_b2b",
            "locale": "ru-RU",
            "browser_locale": "ru-RU",
            "company": "ACME",
            "role_in_company": "ops_lead",
            "metadata": {"source": "widget"},
        },
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    db_session.add_all([
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content="How can I download last month's invoice?",
        ),
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="You can find invoices in Settings → Billing.",
        ),
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content="It's empty there.",
        ),
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="Could you confirm the billing email so I can check?",
        ),
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content="Yes please",
        ),
    ])
    db_session.commit()

    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0102",
        primary_question="Yes please",
        primary_question_redacted="Yes please",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
        user_email="enduser@acme.io",
        user_name="Ivan Petrov",
        plan_tier="pro",
        user_id="u_18422",
        user_note="I need help with the invoice from March.",
        best_similarity_score=0.31,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_called_once()
    args, kwargs = send_email_mock.call_args
    subject = args[1]
    body = args[2]
    headers = kwargs.get("extra_headers") or {}

    assert kwargs.get("reply_to") == "enduser@acme.io"

    # Subject: ticket number only, no priority tier (priority must not leak
    # back to the user through the Re: prefix on a support reply).
    assert subject.startswith("[ESC-0102]")
    assert "HIGH" not in subject
    assert "Chat9" not in subject
    assert "Yes please" in subject

    # Body must be user-safe — anything quoted back via Reply must be safe to
    # be shown to the end user. Internal metadata lives in X-Chat9-* headers.
    assert "Priority:" not in body
    assert "Trigger:" not in body
    assert "Chat ID:" not in body
    assert "Session ID:" not in body
    assert "why_escalated" not in body
    assert "best_match_score" not in body
    assert "/escalations/" not in body
    assert "Reference info" not in body
    assert "for the full audit log" not in body.lower()
    # User-only fields stay in body (already user-known: their own email, name,
    # the question they asked, the conversation they had). Plan tier / user_id
    # / KYC extras are tenant-internal classifications that must NOT leak back
    # to the user — those move to headers.
    assert "pro" not in body  # plan_tier
    assert "u_18422" not in body  # user_id
    assert "paying_b2b" not in body  # audience_tag
    assert "ACME" not in body  # KYC extra: company
    assert "ops_lead" not in body  # KYC extra: role_in_company

    # User-safe content present.
    assert "enduser@acme.io" in body
    assert "Ivan Petrov" in body
    assert "Yes please" in body  # primary question
    assert "I need help with the invoice from March." in body  # user note
    assert "How can I download last month's invoice?" in body
    assert "Could you confirm the billing email so I can check?" in body

    # Conversation renders each turn as a marked header ("▸ USER · HH:MM")
    # followed by the indented message body.
    assert "CONVERSATION (UTC)" in body
    import re as _re
    assert _re.search(r"▸ USER · \d{2}:\d{2}", body) is not None
    assert _re.search(r"· ASSISTANT · \d{2}:\d{2}", body) is not None

    # Internal metadata lives in X-Chat9-* headers.
    assert headers.get("X-Chat9-Ticket-Number") == "ESC-0102"
    assert headers.get("X-Chat9-Priority") == "high"
    assert headers.get("X-Chat9-Trigger") == "user_request"
    assert headers.get("X-Chat9-Why-Escalated") == "user_request"
    assert headers.get("X-Chat9-Plan") == "pro"
    assert headers.get("X-Chat9-User-Id") == "u_18422"
    assert headers.get("X-Chat9-Audience") == "paying_b2b"
    assert headers.get("X-Chat9-Locale") == "ru-RU"
    assert headers.get("X-Chat9-Chat-Id") == str(chat.id)
    assert headers.get("X-Chat9-Match-Score") == "0.3100"
    kyc_raw = headers.get("X-Chat9-KYC") or "{}"
    import json as _json
    kyc = _json.loads(kyc_raw)
    assert kyc.get("company") == "ACME"
    assert kyc.get("role_in_company") == "ops_lead"
    assert kyc.get("metadata") == {"source": "widget"}


def test_notify_email_skipped_when_no_user_email(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="anon-owner@example.com"
    )

    # Anonymous escalation: support cannot reply, so notification is deferred
    # until the user provides an email (fired later by apply_collected_contact_email).
    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0200",
        primary_question="Need a human",
        primary_question_redacted="Need a human",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_email_skipped_when_user_email_is_malformed(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="malformed-owner@example.com"
    )

    # Widget-supplied garbage in user_context.email must not produce a notification —
    # Brevo would reject the send (P1 from Codex review) and support gets nothing.
    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0202",
        primary_question="needs help",
        primary_question_redacted="needs help",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
        user_email="not an email",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_not_called()


def test_apply_collected_contact_email_fires_deferred_notification(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="late-notify-owner@example.com"
    )

    chat = Chat(
        tenant_id=cl.id,
        session_id=uuid.uuid4(),
        user_context={"user_id": "u-late", "email": None},
        escalation_followup_pending=False,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0500",
        primary_question="please connect me to support",
        primary_question_redacted="please connect me to support",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    chat.escalation_awaiting_ticket_id = ticket.id
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        apply_collected_contact_email(
            ticket.id, chat.id, "late@example.com", db_session
        )

    send_email_mock.assert_called_once()
    args, kwargs = send_email_mock.call_args
    assert kwargs.get("reply_to") == "late@example.com"
    assert "ESC-0500" in args[1]
    assert "late@example.com" in args[2]


def test_apply_collected_contact_email_does_not_double_notify(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="dedup-owner@example.com"
    )

    chat = Chat(
        tenant_id=cl.id,
        session_id=uuid.uuid4(),
        user_context={"email": "first@example.com"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0501",
        primary_question="anything",
        primary_question_redacted="anything",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
        user_email="first@example.com",
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    # Email already known when ticket was created → first notify already fired.
    # Updating the contact (e.g. user provides a new address) must NOT spam a
    # second notification, since the support team already got one.
    with patch("backend.escalation.service.send_email") as send_email_mock:
        apply_collected_contact_email(
            ticket.id, chat.id, "second@example.com", db_session
        )

    send_email_mock.assert_not_called()


def test_notify_email_body_appends_latest_user_text_not_yet_in_db(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The user turn that triggers escalation isn't persisted until *after*
    the notification fires (persistence ordering in the chat pipeline).
    Without ``latest_user_text``, the email transcript would miss the very
    message that caused the escalation — exactly the bug seen on ESC-0056."""
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="latest-owner@example.com"
    )

    chat = Chat(
        tenant_id=cl.id,
        session_id=uuid.uuid4(),
        user_context={"email": "u@example.com"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    db_session.add_all([
        Message(chat_id=chat.id, role=MessageRole.user, content="hi"),
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="Hello! How can I help?",
        ),
        Message(chat_id=chat.id, role=MessageRole.user, content="call a human"),
        Message(
            chat_id=chat.id,
            role=MessageRole.assistant,
            content="Would you like me to escalate?",
        ),
    ])
    db_session.commit()

    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0310",
        primary_question="yes, my invoice is broken",
        primary_question_redacted="yes, my invoice is broken",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
        user_email="u@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(
            cl,
            ticket,
            db_session,
            latest_user_text="yes, my invoice is broken",
        )

    body = send_email_mock.call_args.args[2]
    # All 4 persisted turns + the un-persisted current turn must be present.
    assert "hi" in body
    assert "call a human" in body
    assert "Would you like me to escalate?" in body
    assert "yes, my invoice is broken" in body
    # No duplication if the latest_user_text accidentally equals the last
    # persisted user message — handled by transcript dedupe. Sanity: only
    # one occurrence of the new content in the conversation block.
    convo_start = body.index("CONVERSATION (UTC)")
    assert body.count("yes, my invoice is broken", convo_start) == 1


def test_notify_tenant_new_ticket_stores_naive_last_notified_at(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Regression for the prod "Internal error" surfaced in Sentry
    ``PYTHON-FASTAPI-H`` (2026-05-13).

    Background: ``escalation_tickets.last_notified_at`` is declared as
    ``Column(DateTime, nullable=True)`` — i.e. ``TIMESTAMP WITHOUT TIME
    ZONE`` in Postgres. The notify helper used to write ``datetime.now(UTC)``
    (tz-aware) into that column. psycopg2 silently dropped ``tzinfo``;
    asyncpg (used on the ``/widget/chat`` path) rejects aware values for
    naive columns with ``DataError: can't subtract offset-naive and
    offset-aware datetimes``. The DataError put the session into
    ``PENDING_ROLLBACK`` and the very next attribute access on the ticket
    raised ``PendingRollbackError``, surfacing as a 500 in the widget.

    The fix routes every datetime that lands on a naive column through
    :func:`backend.models.base._utcnow`. This test asserts the contract on
    the notify path: after a successful notify the column value must be
    naive.
    """
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="naive-notified-at@example.com"
    )
    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-NV01",
        primary_question="need a human",
        primary_question_redacted="need a human",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.medium,
        status=EscalationStatus.open,
        user_email="enduser@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch(
        "backend.escalation.service.send_email",
        return_value="<test-message-id@example.com>",
    ):
        _notify_tenant_new_ticket(cl, ticket, db_session)

    db_session.refresh(ticket)
    assert ticket.last_notified_at is not None
    assert ticket.last_notified_at.tzinfo is None, (
        "last_notified_at must be naive UTC — column is DateTime WITHOUT TIME "
        "ZONE; asyncpg refuses aware values and surfaces as a 500 in the widget"
    )


def test_advance_notification_marker_stores_naive_last_notified_at(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Same naive-UTC contract for the ``advance_notification_marker_to_current``
    helper. Without this, the email-capture turn fixup would trip the same
    asyncpg DataError on the ``/widget/chat`` path.
    """
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="naive-advance@example.com"
    )
    chat = Chat(tenant_id=cl.id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.flush()
    # The helper bails out early if there is no persisted user message.
    db_session.add(
        Message(
            chat_id=chat.id,
            role=MessageRole.user,
            content="anchor turn",
        )
    )
    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-NV02",
        primary_question="need a human",
        primary_question_redacted="need a human",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.medium,
        status=EscalationStatus.open,
        chat_id=chat.id,
        user_email="enduser@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    advance_notification_marker_to_current(ticket, db_session)

    db_session.refresh(ticket)
    assert ticket.last_notified_at is not None
    assert ticket.last_notified_at.tzinfo is None


def test_notify_email_subject_omits_priority_tier(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="subj-owner@example.com"
    )
    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0311",
        primary_question="urgent help",
        primary_question_redacted="urgent help",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.critical,
        status=EscalationStatus.open,
        user_email="user@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    subject = send_email_mock.call_args.args[1]
    assert subject.startswith("[ESC-0311]")
    # Priority tier is internal — must not leak via the Re: prefix on a
    # support reply quoting this subject.
    for forbidden in ("CRITICAL", "Critical", "HIGH", "Chat9"):
        assert forbidden not in subject


def test_notify_email_body_omits_user_note_section_when_absent(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="no-note-owner@example.com"
    )

    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0201",
        primary_question="generic question",
        primary_question_redacted="generic question",
        trigger=EscalationTrigger.low_similarity,
        priority=EscalationPriority.medium,
        status=EscalationStatus.open,
        user_email="someone@example.com",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_called_once()
    body = send_email_mock.call_args.args[2]
    assert "USER'S NOTE" not in body


def _run_manual_escalation(db_session: Session, *args, **kwargs):
    """Drive the async ``perform_manual_escalation`` from sync tests.

    Mirrors the ``process_chat_message`` compat shim: commit the caller's sync
    session first so SQLite releases its lock before the async entry point
    opens its own connection, and expire the sync session after so subsequent
    reads see the writes made on that connection. Requires the ``tenant``
    fixture (it rebinds ``core_db.AsyncSessionLocal`` to the test engine).
    """
    from backend.core import db as core_db

    db_session.commit()

    async def _inner():
        async with core_db.AsyncSessionLocal() as adb:
            return await perform_manual_escalation(adb, *args, **kwargs)

    result = asyncio.run(_inner())
    db_session.expire_all()
    return result


def test_perform_manual_escalation_sets_awaiting_ticket_when_email_missing(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="manual-missing@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual Missing Email"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(tenant, token)

    chat = Chat(
        tenant_id=tenant_id, session_id=uuid.uuid4(), user_context={"email": None}
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None
    msg, tnum = _run_manual_escalation(
        db_session,
        cl,
        chat.session_id,
        api_key="sk-test",
        user_note="please escalate",
        trigger=EscalationTrigger.user_request,
    )

    assert tnum.startswith("ESC-")
    assert isinstance(msg, str) and msg != ""
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is not None
    assert chat.escalation_followup_pending is False

    ticket = (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.chat_id == chat.id)
        .first()
    )
    assert ticket is not None
    assert ticket.trigger == EscalationTrigger.user_request
    messages = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(messages) == 1
    assert messages[0].role == MessageRole.assistant


def test_perform_manual_escalation_sets_followup_when_email_known(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="manual-known@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Manual Known Email"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(tenant, token)

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"email": "known@example.com"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None
    _msg, tnum = _run_manual_escalation(
        db_session,
        cl,
        chat.session_id,
        api_key="sk-test",
        user_note="answer rejected",
        trigger=EscalationTrigger.answer_rejected,
    )
    assert tnum.startswith("ESC-")
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is True
    ticket = (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.chat_id == chat.id)
        .first()
    )
    assert ticket is not None
    assert ticket.trigger == EscalationTrigger.answer_rejected


def test_escalation_api_returns_safe_question_by_default(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="esc-api@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc API Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        tenant_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )

    resp = tenant.get(
        f"/escalations/{ticket.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["primary_question"] == "contact me at [EMAIL]"
    assert data["primary_question_original"] is None
    assert data["primary_question_original_available"] is True


def test_escalation_api_can_include_original_question(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="esc-api-orig@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc API Orig Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        tenant_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )
    user = (
        db_session.query(User).filter(User.email == "esc-api-orig@example.com").first()
    )
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = tenant.get(
        f"/escalations/{ticket.id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["primary_question"] == "contact me at [EMAIL]"
    assert data["primary_question_original"] == "contact me at user@example.com"


def test_escalation_api_include_original_requires_admin(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="esc-api-no-admin@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc API No Admin Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        tenant_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )

    resp = tenant.get(
        f"/escalations/{ticket.id}?include_original=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_delete_escalation_original_requires_admin_and_removes_original(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="esc-delete@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc Delete Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    ticket = create_escalation_ticket(
        tenant_id,
        "contact me at user@example.com",
        EscalationTrigger.user_request,
        db_session,
    )

    denied = tenant.post(
        f"/escalations/{ticket.id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403

    user = db_session.query(User).filter(User.email == "esc-delete@example.com").first()
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = tenant.post(
        f"/escalations/{ticket.id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted_count"] == 1

    db_session.refresh(ticket)
    assert ticket.primary_question_original_encrypted is None
    assert ticket.primary_question == ticket.primary_question_redacted


def test_delete_escalation_original_clears_legacy_plaintext_when_redacted_missing(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from backend.core.crypto import encrypt_value

    token = register_and_verify_user(
        tenant, db_session, email="esc-delete-empty@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Esc Delete Empty Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="secret@example.com",
        primary_question_original_encrypted=encrypt_value("secret@example.com"),
        primary_question_redacted=None,
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
    )
    db_session.add(ticket)
    db_session.commit()

    user = (
        db_session.query(User)
        .filter(User.email == "esc-delete-empty@example.com")
        .first()
    )
    assert user is not None
    user.is_admin = True
    db_session.add(user)
    db_session.commit()

    resp = tenant.post(
        f"/escalations/{ticket.id}/delete-original",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db_session.refresh(ticket)
    assert ticket.primary_question_original_encrypted is None
    assert ticket.primary_question == ""


def test_perform_manual_escalation_emits_chat_escalated_event(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def fake_capture(event, **kwargs):
        captured.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.events.capture_event", fake_capture)

    token = register_and_verify_user(tenant, db_session, email="evt-manual@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Event Manual Escalation"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    set_client_openai_key(tenant, token)

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"email": "user@example.com", "plan_tier": "pro"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None

    _run_manual_escalation(
        db_session,
        cl,
        chat.session_id,
        api_key="sk-test",
        user_note="I need help",
        trigger=EscalationTrigger.user_request,
        bot_public_id="bot_abc",
    )

    escalated_events = [e for e in captured if e["event"] == "chat_escalated"]
    assert len(escalated_events) == 1
    props = escalated_events[0]["properties"]
    assert props["escalation_reason"] == "user_request"
    assert props["escalation_trigger"] == "user_request"
    assert escalated_events[0].get("bot_id") == "bot_abc"


# ---------------------------------------------------------------------------
# Follow-up update emails (threaded notifies for new turns post-handoff).
# ---------------------------------------------------------------------------


def _setup_followup_fixture(
    tenant: TestClient,
    db_session: Session,
    *,
    owner_email: str,
    notification_message_id: str | None = "<initial-abc@brevo>",
) -> tuple[Tenant, Chat, EscalationTicket]:
    token = register_and_verify_user(tenant, db_session, email=owner_email)
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Followup Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    cl = db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
    assert cl is not None

    chat = Chat(
        tenant_id=tenant_id,
        session_id=uuid.uuid4(),
        user_context={"email": "enduser@acme.io"},
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-9001",
        primary_question="i cannot log in",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
        user_email="enduser@acme.io",
        notification_message_id=notification_message_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return cl, chat, ticket


def _persist_user_message(db_session: Session, chat: Chat, content: str) -> Message:
    msg = Message(chat_id=chat.id, role=MessageRole.user, content=content)
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


def test_notify_ticket_update_threads_under_initial_notify(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="thread-owner@example.com"
    )
    msg = _persist_user_message(db_session, chat, "also locked out of my admin account")

    with patch("backend.escalation.service.send_email") as send_email_mock:
        send_email_mock.return_value = "<update-xyz@brevo>"
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_called_once()
    subject = send_email_mock.call_args.args[1]
    body = send_email_mock.call_args.args[2]
    headers = send_email_mock.call_args.kwargs["extra_headers"]
    assert subject.startswith("Re: [ESC-9001]")
    assert headers["In-Reply-To"] == "<initial-abc@brevo>"
    assert headers["References"] == "<initial-abc@brevo>"
    assert "X-Chat9-Ticket-Number" in headers
    assert "also locked out of my admin account" in body

    db_session.refresh(ticket)
    assert ticket.last_notified_message_id == msg.id


def test_notify_ticket_update_stores_naive_last_notified_at(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Same naive-UTC contract as the initial notify (Sentry
    ``PYTHON-FASTAPI-H``): ``_notify_tenant_ticket_update`` also assigns to
    ``ticket.last_notified_at`` (line 821 after the ``send_email`` call).
    Computes ``now = datetime.now(UTC)`` for the debounce arithmetic, then
    writes ``_utcnow()`` to the column — aware-for-math, naive-for-storage.
    """
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="naive-update@example.com"
    )
    _persist_user_message(db_session, chat, "follow-up turn after handoff")

    with patch(
        "backend.escalation.service.send_email",
        return_value="<update-msgid@example.com>",
    ):
        _notify_tenant_ticket_update(ticket, db_session)

    db_session.refresh(ticket)
    assert ticket.last_notified_at is not None
    assert ticket.last_notified_at.tzinfo is None


def test_notify_ticket_update_sends_only_new_turns_as_delta(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="delta-owner@example.com"
    )
    from datetime import UTC, datetime, timedelta

    old = _persist_user_message(db_session, chat, "OLD CONTEXT already notified")
    ticket.last_notified_message_id = old.id
    ticket.last_notified_at = datetime.now(UTC) - timedelta(
        seconds=_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS + 30
    )
    db_session.add(ticket)
    db_session.commit()

    _persist_user_message(db_session, chat, "BRAND NEW context turn one")
    _persist_user_message(db_session, chat, "BRAND NEW context turn two")

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    body = send_email_mock.call_args.args[2]
    assert "OLD CONTEXT already notified" not in body
    assert "BRAND NEW context turn one" in body
    assert "BRAND NEW context turn two" in body


def test_notify_ticket_update_debounces_within_window(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from datetime import UTC, datetime, timedelta

    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="debounce-owner@example.com"
    )
    _persist_user_message(db_session, chat, "first follow-up message")
    ticket.last_notified_at = datetime.now(UTC) - timedelta(
        seconds=_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS - 5
    )
    db_session.add(ticket)
    db_session.commit()

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_ticket_update_skips_when_no_initial_message_id(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, chat, ticket = _setup_followup_fixture(
        tenant,
        db_session,
        owner_email="anchor-owner@example.com",
        notification_message_id=None,
    )
    _persist_user_message(db_session, chat, "new context but no anchor")

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_ticket_update_skips_when_ticket_resolved(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="resolved-owner@example.com"
    )
    ticket.status = EscalationStatus.resolved
    db_session.add(ticket)
    db_session.commit()
    _persist_user_message(db_session, chat, "post-resolution chatter")

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_ticket_update_skips_when_chat_ended(
    tenant: TestClient,
    db_session: Session,
) -> None:
    from datetime import UTC, datetime

    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="ended-owner@example.com"
    )
    chat.ended_at = datetime.now(UTC)
    db_session.add(chat)
    db_session.commit()
    _persist_user_message(db_session, chat, "post-end message")

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_ticket_update_noop_when_no_new_turns(
    tenant: TestClient,
    db_session: Session,
) -> None:
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="noturns-owner@example.com"
    )
    only = _persist_user_message(db_session, chat, "the only turn already notified")
    ticket.last_notified_message_id = only.id
    ticket.last_notified_at = only.created_at
    db_session.add(ticket)
    db_session.commit()

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_new_ticket_captures_message_id_from_send_email(
    tenant: TestClient,
    db_session: Session,
) -> None:
    cl, chat, ticket = _setup_followup_fixture(
        tenant,
        db_session,
        owner_email="capture-id-owner@example.com",
        notification_message_id=None,
    )

    with patch("backend.escalation.service.send_email") as send_email_mock:
        send_email_mock.return_value = "<brand-new-id@brevo>"
        _notify_tenant_new_ticket(cl, ticket, db_session)

    db_session.refresh(ticket)
    assert ticket.notification_message_id == "<brand-new-id@brevo>"
    assert ticket.last_notified_at is not None


def test_advance_notification_marker_to_current_skips_persisted_turn(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Mimics the email-capture flow: initial notify bundled the current turn
    via ``latest_user_text``; the marker advance prevents a follow-up notify
    from re-sending that same turn under the threaded reply.
    """
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="advance-owner@example.com"
    )
    persisted = _persist_user_message(
        db_session, chat, "current turn bundled in initial body"
    )
    advance_notification_marker_to_current(ticket, db_session)
    db_session.refresh(ticket)
    assert ticket.last_notified_message_id == persisted.id

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


def test_notify_new_ticket_does_not_advance_markers_on_send_failure(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Brevo HTTP 4xx/5xx returns ``None`` from ``send_email``. The notify
    function must NOT set ``last_notified_*`` in that case — leaving them
    NULL keeps the ticket eligible for retry by ``apply_collected_contact_email``
    and prevents permanent suppression via missing-anchor skip in updates.
    """
    cl, chat, ticket = _setup_followup_fixture(
        tenant,
        db_session,
        owner_email="failure-owner@example.com",
        notification_message_id=None,
    )
    assert ticket.last_notified_at is None
    assert ticket.last_notified_message_id is None

    with patch("backend.escalation.service.send_email") as send_email_mock:
        send_email_mock.return_value = None
        _notify_tenant_new_ticket(cl, ticket, db_session)

    db_session.refresh(ticket)
    assert ticket.notification_message_id is None
    assert ticket.last_notified_at is None
    assert ticket.last_notified_message_id is None


def test_notify_ticket_update_does_not_advance_marker_on_send_failure(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """A failed update send must leave ``last_notified_message_id`` untouched
    so the delta is retried on the next eligible user turn.
    """
    from datetime import UTC, datetime, timedelta

    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="retry-owner@example.com"
    )
    ticket.last_notified_at = datetime.now(UTC) - timedelta(
        seconds=_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS + 30
    )
    db_session.add(ticket)
    db_session.commit()
    pre_marker = ticket.last_notified_message_id

    _persist_user_message(db_session, chat, "context that fails to send")

    with patch("backend.escalation.service.send_email") as send_email_mock:
        send_email_mock.return_value = None
        _notify_tenant_ticket_update(ticket, db_session)

    db_session.refresh(ticket)
    assert ticket.last_notified_message_id == pre_marker


def test_notify_ticket_update_skips_yes_no_admin_replies_via_handler(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """The escalation handler only calls update-notify on decision == ``unclear``
    (substantive content). Yes/no replies short-circuit before the notify call,
    so the support inbox stays free of admin chatter. Asserted here as a
    state-level invariant rather than via the full handler stack.
    """
    _, chat, ticket = _setup_followup_fixture(
        tenant, db_session, owner_email="yesno-owner@example.com"
    )
    # User answered "yes" / "no" — no notify expected because the handler
    # never reaches the notify call site for those branches.
    yes_msg = _persist_user_message(db_session, chat, "да")
    # Simulate what the handler would do: advance marker past this turn since
    # it is administrative and is not forwarded to support.
    ticket.last_notified_message_id = yes_msg.id
    ticket.last_notified_at = yes_msg.created_at
    db_session.add(ticket)
    db_session.commit()

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_ticket_update(ticket, db_session)

    send_email_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Pre-confirm static template + narrow classifier
#
# Regression suite for the prod bug visible in the user's screenshot
# ("Ваш запрос передан … Хотите, чтобы я передал?"): the general escalation
# LLM was leaking handoff-phase wording into the pre_confirm reply because
# both phases shared one system prompt. The fix renders pre_confirm copy
# from canonical English templates via ``async_localize_text_to_language_result``
# and isolates the yes/no/unclear decision into a narrow classifier whose
# output schema has no ``message_to_user`` slot at all.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_pre_confirm_text_initial_localizes_canonical_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial pre_confirm reply must come from the canonical English text,
    not from the general escalation LLM. This is the bug source: the LLM was
    free to compose its own message that mixed phases.
    """
    from backend.chat.language import LocalizationResult
    from backend.escalation.openai_escalation import (
        PRE_CONFIRM_QUESTION_EN,
        render_pre_confirm_text,
    )

    captured: dict[str, object] = {}

    async def _fake_localize(**kwargs: object) -> LocalizationResult:
        captured.update(kwargs)
        return LocalizationResult(text="LOCALIZED", tokens_used=7)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    out = await render_pre_confirm_text(
        variant="initial",
        response_language="ru",
        api_key="sk-test",
    )

    assert captured["canonical_text"] == PRE_CONFIRM_QUESTION_EN
    assert captured["target_language"] == "ru"
    assert out.message_to_user == "LOCALIZED"
    assert out.followup_decision is None
    assert out.tokens_used == 7


@pytest.mark.asyncio
async def test_render_pre_confirm_text_declined_and_clarify_use_distinct_canonicals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The variants must localize different canonical strings.
    Otherwise the ``no`` and ``unclear`` branches would echo the same text
    as the initial question and the UX would look like a stuck loop. The
    ``no_answer`` variant additionally leads with a "couldn't find an answer"
    preamble, distinct from the bare ``initial`` question.
    """
    from backend.chat.language import LocalizationResult
    from backend.escalation.openai_escalation import (
        PRE_CONFIRM_CLARIFY_EN,
        PRE_CONFIRM_DECLINED_EN,
        PRE_CONFIRM_NO_ANSWER_EN,
        PRE_CONFIRM_QUESTION_EN,
        PRE_CONFIRM_SUPPORT_CONTACT_EN,
        render_pre_confirm_text,
    )

    seen: list[str] = []

    async def _fake_localize(*, canonical_text: str, **_kwargs: object) -> LocalizationResult:
        seen.append(canonical_text)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    await render_pre_confirm_text(variant="initial", response_language="en", api_key="k")
    await render_pre_confirm_text(variant="no_answer", response_language="en", api_key="k")
    await render_pre_confirm_text(variant="support_contact", response_language="en", api_key="k")
    await render_pre_confirm_text(variant="clarify", response_language="en", api_key="k")
    await render_pre_confirm_text(variant="declined", response_language="en", api_key="k")

    assert seen == [
        PRE_CONFIRM_QUESTION_EN,
        PRE_CONFIRM_NO_ANSWER_EN,
        PRE_CONFIRM_SUPPORT_CONTACT_EN,
        PRE_CONFIRM_CLARIFY_EN,
        PRE_CONFIRM_DECLINED_EN,
    ]
    assert len(set(seen)) == 5, "five variants must use five distinct canonicals"
    # The support-contact lead-in must NOT claim the bot couldn't find an answer:
    # the bot itself is the support channel, so framing it as a failure is wrong.
    assert "couldn't find" not in PRE_CONFIRM_SUPPORT_CONTACT_EN.lower()


def _fake_pre_confirm_context_client(content: str, tokens: int = 11) -> object:
    """Fake OpenAI client whose completions.create returns ``content``."""

    class _FakeMessage:
        pass

    _FakeMessage.content = content

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        total_tokens = tokens

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    class _FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs: object) -> object:
                    _FakeClient.last_create_kwargs = kwargs
                    return _FakeResponse()

    return _FakeClient


@pytest.mark.asyncio
async def test_render_pre_confirm_text_context_aware_summarizes_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a transcript is passed for a bot-initiated variant, the offer is
    drafted by the narrow context-aware call (grounded in the dialog) instead
    of the canonical template, and the result is never cached (86exn3x9u)."""
    from backend.escalation.openai_escalation import (
        _PRE_CONFIRM_RENDER_CACHE,
        render_pre_confirm_text,
    )

    fake_client = _fake_pre_confirm_context_client(
        '{"message_to_user": "I see your PDF is stuck in Processing — '
        'shall I forward a summary of your case to our support team?"}'
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: fake_client,
    )

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    def _no_localize(**_kwargs: object) -> object:
        raise AssertionError("context-aware path must not localize the template")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _no_localize,
    )

    transcript = [
        {"role": "user", "content": "загрузил pdf, статус Processing"},
        {"role": "assistant", "content": "попробуйте перезагрузить"},
        {"role": "user", "content": "уже полчаса в Processing, что делать?"},
    ]
    out = await render_pre_confirm_text(
        variant="no_answer",
        response_language="ru",
        api_key="sk-test",
        chat_messages=transcript,
    )

    assert "stuck in Processing" in out.message_to_user
    assert out.followup_decision is None
    assert out.tokens_used == 11
    assert not _PRE_CONFIRM_RENDER_CACHE, "dialog-specific text must not be cached"
    # The drafting prompt must carry the transcript and the response language.
    sent = fake_client.last_create_kwargs["messages"]
    assert "уже полчаса в Processing" in sent[1]["content"]
    assert "RESPONSE_LANGUAGE:\nru" in sent[1]["content"]


@pytest.mark.asyncio
async def test_render_pre_confirm_text_context_failure_degrades_to_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any failure of the context-aware call (API error, empty message) must
    fall back to the canonical-template localization, never raise."""
    from backend.chat.language import LocalizationResult
    from backend.escalation.openai_escalation import (
        PRE_CONFIRM_NO_ANSWER_EN,
        render_pre_confirm_text,
    )

    def _broken_client(*_a: object, **_k: object) -> object:
        raise RuntimeError("openai down")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        _broken_client,
    )

    localized: list[str] = []

    async def _fake_localize(*, canonical_text: str, **_kwargs: object) -> LocalizationResult:
        localized.append(canonical_text)
        return LocalizationResult(text="LOCALIZED FALLBACK", tokens_used=3)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    out = await render_pre_confirm_text(
        variant="no_answer",
        response_language="ru",
        api_key="sk-test",
        chat_messages=[{"role": "user", "content": "вопрос"}],
    )

    assert out.message_to_user == "LOCALIZED FALLBACK"
    assert localized == [PRE_CONFIRM_NO_ANSWER_EN]


@pytest.mark.asyncio
async def test_render_pre_confirm_text_admin_variants_ignore_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clarify/declined/initial are direct reactions to the user's escalation
    answer — they stay templated even when a transcript is passed."""
    from backend.chat.language import LocalizationResult
    from backend.escalation.openai_escalation import (
        PRE_CONFIRM_CLARIFY_EN,
        PRE_CONFIRM_DECLINED_EN,
        PRE_CONFIRM_QUESTION_EN,
        render_pre_confirm_text,
    )

    def _no_llm(*_a: object, **_k: object) -> object:
        raise AssertionError("administrative variants must not hit the drafting LLM")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        _no_llm,
    )

    seen: list[str] = []

    async def _fake_localize(*, canonical_text: str, **_kwargs: object) -> LocalizationResult:
        seen.append(canonical_text)
        return LocalizationResult(text=canonical_text, tokens_used=1)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    transcript = [{"role": "user", "content": "проблема"}]
    for variant in ("initial", "clarify", "declined"):
        await render_pre_confirm_text(
            variant=variant,  # type: ignore[arg-type]
            response_language="ru",
            api_key="sk-test",
            chat_messages=transcript,
        )

    assert seen == [
        PRE_CONFIRM_QUESTION_EN,
        PRE_CONFIRM_CLARIFY_EN,
        PRE_CONFIRM_DECLINED_EN,
    ]


@pytest.mark.asyncio
async def test_detect_human_request_empty_message_skips_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bootstrap (empty) message is answered deterministically without an
    OpenAI call — nothing to classify."""
    from backend.escalation import service as esc_service

    def _fail(*args, **kwargs):
        raise AssertionError("empty message must not reach the OpenAI client")

    monkeypatch.setattr("backend.escalation.service.get_async_openai_client", _fail)

    for message in ("", "   ", "\n\t"):
        result = await esc_service.detect_human_request(message, "sk-test")
        assert result.human_request is False
        assert result.message_has_request_content is False
        assert await esc_service.detect_support_contact_question(message, "sk-test") is False


@pytest.mark.asyncio
async def test_render_pre_confirm_text_caches_localization_per_variant_and_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical templates are static, so a successful localization is
    reused for the process lifetime — the localization LLM call is paid at most
    once per (variant, language)."""
    from backend.chat.language import LocalizationResult
    from backend.escalation.openai_escalation import render_pre_confirm_text

    calls: list[tuple[str, str]] = []

    async def _fake_localize(*, canonical_text: str, target_language: str, **_kwargs: object) -> LocalizationResult:
        calls.append((canonical_text, target_language))
        return LocalizationResult(text=f"[{target_language}] {canonical_text}", tokens_used=7)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    first = await render_pre_confirm_text(variant="initial", response_language="ru", api_key="k")
    second = await render_pre_confirm_text(variant="initial", response_language="ru", api_key="k")
    assert len(calls) == 1, "second render of the same (variant, language) must hit the cache"
    assert second.message_to_user == first.message_to_user
    assert second.tokens_used == 0

    await render_pre_confirm_text(variant="initial", response_language="de", api_key="k")
    await render_pre_confirm_text(variant="declined", response_language="ru", api_key="k")
    assert len(calls) == 3, "a different language or variant is localized separately"


@pytest.mark.asyncio
async def test_render_pre_confirm_text_does_not_cache_degraded_localization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 0-token result for a non-English target means the localization helper
    degraded (missing key / failure) — it must be retried, not pinned."""
    from backend.chat.language import LocalizationResult
    from backend.escalation.openai_escalation import render_pre_confirm_text

    calls: list[str] = []

    async def _fake_localize(*, canonical_text: str, **_kwargs: object) -> LocalizationResult:
        calls.append(canonical_text)
        return LocalizationResult(text=canonical_text, tokens_used=0)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    await render_pre_confirm_text(variant="initial", response_language="ru", api_key="k")
    await render_pre_confirm_text(variant="initial", response_language="ru", api_key="k")
    assert len(calls) == 2, "degraded localization must not be cached"


def test_pre_confirm_fallback_result_returns_canonical_text() -> None:
    from backend.escalation.openai_escalation import (
        PRE_CONFIRM_NO_ANSWER_EN,
        pre_confirm_fallback_result,
    )

    out = pre_confirm_fallback_result("no_answer")
    assert out.message_to_user == PRE_CONFIRM_NO_ANSWER_EN
    assert out.tokens_used == 0
    assert out.followup_decision is None


@pytest.mark.asyncio
async def test_classify_pre_confirm_reply_parses_decision_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Narrow classifier returns only the decision label; never a message."""
    from backend.escalation.openai_escalation import classify_pre_confirm_reply

    class _FakeMessage:
        content = '{"decision": "yes"}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        total_tokens = 4

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    class _FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**_kwargs: object) -> object:
                    return _FakeResponse()

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _FakeClient(),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    decision, tokens = await classify_pre_confirm_reply(
        latest_user_text="yes please",
        api_key="sk-test",
    )
    assert decision == "yes"
    assert tokens == 4


@pytest.mark.asyncio
async def test_classify_pre_confirm_reply_returns_none_for_non_yes_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A substantive non-yes/no reply (e.g. user describes a new problem) is
    surfaced as ``None`` so the caller can degrade to the unclear/re-ask
    path rather than treat random content as an accept/decline.
    """
    from backend.escalation.openai_escalation import classify_pre_confirm_reply

    class _FakeMessage:
        content = '{"decision": null}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        total_tokens = 3

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    class _FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**_kwargs: object) -> object:
                    return _FakeResponse()

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _FakeClient(),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    decision, tokens = await classify_pre_confirm_reply(
        latest_user_text="my site is down with a 502",
        api_key="sk-test",
    )
    assert decision is None
    assert tokens == 3


@pytest.mark.asyncio
async def test_classify_pre_confirm_reply_fails_safe_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API blowups must degrade to ``("unclear", 0)`` rather than raise — and
    must NOT degrade to ``None``. ``None`` makes the handler drop the
    pre_confirm gate and fall through to RAG; on a transient outage that would
    ignore a real yes/no and skip handoff. ``unclear`` re-asks and keeps the
    gate, which is the safe fallback when we can't classify confidently.
    """
    from backend.escalation.openai_escalation import classify_pre_confirm_reply

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("openai down")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client", _boom
    )

    decision, tokens = await classify_pre_confirm_reply(
        latest_user_text="yes",
        api_key="sk-test",
    )
    assert decision == "unclear"
    assert tokens == 0


def _fake_followup_classifier_client(content: str, tokens: int = 5):
    """Fake async OpenAI client whose completions.create returns ``content``."""

    class _FakeMessage:
        pass

    _FakeMessage.content = content

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        total_tokens = tokens

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    class _FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**_kwargs: object) -> object:
                    return _FakeResponse()

    return _FakeClient()


@pytest.mark.asyncio
async def test_classify_followup_reply_parses_new_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The follow-up gate surfaces ``new_question`` so the handler can clear
    the gate and fall through to RAG on the same turn."""
    from backend.escalation.openai_escalation import classify_followup_reply

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _fake_followup_classifier_client(
            '{"decision": "new_question"}', tokens=6
        ),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    decision, tokens = await classify_followup_reply(
        latest_user_text="do you support wildcard domain names?",
        api_key="sk-test",
    )
    assert decision == "new_question"
    assert tokens == 6


@pytest.mark.asyncio
async def test_classify_followup_reply_unrecognized_degrades_to_unclear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrecognized labels must NOT fall through to RAG: anything outside the
    known set degrades to ``unclear`` so the existing follow-up flow runs."""
    from backend.escalation.openai_escalation import classify_followup_reply

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _fake_followup_classifier_client('{"decision": "maybe"}'),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    decision, _ = await classify_followup_reply(
        latest_user_text="hmm",
        api_key="sk-test",
    )
    assert decision == "unclear"


@pytest.mark.asyncio
async def test_classify_followup_reply_fails_safe_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API blowups degrade to ``("unclear", 0)`` — never raise, never drop the
    follow-up gate (which would skip closing the chat on a real "no")."""
    from backend.escalation.openai_escalation import classify_followup_reply

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("openai down")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client", _boom
    )

    decision, tokens = await classify_followup_reply(
        latest_user_text="no thanks",
        api_key="sk-test",
    )
    assert decision == "unclear"
    assert tokens == 0


@pytest.mark.asyncio
async def test_escalation_turn_uses_dedicated_client_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation LLM calls must request the dedicated short read timeout.

    The general client default is 60s; without the override a single slow
    OpenAI response stalls the escalation turn for up to a minute (observed
    21.7s in prod before the cap).
    """
    from backend.core.config import settings
    from backend.escalation.openai_escalation import complete_escalation_openai_turn
    from backend.models import EscalationPhase

    seen_timeouts: list[object] = []

    class _FakeMessage:
        content = '{"message_to_user": "ok", "followup_decision": null}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = None

    class _FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**_kwargs: object) -> object:
                    return _FakeResponse()

    def _fake_get_client(*_a: object, **kwargs: object) -> object:
        seen_timeouts.append(kwargs.get("timeout"))
        return _FakeClient()

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        _fake_get_client,
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    out = await complete_escalation_openai_turn(
        phase=EscalationPhase.handoff_email_known,
        chat_messages=[],
        fact_json={},
        latest_user_text="hi",
        api_key="sk-test",
    )
    assert out.message_to_user == "ok"
    assert seen_timeouts == [settings.escalation_openai_timeout_seconds]


@pytest.mark.asyncio
async def test_escalation_turn_fallback_localization_is_deadline_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the completion fails AND the fallback localization hangs, the turn
    must still resolve within the escalation deadline with the canonical
    English fallback — not stall for the localization client's 60s timeout.
    """
    from backend.escalation.openai_escalation import (
        FALLBACK_EN_GENERIC,
        complete_escalation_openai_turn,
    )
    from backend.models import EscalationPhase

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("openai down")

    async def _hanging_localize(**_k: object) -> object:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client", _boom
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _hanging_localize,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.escalation_openai_timeout_seconds", 0.05
    )

    out = await asyncio.wait_for(
        complete_escalation_openai_turn(
            phase=EscalationPhase.handoff_ask_email,
            chat_messages=[],
            fact_json={},
            latest_user_text="hi",
            api_key="sk-test",
            response_language="ru",
        ),
        timeout=5,
    )
    assert out.message_to_user == FALLBACK_EN_GENERIC
    assert out.tokens_used == 0
    assert out.followup_decision is None


@pytest.mark.asyncio
async def test_escalation_turn_empty_message_uses_bounded_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty message_to_user from the model goes through the same bounded
    localized-fallback path as an API failure."""
    from backend.escalation.openai_escalation import (
        FALLBACK_EN_GENERIC,
        complete_escalation_openai_turn,
    )
    from backend.models import EscalationPhase

    class _FakeMessage:
        content = '{"message_to_user": "", "followup_decision": null}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeUsage:
        total_tokens = 7

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    class _FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**_kwargs: object) -> object:
                    return _FakeResponse()

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    async def _hanging_localize(**_k: object) -> object:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _FakeClient(),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _hanging_localize,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.escalation_openai_timeout_seconds", 0.05
    )

    out = await asyncio.wait_for(
        complete_escalation_openai_turn(
            phase=EscalationPhase.handoff_email_known,
            chat_messages=[],
            fact_json={},
            latest_user_text="hi",
            api_key="sk-test",
            response_language="ru",
        ),
        timeout=5,
    )
    assert out.message_to_user == FALLBACK_EN_GENERIC
    # Completion tokens are still counted; the degraded localization adds 0.
    assert out.tokens_used == 7
