"""Pipeline routing for the relevance guard's category verdicts (86ey7x2mh).

The guard classifies each message into relevant / offtopic / support_complaint
/ social (see ``backend/guards/relevance_checker.py``). Covers the four
acceptance scenarios:

* An anaphoric follow-up in an on-topic dialog: the guard call receives the
  rendered dialog tail so it can resolve the follow-up against context.
* A complaint about support being unresponsive gets the pre-confirm
  escalation offer (``user_complaint`` trigger), not an off-topic refusal.
* A social turn (thanks / farewell) that reached the guard gets a polite
  acknowledgement, not a refusal.
* Real off-topic is still rejected with the refusal text.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import LocalizationResult
from backend.chat.service import RetrievalContext, process_chat_message
from backend.faq.faq_matcher import FAQMatchResult
from backend.models import Chat, Message, MessageRole, Tenant
from backend.search.service import build_reliability_assessment

from tests._async_utils import as_async as _as_async, as_async_generate
from tests.conftest import register_and_verify_user, set_client_openai_key


def _create_client(http: TestClient, db: Session, *, email: str) -> tuple[Tenant, str]:
    token = register_and_verify_user(http, db, email=email)
    cl_resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Guard Categories Tenant"},
    )
    assert cl_resp.status_code in (200, 201), cl_resp.text
    set_client_openai_key(http, token)
    api_key = cl_resp.json()["api_key"]
    client_row = db.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None
    return client_row, api_key


def _nonempty_retrieval() -> RetrievalContext:
    return RetrievalContext(
        chunk_texts=["SSL setup: upload a certificate in settings."],
        document_ids=[uuid.uuid4()],
        scores=[0.9],
        mode="hybrid",
        best_rank_score=0.9,
        best_confidence_score=0.9,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.9, result_count=1),
        vector_similarities=None,
    )


def _stub_common(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injection clean, FAQ no-match, no rewrites, no legacy escalation."""
    monkeypatch.setattr(
        "backend.chat.service.async_detect_injection",
        _as_async(lambda *_a, **_kw: SimpleNamespace(
            detected=False, level=None, method=None, pattern=None, score=None,
        )),
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *_a, **_kw: (False, None),
    )
    monkeypatch.setattr(
        "backend.chat.service.async_match_faq",
        _as_async(lambda **_kw: FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        )),
    )
    monkeypatch.setattr(
        "backend.chat.service._start_mode_b_followup", lambda _tenant_id: None
    )

    async def _no_rewrite(*_a, **_kw):
        return None

    monkeypatch.setattr("backend.chat.service.async_semantic_query_rewrite", _no_rewrite)
    monkeypatch.setattr(
        "backend.chat.service.async_semantic_query_rewrite_for_kb", _no_rewrite
    )
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _nonempty_retrieval()),
    )


_PROFILE_STUB = SimpleNamespace(product_name="Product", topics=["Topic"])


def _stub_guard_verdict(
    monkeypatch: pytest.MonkeyPatch, verdict: tuple[bool, str, object]
) -> list[dict]:
    calls: list[dict] = []

    async def _guard(**kwargs):
        calls.append(kwargs)
        return verdict

    monkeypatch.setattr(
        "backend.chat.service.async_check_relevance_with_profile", _guard
    )
    return calls


def _identity_localize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.guards.reject_response.localize_text_result",
        lambda **kwargs: LocalizationResult(
            text=kwargs["canonical_text"], tokens_used=0
        ),
    )


def test_followup_guard_receives_dialog_context(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: in an on-topic dialog the guard is given the dialog tail, so an
    anaphoric follow-up is classified with context (and here passes through
    to the normal RAG answer)."""
    cl_row, api_key = _create_client(tenant, db_session, email="gc-followup@example.com")
    _stub_common(monkeypatch)
    calls = _stub_guard_verdict(monkeypatch, (True, "relevant", _PROFILE_STUB))
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *_a, **_kw: ("You can use it, yes.", 7)),
    )

    # Seed an on-topic dialog in the same session.
    session_id = uuid.uuid4()
    chat = Chat(tenant_id=cl_row.id, session_id=session_id)
    db_session.add(chat)
    db_session.commit()
    db_session.add_all(
        [
            Message(chat_id=chat.id, role=MessageRole.user, content="How do I set up SSL?"),
            Message(
                chat_id=chat.id,
                role=MessageRole.assistant,
                content="Upload a certificate in the domain settings.",
            ),
        ]
    )
    db_session.commit()

    outcome = process_chat_message(
        cl_row.id,
        "And what if I have a cloudflare certificate here?",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert outcome.text == "You can use it, yes."
    assert calls, "relevance guard was not called"
    dialog_context = calls[0].get("dialog_context")
    assert dialog_context is not None
    assert "How do I set up SSL?" in dialog_context
    assert "Upload a certificate" in dialog_context


def test_support_complaint_offers_escalation_not_refusal(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: a complaint about support silence arms the pre-confirm escalation
    offer (user_complaint trigger, support_complaint wording) instead of the
    off-topic refusal."""
    cl_row, api_key = _create_client(tenant, db_session, email="gc-complaint@example.com")
    _stub_common(monkeypatch)
    _stub_guard_verdict(monkeypatch, (False, "support_complaint", _PROFILE_STUB))
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("answer LLM must not run on a support complaint")
        )),
    )

    render_calls: list[dict] = []

    def _render(**kwargs):
        render_calls.append(kwargs)
        return SimpleNamespace(
            message_to_user="Sorry for the wait — forward this to support?",
            tokens_used=1,
        )

    monkeypatch.setattr("backend.chat.service.render_pre_confirm_text", _render)

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id,
        "They have not answered me for two weeks already",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert outcome.text == "Sorry for the wait — forward this to support?"
    assert render_calls and render_calls[0]["variant"] == "support_complaint"

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is True
    assert chat.escalation_pre_confirm_context["trigger"] == "user_complaint"


def test_social_turn_gets_polite_acknowledgement(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: a farewell/thanks that reached the guard is answered politely,
    not refused, and never arms escalation."""
    cl_row, api_key = _create_client(tenant, db_session, email="gc-social@example.com")
    _stub_common(monkeypatch)
    _stub_guard_verdict(monkeypatch, (False, "social", _PROFILE_STUB))
    _identity_localize(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("answer LLM must not run on a social turn")
        )),
    )

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id,
        "Thanks a lot, I will wait for the reply, goodbye",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert "Thank you for reaching out" in outcome.text
    assert "can't help" not in outcome.text

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is False


def test_social_question_about_bot_gets_short_reply(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: a social question about the bot itself ("do you speak Russian?")
    gets a short friendly invite, not the off-topic refusal, and never runs the
    answer LLM or arms escalation."""
    cl_row, api_key = _create_client(
        tenant, db_session, email="gc-social-q@example.com"
    )
    _stub_common(monkeypatch)
    _stub_guard_verdict(monkeypatch, (False, "social_question", _PROFILE_STUB))
    _identity_localize(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("answer LLM must not run on a social question")
        )),
    )

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id,
        "Hi, can you speak Russian?",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert "what would you like to know" in outcome.text.lower()
    assert "can't help" not in outcome.text

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is False


def test_short_social_question_classified_on_first_zero_hits_turn(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (short form): a ≤4-word bot-meta question ("do you speak English?")
    bypasses the pre-retrieval guard, so on the first zero-RAG-hits turn the
    pipeline re-classifies it via the force check and returns the friendly
    social reply instead of the generic rephrase prompt."""
    cl_row, api_key = _create_client(
        tenant, db_session, email="gc-short-social-q@example.com"
    )
    _stub_common(monkeypatch)
    # Retrieval comes back empty so the zero-hits fast path runs.
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *_a, **_kw: RetrievalContext(
            chunk_texts=[],
            document_ids=[],
            scores=[],
            mode="none",
            best_rank_score=None,
            best_confidence_score=None,
            confidence_source="none",
            reliability=build_reliability_assessment(top_score=0.0, result_count=0),
            vector_similarities=None,
        )),
    )
    _identity_localize(monkeypatch)

    # First (pre-retrieval) guard call bypasses on the short query; the forced
    # zero-hits re-check classifies it as a social question about the bot.
    async def _guard(**kwargs):
        if kwargs.get("force_llm_check"):
            return (False, "social_question", _PROFILE_STUB)
        return (True, "short_query_bypass", _PROFILE_STUB)

    monkeypatch.setattr(
        "backend.chat.service.async_check_relevance_with_profile", _guard
    )
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("answer LLM must not run on a social question")
        )),
    )

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id,
        "do you speak English?",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert "what would you like to know" in outcome.text.lower()
    assert "rephrase" not in outcome.text.lower()
    assert "couldn't find" not in outcome.text.lower()


def test_offtopic_is_still_rejected_with_support_offer(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: real off-topic still gets the refusal — which now ends with an
    offer to forward the request to support instead of a dead end."""
    cl_row, api_key = _create_client(tenant, db_session, email="gc-offtopic@example.com")
    _stub_common(monkeypatch)
    _stub_guard_verdict(monkeypatch, (False, "offtopic", _PROFILE_STUB))
    _identity_localize(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("answer LLM must not run on an off-topic turn")
        )),
    )

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id,
        "Please write me a long poem about flowers",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert "Sorry, but I can't help with that question" in outcome.text
    assert "forward your request" in outcome.text

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is False
