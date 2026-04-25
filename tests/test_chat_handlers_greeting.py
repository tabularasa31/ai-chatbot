"""Unit tests for GreetingHandler.

Verifies the handler dispatches only on empty + new-session turns and that
the assistant greeting is persisted with no user-message row (analytics rule).
The handler is exercised directly without spinning up the full pipeline, to
keep tests fast and free of mocks for unrelated subsystems.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.chat.handlers.base import HandlerContext
from backend.chat.handlers.greeting import GreetingHandler, _resolve_product_name
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
    tenant: Tenant | None,
    chat: Chat,
    question_text: str = "",
    is_new_session: bool = True,
    response_language: str = "en",
) -> HandlerContext:
    return HandlerContext(
        tenant_id=tenant.id if tenant else uuid.uuid4(),
        chat=chat,
        tenant_row=tenant,
        question=question_text,
        redacted_question=question_text,
        question_text=question_text,
        language_context=_make_language_context(response_language),
        api_key="sk-test",
        optional_entity_types=None,
        is_new_session=is_new_session,
        trace=None,
        db=db,
    )


def _make_persisted_tenant(db: Session, *, name: str = "Acme") -> Tenant:
    tenant = Tenant(name=name, api_key="k" * 32)
    db.add(tenant)
    db.flush()
    return tenant


def _make_persisted_chat(db: Session, tenant: Tenant) -> Chat:
    chat = Chat(tenant_id=tenant.id, session_id=uuid.uuid4())
    db.add(chat)
    db.flush()
    return chat


def test_can_handle_returns_true_for_empty_new_session(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)

    assert GreetingHandler().can_handle(ctx) is True


def test_can_handle_returns_false_when_session_not_new(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat, is_new_session=False)

    assert GreetingHandler().can_handle(ctx) is False


def test_can_handle_returns_false_when_question_present(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat, question_text="hi")

    assert GreetingHandler().can_handle(ctx) is False


def test_handle_produces_outcome_and_persists_only_assistant_message(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _make_persisted_tenant(db_session, name="Acme")
    chat = _make_persisted_chat(db_session, tenant)

    captured_kwargs: dict[str, Any] = {}

    def fake_generate(**kwargs: Any) -> LocalizationResult:
        captured_kwargs.update(kwargs)
        return LocalizationResult(text="Hello, I am the Acme assistant.", tokens_used=7)

    monkeypatch.setattr(
        "backend.chat.handlers.greeting.generate_greeting_in_language_result", fake_generate
    )

    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)
    outcome = GreetingHandler().handle(ctx)

    assert outcome.text == "Hello, I am the Acme assistant."
    assert outcome.tokens_used == 7
    assert outcome.document_ids == []
    assert outcome.chat_ended is False
    assert captured_kwargs["product_name"] == "Acme"
    assert captured_kwargs["target_language"] == "en"

    # Only the assistant greeting is persisted — no empty user-message row.
    persisted = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    roles = [m.role for m in persisted]
    assert roles == [MessageRole.assistant]


def test_resolve_product_name_falls_back_to_default_when_tenant_missing(db_session: Session) -> None:
    assert _resolve_product_name(tenant=None, db=db_session) == "this product"


def test_resolve_product_name_uses_tenant_name_when_no_profile(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session, name="My Co")
    assert _resolve_product_name(tenant=tenant, db=db_session) == "My Co"
