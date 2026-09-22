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
from tests.conftest import register_and_verify_user


@pytest.mark.smoke
def test_should_escalate_low_similarity() -> None:
    esc, trig = should_escalate(0.3, 3)
    assert esc is True
    assert trig == EscalationTrigger.low_similarity


@pytest.mark.smoke
def test_should_escalate_no_documents() -> None:
    esc, trig = should_escalate(None, 0)
    assert esc is True
    assert trig == EscalationTrigger.no_documents


@pytest.mark.smoke
def test_should_escalate_ok() -> None:
    esc, trig = should_escalate(0.9, 2)
    assert esc is False
    assert trig is None


def _mock_llm_human_request(result: bool):
    """Patch the OpenAI call inside detect_human_request to return a fixed result."""
    return _mock_llm_human_request_payload({"human_request": result})


def _mock_llm_human_request_payload(payload: dict):
    """Same, with the full classifier JSON body spelled out by the caller."""
    import json
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, MagicMock, patch

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)

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
@pytest.mark.smoke
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
@pytest.mark.smoke
async def test_detect_human_request_russian() -> None:
    with _mock_llm_human_request(True):
        result = await detect_human_request("хочу поговорить с человеком", "sk-test")
        assert result.human_request is True


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_detect_human_request_explicitness_axis_is_parsed() -> None:
    """A handoff the classifier only inferred comes back flagged as such.

    The caller uses this to answer a stated problem from the knowledge base
    instead of escalating it — see EscalationStateMachine's implied-request
    fall-through.
    """
    with _mock_llm_human_request_payload(
        {
            "human_request": True,
            "message_has_request_content": True,
            "human_request_explicit": False,
        }
    ):
        result = await detect_human_request("не могу менять настройки", "sk-test")
    assert result.human_request is True
    assert result.message_has_request_content is True
    assert result.human_request_explicit is False


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_detect_human_request_defaults_to_explicit_when_axis_missing() -> None:
    """A response without the third axis keeps the original escalate-now contract."""
    with _mock_llm_human_request_payload(
        {"human_request": True, "message_has_request_content": True}
    ):
        result = await detect_human_request("соедините с оператором", "sk-test")
    assert result.human_request is True
    assert result.human_request_explicit is True


def _mock_llm_question_intent(**flags: bool):
    """Patch the OpenAI call inside classify_question_intent."""
    import json
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, MagicMock, patch

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(flags)

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
@pytest.mark.smoke
async def test_classify_question_intent_true_for_contact_question() -> None:
    from backend.escalation.service import (
        _question_intent_cache,
        classify_question_intent,
    )

    _question_intent_cache.clear()
    with _mock_llm_question_intent(support_contact=True):
        result = await classify_question_intent(
            "how can i write to the support?", "sk-test"
        )
    assert result.support_contact is True
    assert result.pricing is False


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_classify_question_intent_false_for_ordinary_question() -> None:
    from backend.escalation.service import (
        _question_intent_cache,
        classify_question_intent,
    )

    _question_intent_cache.clear()
    with _mock_llm_question_intent(support_contact=False):
        result = await classify_question_intent(
            "how do I configure DNS records?", "sk-test"
        )
    assert result.support_contact is False
    assert result.pricing is False
    assert result.service_status is False
    assert result.documentation is False


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_classify_question_intent_reports_every_axis() -> None:
    from backend.escalation.service import (
        _question_intent_cache,
        classify_question_intent,
    )

    _question_intent_cache.clear()
    with _mock_llm_question_intent(
        support_contact=False, pricing=True, service_status=True, documentation=True
    ):
        result = await classify_question_intent("...", "sk-test")
    assert (result.pricing, result.service_status, result.documentation) == (
        True,
        True,
        True,
    )
    assert result.support_contact is False


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_classify_question_intent_fails_safe_to_all_false() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.escalation.service import (
        _question_intent_cache,
        classify_question_intent,
    )

    _question_intent_cache.clear()
    with patch(
        "backend.escalation.service.get_async_openai_client", return_value=MagicMock()
    ), patch(
        "backend.escalation.service.async_call_openai_with_retry",
        new=AsyncMock(side_effect=RuntimeError("provider down")),
    ):
        result = await classify_question_intent("any question", "sk-test")
    assert result.support_contact is False
    assert result.pricing is False
    assert result.service_status is False
    assert result.documentation is False


@pytest.mark.asyncio
@pytest.mark.smoke
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
@pytest.mark.smoke
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


@pytest.mark.smoke
def test_compute_priority_t3_enterprise() -> None:
    p = compute_priority(
        EscalationTrigger.user_request,
        "enterprise",
        {"plan_tier": "enterprise"},
    )
    assert p == EscalationPriority.critical


@pytest.mark.smoke
def test_compute_priority_t3_default() -> None:
    p = compute_priority(EscalationTrigger.user_request, None, {})
    assert p == EscalationPriority.high


@pytest.mark.smoke
def test_parse_contact_email() -> None:
    assert (
        parse_contact_email("reach me at user@example.com thanks") == "user@example.com"
    )
    assert parse_contact_email("no email here") is None


@pytest.mark.smoke
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


@pytest.mark.smoke
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


@pytest.mark.smoke
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


@pytest.mark.smoke
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

    # Storage keeps the original wording; redaction happens on the way out.
    assert ticket.primary_question == "my email is user@example.com"


@pytest.mark.smoke
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


@pytest.mark.smoke
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


@pytest.mark.smoke
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


@pytest.mark.smoke
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




@pytest.mark.smoke
def test_notify_email_body_handles_already_masked_legacy_question(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """Rows written before the move already hold masked text — no crash, no leak."""
    cl = _make_tenant_for_email_test(
        tenant, db_session, owner_email="fallback-owner@example.com"
    )
    ticket = EscalationTicket(
        tenant_id=cl.id,
        ticket_number="ESC-0104",
        primary_question="contact me at [EMAIL]",
        trigger=EscalationTrigger.user_request,
        priority=EscalationPriority.high,
        status=EscalationStatus.open,
        user_email="enduser@acme.io",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    with patch("backend.escalation.service.send_email") as send_email_mock:
        _notify_tenant_new_ticket(cl, ticket, db_session)

    send_email_mock.assert_called_once()
    body = send_email_mock.call_args.args[2]
    assert "contact me at [EMAIL]" in body





@pytest.mark.smoke
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


@pytest.mark.smoke
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


@pytest.mark.smoke
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


@pytest.mark.smoke
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



@pytest.mark.smoke
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



@pytest.mark.smoke
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


@pytest.mark.smoke
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



@pytest.mark.smoke
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



@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
        assert await esc_service.classify_question_intent(
            message, "sk-test"
        ) == esc_service.QuestionIntentResult()


@pytest.mark.asyncio
@pytest.mark.smoke
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
@pytest.mark.smoke
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


@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
@pytest.mark.escalation
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
@pytest.mark.smoke
@pytest.mark.escalation
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
@pytest.mark.smoke
@pytest.mark.escalation
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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
@pytest.mark.smoke
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


@pytest.mark.smoke
def test_late_contact_email_reopens_auto_closed_ticket(
    tenant: TestClient,
    db_session: Session,
) -> None:
    """A ticket the sweeper aged out must reopen when support is finally notified.

    A chat awaiting the user's email blocks conversation rotation, so it can sit
    idle long enough to be auto-closed. If the user then answers, the deferred
    notify fires — support hears about the request for the first time, so it
    cannot read as closed in the dashboard.
    """
    from datetime import UTC, datetime

    token = register_and_verify_user(
        tenant, db_session, email="reopen-owner@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Reopen Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="my domain won't delegate",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.auto_closed,
        resolved_at=datetime.now(UTC).replace(tzinfo=None),
        chat_id=chat.id,
        session_id=chat.session_id,
        user_email=None,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    apply_collected_contact_email(
        ticket.id, chat.id, "late@example.com", db_session
    )
    db_session.commit()

    db_session.refresh(ticket)
    assert ticket.status == EscalationStatus.open
    assert ticket.resolved_at is None
    assert ticket.user_email == "late@example.com"


import backend.escalation.service as escalation_service  # noqa: E402
from datetime import timedelta  # noqa: E402


def _open_ticket_with_anchor(
    db: Session,
    tenant_id: uuid.UUID,
    chat: Chat,
    *,
    anchor: str | None = "<msg-1@brevo>",
    last_notified_at=None,
) -> EscalationTicket:
    ticket = EscalationTicket(
        tenant_id=tenant_id,
        ticket_number="ESC-0001",
        primary_question="first question",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
        user_email="user@example.com",
        notification_message_id=anchor,
        last_notified_at=last_notified_at,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@pytest.mark.smoke
@pytest.mark.escalation
def test_repeat_escalation_notify_bypasses_the_followup_debounce(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    """A second escalation inside the debounce window must still reach support.

    Ticket reuse routes the handoff through the follow-up notify, which carries
    a 60s debounce meant for chatty follow-up turns. The create path it replaces
    has no debounce, and the marker advance at the end of that path guarantees a
    repeat lands inside the window — so without the bypass the bot would tell
    the user "passed to support" while support received nothing.
    """
    from datetime import UTC, datetime

    token = register_and_verify_user(
        tenant, db_session, email="debounce-owner@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Debounce Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    # Notified one second ago — deep inside the 60s debounce window.
    ticket = _open_ticket_with_anchor(
        db_session,
        tenant_id,
        chat,
        last_notified_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
    )

    sent: list[tuple] = []
    monkeypatch.setattr(
        escalation_service,
        "_send_email_off_loop",
        lambda *a, **kw: sent.append((a, kw)) or "<msg-2@brevo>",
    )

    assert (
        escalation_service.notify_support_of_repeat_escalation(
            ticket, db_session, latest_user_text="my invoice for March is wrong"
        )
        is True
    )
    assert len(sent) == 1
    assert "my invoice for March is wrong" in sent[0][0][2]


@pytest.mark.smoke
def test_repeat_escalation_reattempts_initial_notify_when_anchor_missing(
    tenant: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    """A ticket whose initial notify failed must not become un-notifiable.

    ``create_escalation_ticket`` swallows a failing initial send, leaving
    ``notification_message_id`` NULL — the state in which the threaded-update
    path no-ops forever. Before ticket reuse the next repeat minted a new ticket
    and re-attempted the send, so a transient Brevo outage self-healed; the
    reuse guard must preserve that by re-attempting the initial notify.
    """
    token = register_and_verify_user(
        tenant, db_session, email="anchor-owner@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Anchor Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    ticket = _open_ticket_with_anchor(db_session, tenant_id, chat, anchor=None)

    sent: list[tuple] = []
    monkeypatch.setattr(
        escalation_service,
        "_send_email_off_loop",
        lambda *a, **kw: sent.append((a, kw)) or "<recovered@brevo>",
    )

    assert (
        escalation_service.notify_support_of_repeat_escalation(
            ticket, db_session, latest_user_text="hello?? is anyone there"
        )
        is True
    )
    assert len(sent) == 1
    # Re-attempt is the *initial* notify, so the anchor is restored and later
    # turns can thread under it.
    db_session.refresh(ticket)
    assert ticket.notification_message_id == "<recovered@brevo>"


@pytest.mark.smoke
def test_repeat_escalation_raises_priority_but_never_lowers_it(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(
        tenant, db_session, email="priority-owner@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Priority Tenant"},
    )
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    chat = Chat(tenant_id=tenant_id, session_id=uuid.uuid4())
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    ticket = _open_ticket_with_anchor(db_session, tenant_id, chat)
    ticket.priority = EscalationPriority.medium
    db_session.add(ticket)
    db_session.commit()

    high_ctx = {"plan_tier": "enterprise"}
    escalation_service.raise_ticket_priority_if_higher(
        ticket, EscalationTrigger.user_request, high_ctx, db_session
    )
    db_session.commit()
    db_session.refresh(ticket)
    raised = ticket.priority
    assert escalation_service._PRIORITY_ORDER[raised] > escalation_service._PRIORITY_ORDER[
        EscalationPriority.medium
    ]

    # A later low-priority turn must not demote a ticket support is already
    # treating as urgent.
    escalation_service.raise_ticket_priority_if_higher(
        ticket, EscalationTrigger.low_similarity, {}, db_session
    )
    db_session.commit()
    db_session.refresh(ticket)
    assert ticket.priority == raised
