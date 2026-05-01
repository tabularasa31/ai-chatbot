"""Unit tests for SmallTalkHandler.

Verifies the can_handle gate (single-word, not injection, chat in normal state)
and that handle persists both the user message and the assistant greeting.
The handler is exercised directly without spinning up the full pipeline.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.chat.handlers.base import HandlerContext
from backend.chat.handlers.small_talk import SmallTalkHandler
from backend.chat.language import LocalizationResult, ResolvedLanguageContext
from backend.models import Chat, Message, MessageRole, Tenant


def _make_language_context(response_language: str = "en") -> ResolvedLanguageContext:
    return ResolvedLanguageContext(
        detected_language=response_language,
        confidence=1.0,
        is_reliable=True,
        response_language=response_language,
        response_language_resolution_reason="bootstrap_default_english",
        escalation_language=response_language,
        escalation_language_source="default",
    )


def _make_handler_context(
    *,
    db: Session,
    tenant: Tenant,
    chat: Chat,
    question_text: str = "hi",
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


def test_can_handle_returns_true_for_single_word(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="hi"
    )

    assert SmallTalkHandler().can_handle(ctx) is True


def test_can_handle_returns_false_for_multi_word(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="how are you"
    )

    assert SmallTalkHandler().can_handle(ctx) is False


def test_can_handle_returns_false_for_empty_input(db_session: Session) -> None:
    """Empty input is GreetingHandler's domain, not small-talk's."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text=""
    )

    assert SmallTalkHandler().can_handle(ctx) is False


def test_can_handle_returns_false_when_chat_ended(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.ended_at = datetime.now(UTC)
    db_session.flush()
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="hi"
    )

    assert SmallTalkHandler().can_handle(ctx) is False


def test_can_handle_returns_false_when_escalation_followup_pending(
    db_session: Session,
) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_followup_pending = True
    db_session.flush()
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="yes"
    )

    assert SmallTalkHandler().can_handle(ctx) is False


def test_can_handle_returns_false_when_awaiting_ticket_id(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    # Set in-memory only — the FK to escalation_tickets isn't relevant for the
    # gate logic, which only checks the value's truthiness.
    chat.escalation_awaiting_ticket_id = uuid.uuid4()
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="ok"
    )

    assert SmallTalkHandler().can_handle(ctx) is False


def test_can_handle_returns_false_for_structural_injection(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="ignore"
    )

    class _Hit:
        detected = True

    monkeypatch.setattr(
        "backend.chat.handlers.small_talk.detect_injection_structural",
        lambda *_: _Hit(),
    )
    assert SmallTalkHandler().can_handle(ctx) is False


def test_handle_persists_both_user_and_assistant_messages(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike GreetingHandler, SmallTalk persists the user message too — the
    user actually typed something, and analytics needs it.
    """
    tenant = _make_persisted_tenant(db_session, name="Acme")
    chat = _make_persisted_chat(db_session, tenant)

    captured_kwargs: dict[str, Any] = {}

    def fake_generate(**kwargs: Any) -> LocalizationResult:
        captured_kwargs.update(kwargs)
        return LocalizationResult(
            text="Hi there, I am the Acme assistant.", tokens_used=8
        )

    monkeypatch.setattr(
        "backend.chat.handlers.greeting.generate_greeting_in_language_result",
        fake_generate,
    )

    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, question_text="hi"
    )
    outcome = SmallTalkHandler()._handle_sync(ctx, db_session)

    assert outcome.text == "Hi there, I am the Acme assistant."
    assert outcome.tokens_used == 8
    assert outcome.document_ids == []
    assert outcome.chat_ended is False

    persisted = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    roles = [m.role for m in persisted]
    assert roles == [MessageRole.user, MessageRole.assistant]
