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
from backend.chat.handlers.greeting import (
    GreetingHandler,
    _build_greeting_result,
    _resolve_product_name,
)
from backend.chat.language import LocalizationResult, ResolvedLanguageContext
from backend.models import Chat, Message, MessageRole, Tenant, TenantProfile


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
    tenant_profile: TenantProfile | None = None,
    message_has_request_content: bool = True,
    explicit_human_request: bool = False,
) -> HandlerContext:
    return HandlerContext(
        tenant_id=tenant.id if tenant else uuid.uuid4(),
        chat=chat,
        tenant_row=tenant,
        tenant_profile=tenant_profile,
        question=question_text,
        redacted_question=question_text,
        question_text=question_text,
        language_context=_make_language_context(response_language),
        api_key="sk-test",
        optional_entity_types=None,
        is_new_session=is_new_session,
        trace=None,
        db=db,
        message_has_request_content=message_has_request_content,
        explicit_human_request=explicit_human_request,
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


def test_can_handle_returns_true_for_empty_new_session(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)

    assert GreetingHandler().can_handle(ctx) is True


def test_can_handle_returns_false_when_session_not_new(db_session: Session) -> None:
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session, tenant=tenant, chat=chat, is_new_session=False
    )

    assert GreetingHandler().can_handle(ctx) is False


def test_can_handle_returns_false_for_short_question_with_request_content(
    db_session: Session,
) -> None:
    """A short *question* still carries request content → flows to RAG, not greeted.

    This is the bug the old word-count small-talk path had: it greeted one-word
    questions. GreetingHandler now keys off intent, not length.
    """
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="price?",
        is_new_session=False,
        message_has_request_content=True,
    )

    assert GreetingHandler().can_handle(ctx) is False


def test_can_handle_returns_true_for_bare_typed_greeting(db_session: Session) -> None:
    """A typed greeting with no request content is greeted, not sent to RAG."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="Здравствуйте!",
        is_new_session=False,
        message_has_request_content=False,
    )

    assert GreetingHandler().can_handle(ctx) is True


def test_can_handle_returns_false_when_explicit_human_request(db_session: Session) -> None:
    """A hand-me-off request is never small talk, even with no request content."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="оператор",
        is_new_session=False,
        message_has_request_content=False,
        explicit_human_request=True,
    )

    assert GreetingHandler().can_handle(ctx) is False


def test_can_handle_returns_false_during_escalation_pre_confirm(db_session: Session) -> None:
    """A no-request-content reply during pre-confirm must reach the escalation FSM."""
    tenant = _make_persisted_tenant(db_session)
    chat = _make_persisted_chat(db_session, tenant)
    chat.escalation_pre_confirm_pending = True
    db_session.flush()
    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="да",
        is_new_session=False,
        message_has_request_content=False,
    )

    assert GreetingHandler().can_handle(ctx) is False


def test_handle_typed_greeting_persists_user_and_assistant(
    db_session: Session,
) -> None:
    tenant = _make_persisted_tenant(db_session, name="Acme")
    chat = _make_persisted_chat(db_session, tenant)

    ctx = _make_handler_context(
        db=db_session,
        tenant=tenant,
        chat=chat,
        question_text="Здравствуйте!",
        is_new_session=False,
        message_has_request_content=False,
    )
    outcome = GreetingHandler()._handle_sync(
        ctx,
        db_session,
        LocalizationResult(text="Здравствуйте! Чем помочь?", tokens_used=5),
    )

    assert outcome.text == "Здравствуйте! Чем помочь?"
    # Both the user message and the assistant greeting are persisted. Use a set
    # comparison: the two rows share a transaction and can collide on created_at,
    # so order is not guaranteed (notably on SQLite).
    persisted = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    assert len(persisted) == 2
    assert {m.role for m in persisted} == {MessageRole.user, MessageRole.assistant}


def test_handle_produces_outcome_and_persists_only_assistant_message(
    db_session: Session,
) -> None:
    tenant = _make_persisted_tenant(db_session, name="Acme")
    chat = _make_persisted_chat(db_session, tenant)

    ctx = _make_handler_context(db=db_session, tenant=tenant, chat=chat)
    outcome = GreetingHandler()._handle_sync(
        ctx,
        db_session,
        LocalizationResult(text="Hello, I am the Acme assistant.", tokens_used=7),
    )

    assert outcome.text == "Hello, I am the Acme assistant."
    assert outcome.tokens_used == 7
    assert outcome.document_ids == []
    assert outcome.chat_ended is False

    # Only the assistant greeting is persisted — no empty user-message row.
    persisted = db_session.query(Message).filter(Message.chat_id == chat.id).all()
    roles = [m.role for m in persisted]
    assert roles == [MessageRole.assistant]


@pytest.mark.asyncio
async def test_build_greeting_result_passes_product_name_and_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    async def fake_generate(**kwargs: Any) -> LocalizationResult:
        captured_kwargs.update(kwargs)
        return LocalizationResult(text="Hello, I am the Acme assistant.", tokens_used=7)

    monkeypatch.setattr(
        "backend.chat.handlers.greeting.generate_greeting_in_language_result",
        fake_generate,
    )

    result = await _build_greeting_result(
        product_name="Acme",
        response_language="en",
        api_key="sk-test",
    )

    assert result.text == "Hello, I am the Acme assistant."
    assert captured_kwargs["product_name"] == "Acme"
    assert captured_kwargs["target_language"] == "en"
    assert "Acme" in captured_kwargs["fallback_text"]


def test_resolve_product_name_falls_back_to_default_when_tenant_missing(
    db_session: Session,
) -> None:
    assert _resolve_product_name(tenant=None, db=db_session) == "this product"


def test_resolve_product_name_uses_tenant_name_when_no_profile(
    db_session: Session,
) -> None:
    tenant = _make_persisted_tenant(db_session, name="My Co")
    assert _resolve_product_name(tenant=tenant, db=db_session) == "My Co"


def test_resolve_product_name_skips_db_when_profile_passed(db_session: Session) -> None:
    """Caller-provided profile short-circuits the DB lookup."""

    class _SentinelDb:
        def query(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("DB lookup must not happen when profile is provided")

    tenant = _make_persisted_tenant(db_session, name="No Lookup")
    profile = TenantProfile(tenant_id=tenant.id, product_name="ShinyProduct")

    assert (
        _resolve_product_name(tenant=tenant, db=_SentinelDb(), profile=profile)
        == "ShinyProduct"
    )
