"""Dead-end answers: the bot must never leave the user at an unreachable channel.

Origin (Langfuse session 10ac60ad, tenant TurboFlare): asked "почему не приходит
код?", the bot recited the docs and closed with "write to the support chat in the
control panel (available after signing in)" — a channel the user was already
inside, reached by the sign-in they could not complete. The turn was classified
as a blocking clarify, but no question was asked and the clarification budget was
charged anyway; the handoff offer that would have made the reply actionable was a
prompt rule the model simply skipped.

Covered here:
  * the prompt states that the bot IS the support channel and defines the
    machine-readable ``<needs_human/>`` marker instead of asking the model to
    compose the handoff offer itself;
  * a required clarification reaches the model as an instruction for the turn;
  * the handler appends the localized offer and arms pre-confirm on the marker;
  * the clarification budget is charged only when a question was actually asked;
  * identity reaches the prompt as booleans, never as the values themselves.
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.decision import (
    Decision,
    DecisionKind,
    reply_is_clarifying_question,
    requires_blocking_clarify,
)
from backend.chat.prompts import _user_context_prompt_line, build_rag_prompt
from backend.chat.streaming import _strip_and_detect_markers
from tests._async_utils import as_async as _as_async
from tests.conftest import register_and_verify_user, set_client_openai_key


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------


def test_prompt_says_the_bot_is_the_support_channel() -> None:
    prompt = build_rag_prompt("How do I reset it?", ["some documentation chunk"])

    assert "You ARE the tenant's support chat" in prompt
    assert "Never send the user to a support channel they are already using" in prompt
    assert "gated" in prompt and "not working for them" in prompt


def test_prompt_asks_for_the_marker_not_for_a_composed_offer() -> None:
    prompt = build_rag_prompt("How do I reset it?", ["some documentation chunk"])

    assert "`<needs_human/>`" in prompt
    assert "do NOT write the handoff offer yourself" in prompt


def test_required_clarification_becomes_a_turn_instruction() -> None:
    without = build_rag_prompt("Why is the code not arriving?", ["chunk"])
    with_requirement = build_rag_prompt(
        "Why is the code not arriving?",
        ["chunk"],
        require_clarification="low_retrieval_confidence",
    )

    assert "CLARIFICATION (this turn)" not in without
    assert "MUST end with exactly one short clarifying question" in with_requirement
    assert "low_retrieval_confidence" in with_requirement
    assert "do not redirect the user to another support channel" in with_requirement


def test_exhausted_budget_still_wins_over_a_required_clarification() -> None:
    prompt = build_rag_prompt(
        "Why is the code not arriving?",
        ["chunk"],
        allow_clarification=False,
        require_clarification="low_retrieval_confidence",
    )

    assert "Do not ask any clarifying question" in prompt
    assert "MUST end with exactly one short clarifying question" not in prompt


def test_user_context_line_reports_identity_without_leaking_it() -> None:
    line = _user_context_prompt_line(
        {"user_id": "hint:someone@example.com", "email": "someone@example.com", "plan_tier": "free"}
    )

    assert line is not None
    assert "identified=yes" in line
    assert "contact_email_on_file=yes" in line
    assert "someone@example.com" not in line


def test_user_context_line_stays_quiet_for_anonymous_visitors() -> None:
    assert _user_context_prompt_line({"browser_locale": "ru-RU"}) is None
    assert _user_context_prompt_line(None) is None


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------


def _retrieval(*, score: float, chunks: list[str], cap_reason: str | None = None) -> Mock:
    reliability = Mock(cap=None, cap_reason=cap_reason, score="low")
    return Mock(best_confidence_score=score, chunk_texts=chunks, reliability=reliability)


def test_blocking_clarify_is_known_before_generation() -> None:
    assert (
        requires_blocking_clarify(
            retrieval=_retrieval(score=0.36, chunks=["doc"]),
            clarification_budget_available=True,
        )
        == "low_retrieval_confidence"
    )


def test_no_clarify_requirement_without_budget_or_confidence() -> None:
    assert (
        requires_blocking_clarify(
            retrieval=_retrieval(score=0.36, chunks=["doc"]),
            clarification_budget_available=False,
        )
        is None
    )
    assert (
        requires_blocking_clarify(
            retrieval=_retrieval(score=0.62, chunks=["doc"]),
            clarification_budget_available=True,
        )
        is None
    )
    # Zero chunks escalate (low_confidence_no_path); they never clarify.
    assert (
        requires_blocking_clarify(
            retrieval=_retrieval(score=0.1, chunks=[]),
            clarification_budget_available=True,
        )
        is None
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Какой именно код вы имеете в виду?", True),
        ("Which code do you mean — the sign-up one or the login one?", True),
        ("«Точно?»", True),
        ("那是哪个验证码？", True),
        ("Возможные причины: подождите 2 минуты.", False),
        ("", False),
        (None, False),
    ],
)
def test_reply_is_clarifying_question(text: str | None, expected: bool) -> None:
    assert reply_is_clarifying_question(text) is expected


def test_trace_reports_an_uncharged_clarification_honestly() -> None:
    decision = Decision(
        kind=DecisionKind.clarify,
        clarify_reason="low_retrieval_confidence",
        clarify_type="blocking",
    )

    charged = decision.trace_dict(2, clarification_charged=True)
    skipped = decision.trace_dict(2, clarification_charged=False)

    assert charged["clarification_count_after"] == 3
    assert charged["clarification_charged"] is True
    assert skipped["clarification_count_after"] == 2
    assert skipped["clarification_charged"] is False


def test_handoff_marker_is_detected_and_stripped() -> None:
    assert _strip_and_detect_markers("Напишите в чат поддержки. <needs_human/>") == (
        "Напишите в чат поддержки.",
        False,
        True,
    )
    assert _strip_and_detect_markers("Готово.") == ("Готово.", False, False)


# ---------------------------------------------------------------------------
# End to end through /chat
# ---------------------------------------------------------------------------


DEAD_END_ANSWER = (
    "Подождите 2 минуты и проверьте баланс телефона. "
    "Если код всё равно не приходит, напишите в чат поддержки в панели управления."
)
OFFER_TEXT = "Связаться с командой поддержки можно прямо здесь. Передать им ваш вопрос?"


def _tenant_api_key(tenant: TestClient, db_session: Session, email: str, name: str) -> str:
    token = register_and_verify_user(tenant, db_session, email=email)
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert created.status_code == 201
    set_client_openai_key(tenant, token)
    return created.json()["api_key"]


def _patch_retrieval(monkeypatch: pytest.MonkeyPatch, *, score: float) -> None:
    from backend.chat.service import RetrievalContext
    from backend.search.service import build_reliability_assessment

    def _fake_retrieve(*_args, **_kwargs) -> RetrievalContext:
        return RetrievalContext(
            chunk_texts=["TurboFlare > 1.2. Что делать, если не приходит SMS или письмо?"],
            document_ids=[],
            scores=[score],
            mode="hybrid",
            best_rank_score=0.76,
            best_confidence_score=score,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.76, result_count=1),
        )

    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context", _as_async(_fake_retrieve)
    )


def _patch_generation(
    monkeypatch: pytest.MonkeyPatch, *, answer: str, needs_human: bool
) -> None:
    async def _fake_generate(*_args, **_kwargs):
        return (answer, 50, 20, 30, False, needs_human)

    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer", _fake_generate
    )


def test_needs_human_reply_gets_the_offer_appended_and_arms_pre_confirm(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead end from the original trace, rescued by the backend.

    The model produces the documentation answer and marks the turn as one only
    a human can close. The reply the user sees must carry a handoff offer they
    can accept, and the chat must be armed so their "да" creates the ticket.
    """
    from backend.models import Chat, EscalationTrigger

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    _patch_retrieval(monkeypatch, score=0.5)
    _patch_generation(monkeypatch, answer=DEAD_END_ANSWER, needs_human=True)
    monkeypatch.setattr(
        "backend.chat.service.render_pre_confirm_text",
        _as_async(lambda **_kw: Mock(message_to_user=OFFER_TEXT, tokens_used=0)),
    )

    api_key = _tenant_api_key(
        tenant, db_session, "deadend-offer@example.com", "Dead End Offer Tenant"
    )
    session_id = uuid.uuid4()
    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(session_id), "question": "Почему не приходит код ?"},
    )

    assert response.status_code == 200
    text = response.json()["text"]
    assert DEAD_END_ANSWER in text, "the documentation answer must survive"
    assert OFFER_TEXT in text, "the user must be given a channel they can actually use"

    db_session.expire_all()
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.llm_self_offer.value
    )
    assert chat.escalation_pre_confirm_context["primary_question"] == "Почему не приходит код ?"


def test_plain_answer_is_left_alone(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No marker, no rescue: a resolved answer must not grow a handoff question."""
    from backend.models import Chat

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    _patch_retrieval(monkeypatch, score=0.5)
    _patch_generation(monkeypatch, answer="Код приходит в течение 2 минут.", needs_human=False)
    monkeypatch.setattr(
        "backend.chat.service.render_pre_confirm_text",
        _as_async(lambda **_kw: Mock(message_to_user=OFFER_TEXT, tokens_used=0)),
    )

    api_key = _tenant_api_key(
        tenant, db_session, "deadend-plain@example.com", "Plain Answer Tenant"
    )
    session_id = uuid.uuid4()
    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(session_id), "question": "Через сколько приходит код?"},
    )

    assert response.status_code == 200
    assert OFFER_TEXT not in response.json()["text"]

    db_session.expire_all()
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.escalation_pre_confirm_pending is False


@pytest.mark.parametrize(
    "answer,expected_count",
    [
        ("Возможные причины: SMS, письмо, спам-папка.", 0),
        ("Какой именно код вы имеете в виду?", 1),
    ],
)
def test_clarification_budget_follows_the_reply_not_the_verdict(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    expected_count: int,
) -> None:
    """A blocking clarify the model answered instead of asking costs nothing.

    Charging it anyway used to exhaust the per-session budget on questions the
    user was never asked, and the next genuinely ambiguous turn escalated on
    clarify_loop_limit instead of clarifying.
    """
    from backend.models import Chat

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]
    # Below KB_LOW_CONFIDENCE_THRESHOLD with chunks present → blocking clarify.
    _patch_retrieval(monkeypatch, score=0.36)
    _patch_generation(monkeypatch, answer=answer, needs_human=False)

    api_key = _tenant_api_key(
        tenant,
        db_session,
        f"deadend-budget-{expected_count}@example.com",
        f"Budget Tenant {expected_count}",
    )
    session_id = uuid.uuid4()
    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(session_id), "question": "Почему не приходит код ?"},
    )

    assert response.status_code == 200
    db_session.expire_all()
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    assert chat.clarification_count == expected_count
