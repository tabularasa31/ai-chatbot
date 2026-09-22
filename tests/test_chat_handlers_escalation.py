"""Unit tests for EscalationStateMachine's pure dispatch predicate.

Everything else that used to live here — the branch-by-branch FSM behaviour —
has moved to through-the-app scenarios in tests/test_chat_escalation.py (see
the test-audit notes for the migration mapping). This file now holds only
``can_handle``, plus one regression that genuinely cannot be reproduced
through the app: a vanished ``escalation_awaiting_ticket_id`` requires a
persisted dangling foreign key, which the test database's FK enforcement
(``PRAGMA foreign_keys=ON``) refuses to write.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from backend.chat.handlers.base import HandlerContext
from backend.chat.handlers.escalation import EscalationStateMachine
from backend.chat.language import ResolvedLanguageContext
from backend.models import Chat, Tenant


def _make_language_context() -> ResolvedLanguageContext:
    return ResolvedLanguageContext(
        detected_language="en",
        confidence=1.0,
        is_reliable=True,
        response_language="en",
        response_language_resolution_reason="bootstrap_default_english",
        escalation_language="en",
        escalation_language_source="default",
    )


def _make_persisted_tenant(db: Session, *, name: str = "Acme") -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.flush()
    return tenant


def _make_persisted_chat(db: Session, tenant: Tenant) -> Chat:
    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
    db.add(chat)
    db.flush()
    return chat


def _make_handler_context(
    *,
    db: Session,
    tenant: Tenant,
    chat: Chat,
    question_text: str = "anything",
    explicit_human_request: bool = False,
    human_request_explicit: bool = True,
    message_has_request_content: bool = False,
) -> HandlerContext:
    return HandlerContext(
        tenant_id=tenant.id,
        chat=chat,
        tenant_row=tenant,
        tenant_profile=None,
        question=question_text,
        redacted_question=question_text,
        question_text=question_text,
        language_context=_make_language_context(),
        api_key="sk-test",
        is_new_session=False,
        trace=None,
        db=db,
        session_id=chat.session_id,
        explicit_human_request=explicit_human_request,
        human_request_explicit=human_request_explicit,
        message_has_request_content=message_has_request_content,
    )


def test_handle_falls_through_when_awaiting_ticket_vanished_and_no_human_request(
    db_session: Session,
) -> None:
    """Regression for PR #450 P1 review.

    When ``chat.escalation_awaiting_ticket_id`` points to a deleted ticket and
    the user did not ask for a human, we must clear the stale pointer and
    return None so the router falls through to RagHandler — NOT mint a fresh
    escalation ticket as the unguarded T-3 path used to do.

    Reproducing this state through the app is not possible: ``chats.
    escalation_awaiting_ticket_id`` has an ``ON DELETE SET NULL`` foreign key,
    and the test database enforces it (``PRAGMA foreign_keys=ON``), so a
    dangling pointer can never be persisted via ordinary writes — only set
    on the in-memory object as done here, exercising the handler directly.
    """
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    # In-memory only — the FK target doesn't exist by design; the handler
    # should detect the vanished ticket and clear the pointer.
    chat.escalation_awaiting_ticket_id = uuid.uuid4()
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="what is your pricing",
        explicit_human_request=False,
    )

    # ``create_escalation_ticket`` would be invoked from _handle_explicit_request
    # if we accidentally fell into the T-3 branch. Patch it as a sentinel so the
    # test fails loudly if the regression resurfaces.
    def _no_ticket_create(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "EscalationStateMachine attempted to create a ticket on a vanished-"
            "pointer recovery without an explicit human request"
        )

    with patch("backend.chat.service.create_escalation_ticket", _no_ticket_create):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is None, "Handler must yield to RagHandler, not return an outcome"
    # Stale pointer cleared as a side effect.
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is None


@pytest.mark.parametrize(
    "chat_attrs, explicit_human_request, expected",
    [
        pytest.param({}, True, True, id="explicit_request_with_no_state_dispatches"),
        pytest.param({}, False, False, id="no_state_and_no_human_request_declines"),
        pytest.param(
            {"ended_at": lambda: datetime.now(UTC)},
            False,
            False,
            id="legacy_ended_at_is_ignored",
        ),
        pytest.param(
            {"escalation_awaiting_ticket_id": lambda: uuid.uuid4()},
            False,
            True,
            id="awaiting_ticket_id_dispatches",
        ),
        pytest.param(
            {"escalation_followup_pending": True},
            False,
            True,
            id="followup_pending_dispatches",
        ),
        pytest.param(
            {"escalation_awaiting_request": True},
            False,
            True,
            id="awaiting_request_dispatches",
        ),
    ],
)
def test_can_handle_dispatch(
    db_session: Session,
    chat_attrs: dict[str, Any],
    explicit_human_request: bool,
    expected: bool,
) -> None:
    """``can_handle`` dispatches on any deterministic escalation state flag
    (awaiting-ticket, followup-pending, awaiting-request) or an explicit
    human request when no state is set; a legacy ``ended_at`` is not itself
    a dispatch signal."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    for attr, value in chat_attrs.items():
        setattr(chat, attr, value() if callable(value) else value)
    if "escalation_awaiting_ticket_id" not in chat_attrs:
        # A random ticket id has no matching row — flushing it would trip the
        # FK constraint. Other attrs are plain columns, safe to flush.
        db_session.flush()
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        explicit_human_request=explicit_human_request,
    )
    assert EscalationStateMachine().can_handle(ctx) is expected
