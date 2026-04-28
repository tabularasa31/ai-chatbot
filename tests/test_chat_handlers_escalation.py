"""Unit tests for EscalationStateMachine.

Focused on edge cases the integration suite doesn't naturally hit:
the vanished-awaiting-ticket recovery path (regression test for the bug
spotted in PR #450 review where a stale-pointer recovery would mint a fresh
escalation ticket on any ordinary reply).
"""

from __future__ import annotations

import uuid
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
        optional_entity_types=None,
        is_new_session=False,
        trace=None,
        db=db,
        session_id=chat.session_id,
        explicit_human_request=explicit_human_request,
    )


def test_handle_falls_through_when_awaiting_ticket_vanished_and_no_human_request(
    db_session: Session,
) -> None:
    """Regression for PR #450 P1 review.

    When ``chat.escalation_awaiting_ticket_id`` points to a deleted ticket and
    the user did not ask for a human, we must clear the stale pointer and
    return None so the router falls through to RagHandler — NOT mint a fresh
    escalation ticket as the unguarded T-3 path used to do.
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
        outcome = EscalationStateMachine().handle(ctx)

    assert outcome is None, "Handler must yield to RagHandler, not return an outcome"
    # Stale pointer cleared as a side effect.
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is None


def test_can_handle_returns_true_for_explicit_request_when_no_state_set(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="i need a human"
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.detect_human_request",
        lambda *_args, **_kw: True,
    )
    assert EscalationStateMachine().can_handle(ctx) is True


def test_can_handle_returns_false_when_no_state_and_no_human_request(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="what is your price"
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.detect_human_request",
        lambda *_args, **_kw: False,
    )
    assert EscalationStateMachine().can_handle(ctx) is False


def test_can_handle_returns_true_when_chat_ended(db_session: Session) -> None:
    from datetime import UTC, datetime

    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.ended_at = datetime.now(UTC)
    db_session.flush()
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)
    assert EscalationStateMachine().can_handle(ctx) is True


def test_can_handle_returns_true_when_awaiting_ticket_id(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    # In-memory only — handler treats stale pointer as escalation state.
    chat.escalation_awaiting_ticket_id = uuid.uuid4()
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)
    assert EscalationStateMachine().can_handle(ctx) is True


def test_can_handle_returns_true_when_followup_pending(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_followup_pending = True
    db_session.flush()
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)
    assert EscalationStateMachine().can_handle(ctx) is True
