"""Unit tests for EscalationStateMachine.

Focused on edge cases the integration suite doesn't naturally hit:
the vanished-awaiting-ticket recovery path (regression test for the bug
spotted in PR #450 review where a stale-pointer recovery would mint a fresh
escalation ticket on any ordinary reply).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import Mock, patch

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
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is None, "Handler must yield to RagHandler, not return an outcome"
    # Stale pointer cleared as a side effect.
    db_session.refresh(chat)
    assert chat.escalation_awaiting_ticket_id is None


def test_can_handle_returns_true_for_explicit_request_when_no_state_set(
    db_session: Session,
) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="i need a human",
        explicit_human_request=True,
    )
    assert EscalationStateMachine().can_handle(ctx) is True


def test_can_handle_returns_false_when_no_state_and_no_human_request(
    db_session: Session,
) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="what is your price",
        explicit_human_request=False,
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


def test_explicit_request_escalates_immediately_without_pre_confirm(
    db_session: Session,
) -> None:
    """An explicit human request must create a ticket immediately, bypassing the
    pre_confirm gate.

    Regression for the silent-escalation-loss bug: when explicit requests were
    routed through pre_confirm, users who never replied "yes" left the chat
    stuck in ``escalation_pre_confirm_pending`` and no ticket/email was ever
    produced. The request itself is the confirmation, so the gate must be
    skipped here (it still applies to bot-initiated low_similarity escalations).
    """
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="connect me to a human please",
        explicit_human_request=True,
    )

    captured: dict[str, Any] = {}
    sentinel = object()

    def _fake_handoff(
        _self: Any,
        _ctx: HandlerContext,
        *,
        pre_confirm_ctx: dict,
        escalation_reason: str,
        trace_source: str,
        **_kwargs: Any,
    ) -> Any:
        captured["escalation_reason"] = escalation_reason
        captured["trigger"] = pre_confirm_ctx["trigger"]
        captured["primary_question"] = pre_confirm_ctx["primary_question"]
        return sentinel

    with patch.object(EscalationStateMachine, "_create_ticket_and_handoff", _fake_handoff):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is sentinel, "Explicit request must route to immediate handoff"
    assert captured["escalation_reason"] == "explicit_human_request"
    assert captured["trigger"] == "user_request"
    assert captured["primary_question"] == "connect me to a human please"
    # The pre_confirm gate must NOT be engaged for an explicit human request.
    assert not chat.escalation_pre_confirm_pending


def _make_pre_confirm_chat(db: Session, tenant: Tenant, *, clarify: bool = False) -> Chat:
    """A chat parked on the pre_confirm gate (bot asked 'forward to support?')."""
    chat = Chat(
        tenant_id=tenant.id,
        session_id=uuid.uuid4(),
        escalation_pre_confirm_pending=True,
        escalation_pre_confirm_context={
            "trigger": "low_similarity",
            "primary_question": "my widget won't render",
            "best_similarity_score": 0.31,
            "retrieved_chunks": None,
        },
        user_context={"escalation_followup_clarify": True} if clarify else {},
    )
    db.add(chat)
    db.flush()
    return chat


def _fail_if_ticket_created(_self: Any, _ctx: HandlerContext, **_kwargs: Any) -> Any:
    raise AssertionError(
        "pre_confirm escalated to a ticket without an explicit user 'yes'"
    )


def _drive(awaitable: Any) -> Any:
    """Run the handler's ``await_only(asyncio.to_thread(...))`` calls in a sync
    test, which has no SQLAlchemy greenlet context."""
    return asyncio.run(awaitable)


def test_pre_confirm_non_yes_no_reply_falls_through_without_ticket(
    db_session: Session,
) -> None:
    """Regression for 86exn3x7c.

    When the user answers the pre_confirm question with a substantive non-yes/no
    reply (a new symptom or topic change → classifier returns ``None``), the bot
    must NOT create a ticket. It clears the pre_confirm gate and yields to
    RagHandler (returns None) so the new message gets a fresh KB answer.
    """
    tenant = _make_persisted_tenant(db_session)
    chat = _make_pre_confirm_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="I checked the data-bot-id, it matches the dashboard",
    )

    with (
        patch("backend.chat.handlers.escalation.await_only", _drive),
        patch("backend.chat.service.classify_pre_confirm_reply", lambda **_kw: (None, 0)),
        patch.object(EscalationStateMachine, "_create_ticket_and_handoff", _fail_if_ticket_created),
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is None, "Must yield to RagHandler, not answer from the FSM"
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None
    assert (chat.user_context or {}).get("escalation_followup_clarify") is None


def test_pre_confirm_repeated_unclear_never_auto_escalates(
    db_session: Session,
) -> None:
    """Regression for 86exn3x7c.

    The old flow promoted a *second* ``unclear`` reply to ``yes`` and silently
    minted a ticket. A ticket must only be created on an explicit ``yes``, so a
    repeated ``unclear`` (clarify flag already set) must re-ask, never escalate.
    """
    tenant = _make_persisted_tenant(db_session)
    chat = _make_pre_confirm_chat(db_session, tenant, clarify=True)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="wait, what do you mean by forwarding?",
    )

    sentinel = object()
    with (
        patch("backend.chat.handlers.escalation.await_only", _drive),
        patch("backend.chat.service.classify_pre_confirm_reply", lambda **_kw: ("unclear", 0)),
        patch("backend.chat.service.render_pre_confirm_text", lambda **_kw: Mock(tokens_used=0)),
        patch("backend.chat.service._escalation_turn_response", lambda **_kw: sentinel),
        patch.object(EscalationStateMachine, "_create_ticket_and_handoff", _fail_if_ticket_created),
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is sentinel, "Repeated unclear must re-ask, not escalate"
    db_session.refresh(chat)
    # Still parked on the gate awaiting an explicit answer.
    assert chat.escalation_pre_confirm_pending is True


def test_pre_confirm_null_reply_with_explicit_human_request_escalates(
    db_session: Session,
) -> None:
    """Regression for PR #694 review (gemini medium).

    If the substantive reply that clears the pre_confirm gate is *also* an
    explicit human request, the FSM must still escalate this turn instead of
    silently falling through to RAG. Returning None from _handle_pre_confirm
    must drop into the explicit-request check, not bypass it.
    """
    tenant = _make_persisted_tenant(db_session)
    chat = _make_pre_confirm_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="still broken, just connect me to a human already",
        explicit_human_request=True,
    )

    captured: dict[str, Any] = {}
    sentinel = object()

    def _fake_handoff(_self: Any, _ctx: HandlerContext, *, escalation_reason: str, **_kwargs: Any) -> Any:
        captured["reason"] = escalation_reason
        return sentinel

    with (
        patch("backend.chat.handlers.escalation.await_only", _drive),
        patch("backend.chat.service.classify_pre_confirm_reply", lambda **_kw: (None, 0)),
        patch.object(EscalationStateMachine, "_create_ticket_and_handoff", _fake_handoff),
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is sentinel, "Explicit human request in the reply must escalate"
    assert captured["reason"] == "explicit_human_request"
    db_session.refresh(chat)
    # The pre_confirm gate was cleared before the explicit-request escalation.
    assert chat.escalation_pre_confirm_pending is False


def test_stale_followup_falls_through_after_session_reported_ended(
    db_session: Session,
) -> None:
    """Regression: a follow-up prompt left pending across an inactivity gap must
    not eat a genuine new question.

    The bot asked "Is there anything else?" and set ``escalation_followup_pending``.
    Hours later the inactivity sweeper set ``session_ended_event_at``; the user
    then returns with a real new question. The follow-up is stale — the FSM must
    clear the gate and yield to RagHandler (return None) instead of classifying
    the new question as a yes/no answer and re-emitting the forwarded-handoff copy.
    """
    from datetime import UTC, datetime

    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_followup_pending = True
    chat.session_ended_event_at = datetime.now(UTC)
    chat.user_context = {"escalation_followup_clarify": True}
    db_session.flush()

    def _fail_if_classified(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "stale follow-up must fall through to RAG, not run the yes/no classifier"
        )

    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="why was the A www record not added to the list?",
    )

    with patch(
        "backend.chat.service.complete_escalation_openai_turn", _fail_if_classified
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is None, "Stale follow-up must yield to RagHandler"
    db_session.refresh(chat)
    assert chat.escalation_followup_pending is False
    assert (chat.user_context or {}).get("escalation_followup_clarify") is None


def test_pre_confirm_explicit_yes_creates_ticket(db_session: Session) -> None:
    """Sanity: an explicit ``yes`` still routes to ticket creation/handoff."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_pre_confirm_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="yes please"
    )

    captured: dict[str, Any] = {}
    sentinel = object()

    def _fake_handoff(_self: Any, _ctx: HandlerContext, *, pre_confirm_ctx: dict, **_kwargs: Any) -> Any:
        captured["trigger"] = pre_confirm_ctx["trigger"]
        return sentinel

    with (
        patch("backend.chat.handlers.escalation.await_only", _drive),
        patch("backend.chat.service.classify_pre_confirm_reply", lambda **_kw: ("yes", 5)),
        patch.object(EscalationStateMachine, "_create_ticket_and_handoff", _fake_handoff),
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is sentinel
    assert captured["trigger"] == "low_similarity"
