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


def test_can_handle_returns_true_when_pre_confirm_pending(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_pre_confirm_pending = True
    db_session.flush()
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)
    assert EscalationStateMachine().can_handle(ctx) is True


# ---------------------------------------------------------------------------
# Pre-confirm flow (FI-ESC).
#
# These tests cover the deferred-ticket behaviour: instead of minting a ticket
# the instant ``explicit_human_request`` fires (which produced the duplicate
# "Я передал ваш запрос в службу поддержки…" UX), the handler first asks the
# user to confirm and only escalates on the next turn — using whatever the
# user types as the actual support question.
# ---------------------------------------------------------------------------


def _fake_escalation_llm(
    *,
    message: str,
    decision: str | None = None,
    tokens: int = 3,
) -> Any:
    """Build a minimal stand-in for ``EscalationLlmResult``."""

    return type(
        "EscalationLlmResult",
        (),
        {
            "message_to_user": message,
            "followup_decision": decision,
            "tokens_used": tokens,
        },
    )()


def test_explicit_request_sets_pre_confirm_without_creating_ticket(
    db_session: Session,
) -> None:
    """T-3 first turn: must defer ticket creation and set pre_confirm state."""
    from backend.models import EscalationPhase, EscalationTicket

    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="как связаться с оператором?",
        explicit_human_request=True,
    )

    captured_phases: list[EscalationPhase] = []

    def _llm(**kwargs: Any) -> Any:
        captured_phases.append(kwargs["phase"])
        return _fake_escalation_llm(
            message="Опишите проблему — отвечу здесь или передам в поддержку.",
        )

    def _no_ticket_create(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "Ticket must not be created on the first explicit-human-request turn — "
            "pre_confirm flow defers ticket creation until the user provides context"
        )

    with patch(
        "backend.chat.service.complete_escalation_openai_turn", side_effect=_llm
    ), patch(
        "backend.chat.service.create_escalation_ticket", _no_ticket_create
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is not None
    assert outcome.ticket_number is None
    assert outcome.chat_ended is False
    assert captured_phases == [EscalationPhase.pre_confirm]

    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is True
    assert chat.escalation_pre_confirm_context is not None
    assert chat.escalation_pre_confirm_context["trigger"] == "user_request"
    assert "оператором" in chat.escalation_pre_confirm_context["primary_question"]
    assert chat.escalation_awaiting_ticket_id is None
    assert chat.escalation_followup_pending is False
    # No ticket was minted.
    assert db_session.query(EscalationTicket).count() == 0


def test_pre_confirm_no_clears_state_and_falls_through(db_session: Session) -> None:
    """User declines the handoff → flag cleared, return None for RAG fallthrough."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_pre_confirm_pending = True
    chat.escalation_pre_confirm_context = {
        "trigger": "user_request",
        "primary_question": "как связаться с оператором?",
        "best_similarity_score": None,
        "retrieved_chunks": None,
    }
    db_session.flush()

    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="нет, спасибо",
        explicit_human_request=False,
    )

    def _llm(**_kwargs: Any) -> Any:
        return _fake_escalation_llm(message="понял, не передаю", decision="no")

    def _no_ticket(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Ticket must not be created when user declines handoff")

    with patch(
        "backend.chat.service.complete_escalation_openai_turn", side_effect=_llm
    ), patch(
        "backend.chat.service.create_escalation_ticket", _no_ticket
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is None  # Handler yields to RAG.
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None


def test_pre_confirm_substantive_reply_escalates_with_current_message(
    db_session: Session,
) -> None:
    """User replies with the actual problem on the second turn → ticket is
    created using the new message (not the original meta-question) as the
    ticket's ``primary_question``, and chat transitions to the email/follow-up
    stage.
    """
    from backend.models import EscalationPhase, EscalationTicket

    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_pre_confirm_pending = True
    chat.escalation_pre_confirm_context = {
        "trigger": "user_request",
        "primary_question": "как связаться с оператором?",
        "best_similarity_score": None,
        "retrieved_chunks": None,
    }
    db_session.flush()

    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="не работает сайт, отдаёт 502",
        explicit_human_request=False,
    )

    captured_primary_questions: list[str] = []

    def _create_ticket(
        _tenant_id: Any,
        primary_question: str,
        _trigger: Any,
        _db: Any,
        **kwargs: Any,
    ) -> EscalationTicket:
        captured_primary_questions.append(primary_question)
        ticket = EscalationTicket(
            tenant_id=tenant.id,
            ticket_number="ESC-T1",
            primary_question=primary_question,
            trigger=kwargs.get("trigger") or _trigger,
            chat_id=kwargs.get("chat_id") or chat.id,
            session_id=kwargs.get("session_id"),
            user_email=None,
        )
        db_session.add(ticket)
        db_session.flush()
        return ticket

    call_phases: list[EscalationPhase] = []

    def _llm(**kwargs: Any) -> Any:
        call_phases.append(kwargs["phase"])
        if kwargs["phase"] == EscalationPhase.pre_confirm:
            # Per the system prompt, a substantive non-yes/no reply yields
            # ``followup_decision=None``; the handler treats this as implicit
            # confirmation and escalates using the new message.
            return _fake_escalation_llm(message="(unused)", decision=None)
        # Second call: phase=handoff_ask_email (no user_email on the ticket).
        return _fake_escalation_llm(
            message="Передал в поддержку, пришлите email для ответа.", tokens=4
        )

    with patch(
        "backend.chat.service.complete_escalation_openai_turn", side_effect=_llm
    ), patch(
        "backend.chat.service.create_escalation_ticket", _create_ticket
    ), patch(
        "backend.chat.service._emit_chat_escalated_event"
    ), patch(
        "backend.chat.service._emit_chat_session_ended_event"
    ), patch(
        "backend.chat.service._try_ingest_gap_signal"
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is not None
    assert outcome.ticket_number == "ESC-T1"
    # The ticket's primary_question came from THIS turn, not the meta-question
    # the user typed first.
    assert captured_primary_questions == ["не работает сайт, отдаёт 502"]
    # Two LLM calls: classifier + handoff render.
    assert call_phases[0] == EscalationPhase.pre_confirm
    assert call_phases[1] == EscalationPhase.handoff_ask_email

    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None
    # No user_email on the ticket → awaiting-email state engaged.
    assert chat.escalation_awaiting_ticket_id is not None
    assert chat.escalation_followup_pending is False


def test_pre_confirm_short_yes_keeps_original_question_for_ticket(
    db_session: Session,
) -> None:
    """Short confirmation ("да") keeps the originally-stored question as
    ``primary_question`` — there's nothing more substantive to use.
    """
    from backend.models import EscalationPhase, EscalationTicket

    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_pre_confirm_pending = True
    chat.escalation_pre_confirm_context = {
        "trigger": "user_request",
        "primary_question": "как связаться с оператором?",
        "best_similarity_score": None,
        "retrieved_chunks": None,
    }
    db_session.flush()

    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="да",
        explicit_human_request=False,
    )

    captured: list[str] = []

    def _create_ticket(
        _tenant_id: Any,
        primary_question: str,
        _trigger: Any,
        _db: Any,
        **kwargs: Any,
    ) -> EscalationTicket:
        captured.append(primary_question)
        ticket = EscalationTicket(
            tenant_id=tenant.id,
            ticket_number="ESC-T2",
            primary_question=primary_question,
            trigger=kwargs.get("trigger") or _trigger,
            chat_id=kwargs.get("chat_id") or chat.id,
            session_id=kwargs.get("session_id"),
            user_email="known@example.com",
        )
        db_session.add(ticket)
        db_session.flush()
        return ticket

    def _llm(**kwargs: Any) -> Any:
        if kwargs["phase"] == EscalationPhase.pre_confirm:
            return _fake_escalation_llm(message="(unused)", decision="yes")
        return _fake_escalation_llm(message="Запрос передан в поддержку.", tokens=2)

    with patch(
        "backend.chat.service.complete_escalation_openai_turn", side_effect=_llm
    ), patch(
        "backend.chat.service.create_escalation_ticket", _create_ticket
    ), patch(
        "backend.chat.service._emit_chat_escalated_event"
    ), patch(
        "backend.chat.service._emit_chat_session_ended_event"
    ), patch(
        "backend.chat.service._try_ingest_gap_signal"
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is not None
    assert captured == ["как связаться с оператором?"]
    db_session.refresh(chat)
    # user_email known → followup_pending engaged (not awaiting_email).
    assert chat.escalation_followup_pending is True
    assert chat.escalation_awaiting_ticket_id is None


def test_pre_confirm_unclear_first_round_asks_again(db_session: Session) -> None:
    """Ambiguous first reply re-asks the confirmation (sets clarify flag,
    keeps pre_confirm state, does NOT create a ticket).
    """
    from backend.models import EscalationPhase

    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_pre_confirm_pending = True
    chat.escalation_pre_confirm_context = {
        "trigger": "user_request",
        "primary_question": "как связаться с оператором?",
        "best_similarity_score": None,
        "retrieved_chunks": None,
    }
    db_session.flush()

    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="хм",
        explicit_human_request=False,
    )

    captured_phases: list[EscalationPhase] = []

    def _llm(**kwargs: Any) -> Any:
        captured_phases.append(kwargs["phase"])
        return _fake_escalation_llm(
            message="Уточните, пожалуйста: передать в поддержку?",
            decision="unclear",
        )

    def _no_ticket(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Ticket must not be minted on the first unclear reply")

    with patch(
        "backend.chat.service.complete_escalation_openai_turn", side_effect=_llm
    ), patch(
        "backend.chat.service.create_escalation_ticket", _no_ticket
    ):
        outcome = EscalationStateMachine()._handle_sync(ctx, db_session)

    assert outcome is not None
    assert outcome.ticket_number is None
    assert captured_phases == [EscalationPhase.pre_confirm]
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is True
    # Clarify flag now set so the next unclear answer will promote to "yes".
    from backend.escalation.service import _pre_confirm_clarify_already_asked

    assert _pre_confirm_clarify_already_asked(chat) is True
