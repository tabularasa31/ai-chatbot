"""A ticket must record the reason that actually caused the handoff.

Loop detection and the exhausted clarification budget both force an escalation
on top of the retrieval verdict, and both used to be written to the ticket as
``low_similarity`` — so counting tickets by trigger hid every loop-caused one
inside "weak answer". They now carry ``loop_detected`` / ``clarify_loop_limit``.

This is bookkeeping only: the offer the user sees and the turn it arrives on
must not move, which is what the variant and turn-parity assertions below pin.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.decision import MAX_CLARIFICATIONS_PER_SESSION
from backend.chat.handlers.rag import LoopSignal
from backend.chat.service import process_chat_message
from backend.models import EscalationTicket, EscalationTrigger
from tests.test_clarifying_reply_survives_handoff import (
    CLARIFYING_ANSWER,
    PLAIN_ANSWER,
    PRE_CONFIRM,
    _chat,
    _empty_retrieval,
    _patch_common,
    _patch_pipeline,
    _setup,
    _turn_props,
    _weak_retrieval,
)


def _capture_offer_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every kwarg the pre-confirm renderer is called with.

    The rendered text is a function of these arguments alone; the trigger is
    not among them, which is what keeps the offer identical while the recorded
    reason changes.
    """
    calls: list[dict[str, Any]] = []

    async def _render(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return Mock(message_to_user=PRE_CONFIRM, tokens_used=1)

    monkeypatch.setattr("backend.chat.service.render_pre_confirm_text", _render)
    return calls


def _patch_confirmation_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the next turn a clean ``yes`` that mints the ticket."""

    async def _yes(**_kwargs: Any) -> tuple[str, int]:
        return "yes", 0

    async def _handoff(**_kwargs: Any) -> Any:
        return Mock(
            message_to_user="Forwarded to the support team.",
            followup_decision=None,
            tokens_used=0,
        )

    monkeypatch.setattr("backend.chat.service.classify_pre_confirm_reply", _yes)
    monkeypatch.setattr("backend.chat.service.complete_escalation_openai_turn", _handoff)
    monkeypatch.setattr(
        "backend.escalation.service._notify_tenant_new_ticket",
        lambda *_a, **_k: None,
    )


def _patch_loop_signal(monkeypatch: pytest.MonkeyPatch, detected: bool) -> None:
    signal = (
        LoopSignal(
            detected=True,
            docs_repeat=True,
            doc_overlap_ratio=1.0,
            questions_repeat=True,
            question_similarity=1.0,
            window_size=3,
        )
        if detected
        else LoopSignal()
    )
    monkeypatch.setattr(
        "backend.chat.handlers.rag._compute_loop_signal", lambda *_a, **_k: signal
    )


def _ticket(db_session: Session, tenant_id: uuid.UUID) -> EscalationTicket:
    return (
        db_session.query(EscalationTicket)
        .filter(EscalationTicket.tenant_id == tenant_id)
        .one()
    )


def test_loop_escalation_records_loop_detected(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handoff forced by loop detection is a loop_detected ticket.

    Also the turn-parity proof for this path: the turn before the loop signal
    fires answers normally, and the offer arrives on the turn that carries the
    signal — exactly where it did when the ticket said ``low_similarity``.
    """
    tenant_id, api_key = _setup(tenant, db_session, "reason-loop@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    offers = _capture_offer_calls(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=_empty_retrieval(),
        escalation_recommended=False,
        escalation_trigger=None,
        llm_clarifying=False,
    )

    _patch_loop_signal(monkeypatch, detected=False)
    first = process_chat_message(
        tenant_id, "the widget shows an error", session_id, db_session, api_key=api_key
    )
    assert first.text == PLAIN_ANSWER
    assert _chat(db_session, session_id).escalation_pre_confirm_pending is False
    assert offers == []

    events.clear()
    _patch_loop_signal(monkeypatch, detected=True)
    second = process_chat_message(
        tenant_id, "the widget still shows an error", session_id, db_session, api_key=api_key
    )

    assert second.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.loop_detected.value
    )
    assert _turn_props(events)["turn_outcome"] == "escalate"
    # The offer is the same one this path rendered before: same variant, and
    # the trigger never reaches the renderer.
    assert [c["variant"] for c in offers] == ["no_answer"]
    assert "trigger" not in offers[0]

    _patch_confirmation_turn(monkeypatch)
    process_chat_message(tenant_id, "yes", session_id, db_session, api_key=api_key)

    assert _ticket(db_session, tenant_id).trigger is EscalationTrigger.loop_detected


def test_clarification_ceiling_records_clarify_loop_limit(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handoff forced by the exhausted budget is a clarify_loop_limit ticket.

    Turn parity: with budget left the clarifying question reaches the user, and
    only the turn that finds the budget spent replaces it with the offer.
    """
    tenant_id, api_key = _setup(tenant, db_session, "reason-budget@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    offers = _capture_offer_calls(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=False,
        escalation_trigger=None,
        llm_clarifying=True,
    )

    first = process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    assert first.text == CLARIFYING_ANSWER
    assert offers == []

    chat = _chat(db_session, session_id)
    chat.clarification_count = MAX_CLARIFICATIONS_PER_SESSION
    db_session.add(chat)
    db_session.commit()

    events.clear()
    second = process_chat_message(
        tenant_id, "and what about domains?", session_id, db_session, api_key=api_key
    )

    assert second.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.clarify_loop_limit.value
    )
    props = _turn_props(events)
    assert props["turn_outcome"] == "escalate"
    # The override re-arms the handoff, so nothing was stood down for the
    # clarifying question the model wrote on this turn.
    assert props["handoff_stood_down"] is False
    assert [c["variant"] for c in offers] == ["no_answer"]
    assert "trigger" not in offers[0]

    _patch_confirmation_turn(monkeypatch)
    process_chat_message(tenant_id, "yes", session_id, db_session, api_key=api_key)

    assert _ticket(db_session, tenant_id).trigger is EscalationTrigger.clarify_loop_limit


@pytest.mark.parametrize(
    "retrieval_factory, pipeline_trigger, email",
    [
        (_weak_retrieval, EscalationTrigger.low_similarity, "reason-weak@example.com"),
        (_empty_retrieval, EscalationTrigger.no_documents, "reason-empty@example.com"),
    ],
)
def test_retrieval_escalations_keep_their_own_trigger(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    retrieval_factory: Any,
    pipeline_trigger: EscalationTrigger,
    email: str,
) -> None:
    """The new reasons must not leak into the genuine retrieval paths."""
    tenant_id, api_key = _setup(tenant, db_session, email)
    session_id = uuid.uuid4()
    _patch_common(monkeypatch)
    offers = _capture_offer_calls(monkeypatch)
    _patch_loop_signal(monkeypatch, detected=False)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=retrieval_factory(),
        escalation_recommended=True,
        escalation_trigger=pipeline_trigger,
        llm_clarifying=False,
    )

    # First weak turn is deferred; the second one escalates.
    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    outcome = process_chat_message(
        tenant_id, "so how do I run one?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_context["trigger"] == pipeline_trigger.value
    assert [c["variant"] for c in offers] == ["no_answer"]

    _patch_confirmation_turn(monkeypatch)
    process_chat_message(tenant_id, "yes", session_id, db_session, api_key=api_key)

    assert _ticket(db_session, tenant_id).trigger is pipeline_trigger
