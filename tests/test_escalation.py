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
    _notify_tenant_new_ticket,
    _notify_tenant_ticket_update,
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
)
from tests.conftest import register_and_verify_user


@pytest.mark.smoke
@pytest.mark.parametrize(
    "similarity, doc_count, expected_escalate, expected_trigger",
    [
        pytest.param(0.3, 3, True, EscalationTrigger.low_similarity, id="low_similarity"),
        pytest.param(None, 0, True, EscalationTrigger.no_documents, id="no_documents"),
        pytest.param(0.9, 2, False, None, id="good_match_no_escalation"),
    ],
)
def test_should_escalate(
    similarity: float | None,
    doc_count: int,
    expected_escalate: bool,
    expected_trigger: EscalationTrigger | None,
) -> None:
    esc, trig = should_escalate(similarity, doc_count)
    assert esc is expected_escalate
    assert trig == expected_trigger


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
@pytest.mark.parametrize(
    "payload, expected_explicit",
    [
        pytest.param(
            {
                "human_request": True,
                "message_has_request_content": True,
                "human_request_explicit": False,
            },
            False,
            id="inferred_handoff_stays_non_explicit",
        ),
        pytest.param(
            {"human_request": True, "message_has_request_content": True},
            True,
            id="missing_axis_defaults_to_explicit",
        ),
    ],
)
async def test_detect_human_request_explicitness_axis(
    payload: dict, expected_explicit: bool
) -> None:
    """The explicitness axis is parsed when present and defaults to explicit
    when the classifier omits it — the caller uses it to decide between
    answering an implied problem from the knowledge base and escalating it
    (see EscalationStateMachine's implied-request fall-through)."""
    with _mock_llm_human_request_payload(payload):
        result = await detect_human_request("не могу менять настройки", "sk-test")
    assert result.human_request is True
    assert result.message_has_request_content is True
    assert result.human_request_explicit is expected_explicit


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
@pytest.mark.parametrize(
    "flags, expected",
    [
        pytest.param(
            {"support_contact": True},
            {"support_contact": True, "pricing": False},
            id="contact_axis_true",
        ),
        pytest.param(
            {"support_contact": False},
            {
                "support_contact": False,
                "pricing": False,
                "service_status": False,
                "documentation": False,
            },
            id="all_axes_false",
        ),
        pytest.param(
            {
                "support_contact": False,
                "pricing": True,
                "service_status": True,
                "documentation": True,
            },
            {
                "support_contact": False,
                "pricing": True,
                "service_status": True,
                "documentation": True,
            },
            id="every_non_contact_axis_true",
        ),
    ],
)
async def test_classify_question_intent_axis_parsing(
    flags: dict, expected: dict
) -> None:
    """Each classifier axis is parsed independently from the JSON response."""
    from backend.escalation.service import (
        _question_intent_cache,
        classify_question_intent,
    )

    _question_intent_cache.clear()
    with _mock_llm_question_intent(**flags):
        result = await classify_question_intent("a question", "sk-test")
    for axis, value in expected.items():
        assert getattr(result, axis) is value


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


@pytest.mark.smoke
@pytest.mark.parametrize(
    "plan_tier, tenant_profile, expected",
    [
        pytest.param(
            "enterprise",
            {"plan_tier": "enterprise"},
            EscalationPriority.critical,
            id="enterprise_tier_is_critical",
        ),
        pytest.param(None, {}, EscalationPriority.high, id="default_tier_is_high"),
    ],
)
def test_compute_priority_selects_by_plan_tier(
    plan_tier: str | None,
    tenant_profile: dict,
    expected: EscalationPriority,
) -> None:
    p = compute_priority(EscalationTrigger.user_request, plan_tier, tenant_profile)
    assert p == expected


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
@pytest.mark.parametrize(
    "failure_count, expect_raise",
    [
        pytest.param(1, False, id="recovers_after_one_integrity_error"),
        pytest.param(None, True, id="raises_after_max_retries_exhausted"),
    ],
)
def test_create_escalation_ticket_retry_arithmetic(
    tenant: TestClient,
    db_session: Session,
    failure_count: int | None,
    expect_raise: bool,
) -> None:
    """create_escalation_ticket retries a commit IntegrityError up to its cap,
    then re-raises once retries are exhausted."""
    token = register_and_verify_user(
        tenant, db_session, email=f"esc-retry-{expect_raise}@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Retry Tenant"},
    )
    assert cl_resp.status_code == 201
    tenant_id = uuid.UUID(cl_resp.json()["id"])

    real_commit = db_session.commit
    call_count = [0]

    def commit_side_effect():
        call_count[0] += 1
        if failure_count is None or call_count[0] <= failure_count:
            raise SAIntegrityError("stmt", {}, Exception("unique constraint violation"))
        return real_commit()

    with patch.object(db_session, "commit", side_effect=commit_side_effect):
        if expect_raise:
            with pytest.raises(SAIntegrityError):
                create_escalation_ticket(
                    tenant_id,
                    "test retry question",
                    EscalationTrigger.low_similarity,
                    db_session,
                )
        else:
            ticket = create_escalation_ticket(
                tenant_id,
                "test retry question",
                EscalationTrigger.low_similarity,
                db_session,
            )
            assert ticket.ticket_number.startswith("ESC-")
            assert call_count[0] == 2


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
@pytest.mark.parametrize(
    "case_id",
    [
        "debounce_window",
        "no_initial_message_id",
        "no_new_turns_since_last_notify",
        "marker_already_advanced_past_turn",
    ],
)
def test_notify_ticket_update_skips(
    tenant: TestClient,
    db_session: Session,
    case_id: str,
) -> None:
    """``_notify_tenant_ticket_update`` must no-op — never call ``send_email``
    — for each of the four independent skip conditions: inside the debounce
    window, no anchor to thread under, no turn since the last notify, or a
    turn already covered by ``advance_notification_marker_to_current`` (the
    email-capture flow bundles the current turn into the initial notify)."""
    from datetime import UTC, datetime, timedelta

    _, chat, ticket = _setup_followup_fixture(
        tenant,
        db_session,
        owner_email=f"skip-{case_id}@example.com",
        notification_message_id=None if case_id == "no_initial_message_id" else "<initial-abc@brevo>",
    )

    if case_id == "debounce_window":
        _persist_user_message(db_session, chat, "first follow-up message")
        ticket.last_notified_at = datetime.now(UTC) - timedelta(
            seconds=_FOLLOWUP_NOTIFY_DEBOUNCE_SECONDS - 5
        )
        db_session.add(ticket)
        db_session.commit()
    elif case_id == "no_initial_message_id":
        _persist_user_message(db_session, chat, "new context but no anchor")
    elif case_id == "no_new_turns_since_last_notify":
        only = _persist_user_message(db_session, chat, "the only turn already notified")
        ticket.last_notified_message_id = only.id
        ticket.last_notified_at = only.created_at
        db_session.add(ticket)
        db_session.commit()
    elif case_id == "marker_already_advanced_past_turn":
        _persist_user_message(db_session, chat, "current turn bundled in initial body")
        advance_notification_marker_to_current(ticket, db_session)

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
async def test_render_pre_confirm_text_selects_distinct_canonical_per_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each pre_confirm variant must localize its own canonical English text,
    not the general escalation LLM's free-form composition — the bug source
    of the prod issue where handoff-phase wording leaked into the pre_confirm
    reply. Otherwise the ``no``/``unclear`` branches would echo the ``initial``
    question and the UX would look like a stuck loop. The ``no_answer``
    variant additionally leads with a "couldn't find an answer" preamble,
    distinct from the bare ``initial`` question.
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
        return LocalizationResult(text=f"LOCALIZED:{canonical_text}", tokens_used=7)

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    initial_out = await render_pre_confirm_text(
        variant="initial", response_language="ru", api_key="k"
    )
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
    # Output wiring: the localized text and language flow through to the caller.
    assert initial_out.message_to_user == f"LOCALIZED:{PRE_CONFIRM_QUESTION_EN}"
    assert initial_out.followup_decision is None
    assert initial_out.tokens_used == 7


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


def _fake_decision_client(content: str, tokens: int) -> object:
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
@pytest.mark.parametrize(
    "content, tokens, expected_decision, latest_user_text",
    [
        pytest.param('{"decision": "yes"}', 4, "yes", "yes please", id="yes_decision_parsed"),
        pytest.param(
            '{"decision": null}',
            3,
            None,
            "my site is down with a 502",
            id="null_decision_degrades_to_none",
        ),
    ],
)
async def test_classify_pre_confirm_reply_decision_parsing(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    tokens: int,
    expected_decision: str | None,
    latest_user_text: str,
) -> None:
    """Narrow classifier returns only the decision label (never a message); a
    substantive non-yes/no reply surfaces as ``None`` so the caller degrades
    to the unclear/re-ask path rather than treat random content as accept.
    """
    from backend.escalation.openai_escalation import classify_pre_confirm_reply

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _fake_decision_client(content, tokens),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    decision, actual_tokens = await classify_pre_confirm_reply(
        latest_user_text=latest_user_text,
        api_key="sk-test",
    )
    assert decision == expected_decision
    assert actual_tokens == tokens


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


@pytest.mark.asyncio
@pytest.mark.smoke
@pytest.mark.escalation
@pytest.mark.parametrize(
    "content, tokens, expected_decision",
    [
        pytest.param(
            '{"decision": "new_question"}',
            6,
            "new_question",
            id="new_question_clears_gate_for_same_turn_rag",
        ),
        pytest.param(
            '{"decision": "maybe"}',
            5,
            "unclear",
            id="unrecognized_label_degrades_to_unclear",
        ),
    ],
)
async def test_classify_followup_reply_decision_parsing(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    tokens: int,
    expected_decision: str,
) -> None:
    """``new_question`` lets the handler clear the follow-up gate and fall
    through to RAG on the same turn; any other label — unrecognized by the
    known set — must NOT fall through to RAG and instead degrades to
    ``unclear`` so the existing follow-up flow re-asks."""
    from backend.escalation.openai_escalation import classify_followup_reply

    async def _fake_retry(_name, fn, **_k):
        return await fn()

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_a, **_k: _fake_decision_client(content, tokens),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_call_openai_with_retry",
        _fake_retry,
    )

    decision, _ = await classify_followup_reply(
        latest_user_text="do you support wildcard domain names?",
        api_key="sk-test",
    )
    assert decision == expected_decision


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
@pytest.mark.parametrize(
    "completion_content, expected_tokens",
    [
        pytest.param(None, 0, id="completion_api_failure"),
        pytest.param(
            '{"message_to_user": "", "followup_decision": null}',
            7,
            id="empty_message_content",
        ),
    ],
)
async def test_escalation_turn_deadline_bounded_fallback(
    monkeypatch: pytest.MonkeyPatch,
    completion_content: str | None,
    expected_tokens: int,
) -> None:
    """When the completion fails, or returns an empty ``message_to_user``, AND
    the fallback localization hangs, the turn must still resolve within the
    escalation deadline with the canonical English fallback — not stall for
    the localization client's 60s timeout.
    """
    from backend.escalation.openai_escalation import (
        FALLBACK_EN_GENERIC,
        complete_escalation_openai_turn,
    )
    from backend.models import EscalationPhase

    async def _hanging_localize(**_k: object) -> object:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    if completion_content is None:

        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("openai down")

        monkeypatch.setattr(
            "backend.escalation.openai_escalation.get_async_openai_client", _boom
        )
    else:

        class _FakeMessage:
            content = completion_content

        class _FakeChoice:
            message = _FakeMessage()

        class _FakeUsage:
            total_tokens = expected_tokens

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
    assert out.tokens_used == expected_tokens
    assert out.followup_decision is None


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
