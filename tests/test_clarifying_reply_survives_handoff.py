"""A clarifying question must survive the escalate branch, and be countable.

The escalation verdict is computed from retrieval scores before generation, so
a reply the model marked ``<clarifying/>`` — the troubleshooting step that is
supposed to come before a handoff — used to be replaced wholesale by the
"shall I forward this?" offer, with a clarification charged for a reply nobody
saw. The retrieval-score escalations now stand down for that question, the
budget is charged only for a delivered one, and the turn event carries a
``turn_outcome`` that tells a diagnosing turn apart from a plain answer and
from an escalation.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.decision import MAX_CLARIFICATIONS_PER_SESSION
from backend.chat.handlers.rag import LoopSignal
from backend.chat.language import LanguageDetectionResult
from backend.chat.service import (
    ChatPipelineResult,
    RetrievalContext,
    process_chat_message,
)
from backend.models import Chat, EscalationTrigger
from backend.search.service import build_reliability_assessment
from tests.conftest import register_and_verify_user, set_client_openai_key

CLARIFYING_ANSWER = "Which page do you see the error on?"
PLAIN_ANSWER = "The limitations list covers that case."
PRE_CONFIRM = "PRE_CONFIRM_QUESTION"


class _FakeSpan:
    def end(self, **kwargs: object) -> None:
        return None


class _FakeTrace:
    def span(self, **kwargs: object) -> _FakeSpan:
        return _FakeSpan()

    def update(self, **kwargs: object) -> None:
        return None

    def promote(self, **kwargs: object) -> None:
        return None


def _weak_retrieval() -> RetrievalContext:
    """Chunks came back, but below the handoff floor."""
    return RetrievalContext(
        chunk_texts=["tunnels to origin without a public IP are not supported"],
        document_ids=[uuid.uuid4()],
        scores=[0.31],
        mode="hybrid",
        best_rank_score=0.31,
        best_confidence_score=0.31,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.31, result_count=3),
    )


def _empty_retrieval() -> RetrievalContext:
    """Retrieval returned nothing at all."""
    return RetrievalContext(
        chunk_texts=[],
        document_ids=[],
        scores=[],
        mode="hybrid",
        best_rank_score=None,
        best_confidence_score=None,
        confidence_source=None,
        reliability=build_reliability_assessment(top_score=0.0, result_count=0),
    )


def _setup(tenant: TestClient, db_session: Session, email: str) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Clarify Survival Tenant"},
    ).json()
    set_client_openai_key(tenant, token)
    return uuid.UUID(created["id"]), created["api_key"]


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Silence tracing/background work and record captured analytics events."""
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: _FakeTrace())
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda text: LanguageDetectionResult("en", 0.99, True),
    )
    monkeypatch.setattr("backend.chat.service._try_ingest_gap_signal", lambda **kwargs: None)
    monkeypatch.setattr(
        "backend.chat.service._trigger_log_analysis_threshold",
        lambda *_a, **_k: None,
    )

    events: list[dict] = []

    def _record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.events.capture_event", _record)

    async def _fake_render_pre_confirm(**kwargs):
        return type(
            "EscalationOut", (), {"message_to_user": PRE_CONFIRM, "tokens_used": 1}
        )()

    monkeypatch.setattr(
        "backend.chat.service.render_pre_confirm_text", _fake_render_pre_confirm
    )
    return events


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answer: str,
    retrieval: RetrievalContext,
    escalation_recommended: bool,
    escalation_trigger: EscalationTrigger | None,
    llm_clarifying: bool,
) -> None:
    async def _pipeline(*args, **kwargs) -> ChatPipelineResult:
        return ChatPipelineResult(
            raw_answer=answer,
            final_answer=answer,
            tokens_used=3,
            strategy="rag_only",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=False,
            retrieval=retrieval,
            escalation_recommended=escalation_recommended,
            escalation_trigger=escalation_trigger,
            llm_clarifying=llm_clarifying,
        )

    monkeypatch.setattr("backend.chat.service.async_run_chat_pipeline", _pipeline)


def _turn_props(events: list[dict]) -> dict:
    return next(e for e in events if e["event"] == "chat.turn")["properties"]


def _chat(db_session: Session, session_id: uuid.UUID) -> Chat:
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    return chat


def test_zero_retrieval_clarifying_question_reaches_user(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``no_documents`` escalates immediately — but not over a clarifying question."""
    tenant_id, api_key = _setup(tenant, db_session, "clarify-zero@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_empty_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.no_documents,
        llm_clarifying=True,
    )

    outcome = process_chat_message(
        tenant_id, "the widget shows an error", session_id, db_session, api_key=api_key
    )

    assert outcome.text == CLARIFYING_ANSWER
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None
    # The user saw the question, so it costs budget.
    assert chat.clarification_count == 1

    props = _turn_props(events)
    assert props["turn_outcome"] == "diagnose"
    assert props["clarifying_reply"] is True
    assert props["handoff_stood_down"] is True
    assert props["escalated"] is False


def test_second_weak_turn_clarifying_question_reaches_user(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second consecutive weak turn hands off — unless the bot asked something."""
    tenant_id, api_key = _setup(tenant, db_session, "clarify-second@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.low_similarity,
        llm_clarifying=False,
    )
    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    assert _chat(db_session, session_id).last_reply_was_low_confidence is True

    events.clear()
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.low_similarity,
        llm_clarifying=True,
    )
    outcome = process_chat_message(
        tenant_id, "so how do I run one?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == CLARIFYING_ANSWER
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.clarification_count == 1
    # Standing down does not restart the two-strike count: the turn was weak.
    assert chat.last_reply_was_low_confidence is True

    props = _turn_props(events)
    assert props["turn_outcome"] == "diagnose"
    assert props["handoff_stood_down"] is True


def test_second_weak_turn_without_clarifying_still_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A weak turn whose reply asks the user nothing still offers the handoff."""
    tenant_id, api_key = _setup(tenant, db_session, "clarify-plain@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.low_similarity,
        llm_clarifying=False,
    )

    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    events.clear()
    outcome = process_chat_message(
        tenant_id, "so how do I run one?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.low_similarity.value
    )

    props = _turn_props(events)
    assert props["turn_outcome"] == "escalate"
    assert props["clarifying_reply"] is False
    assert props["handoff_stood_down"] is False


def test_plain_answer_turn_reports_answer_outcome(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary answer stays distinguishable from a diagnosing turn."""
    tenant_id, api_key = _setup(tenant, db_session, "clarify-answer@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=False,
        escalation_trigger=None,
        llm_clarifying=False,
    )

    outcome = process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PLAIN_ANSWER
    props = _turn_props(events)
    assert props["turn_outcome"] == "answer"
    assert props["clarifying_reply"] is False
    assert props["handoff_stood_down"] is False


def test_replaced_clarifying_question_does_not_spend_budget(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget ceiling still escalates — and charges nothing for the lost reply.

    With the clarification budget exhausted, ``decide()`` returns
    ``clarify_loop_limit`` and the handoff offer replaces the reply. The user
    never sees the question, so it must not move the counter.
    """
    tenant_id, api_key = _setup(tenant, db_session, "clarify-budget@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=False,
        escalation_trigger=None,
        llm_clarifying=False,
    )
    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    chat = _chat(db_session, session_id)
    chat.clarification_count = MAX_CLARIFICATIONS_PER_SESSION
    db_session.add(chat)
    db_session.commit()

    events.clear()
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=False,
        escalation_trigger=None,
        llm_clarifying=True,
    )
    outcome = process_chat_message(
        tenant_id, "and what about domains?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert chat.clarification_count == MAX_CLARIFICATIONS_PER_SESSION

    props = _turn_props(events)
    assert props["turn_outcome"] == "escalate"
    assert props["clarifying_reply"] is False


def test_budget_ceiling_overrules_the_stand_down(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stand-down the budget ceiling then overrules is not a stand-down.

    The second weak turn escalates from retrieval, the clarifying question
    stands that down, and ``clarify_loop_limit`` re-arms the handoff. The user
    gets the offer, so the turn must not be counted as a removed ticket.
    """
    tenant_id, api_key = _setup(tenant, db_session, "clarify-budget-override@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=PLAIN_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.low_similarity,
        llm_clarifying=False,
    )
    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    chat = _chat(db_session, session_id)
    assert chat.last_reply_was_low_confidence is True
    chat.clarification_count = MAX_CLARIFICATIONS_PER_SESSION
    db_session.add(chat)
    db_session.commit()

    events.clear()
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.low_similarity,
        llm_clarifying=True,
    )
    outcome = process_chat_message(
        tenant_id, "so how do I run one?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert chat.clarification_count == MAX_CLARIFICATIONS_PER_SESSION

    props = _turn_props(events)
    assert props["escalated"] is True
    assert props["handoff_stood_down"] is False
    assert props["clarifying_reply"] is False
    assert props["turn_outcome"] == "escalate"


def test_loop_detection_overrules_the_stand_down(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same for the loop escalation: the user got the offer, nothing stood down."""
    tenant_id, api_key = _setup(tenant, db_session, "clarify-loop-override@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.handlers.rag._compute_loop_signal",
        lambda *_a, **_k: LoopSignal(
            detected=True,
            docs_repeat=True,
            doc_overlap_ratio=1.0,
            questions_repeat=True,
            question_similarity=1.0,
            window_size=3,
        ),
    )
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_empty_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.no_documents,
        llm_clarifying=True,
    )

    outcome = process_chat_message(
        tenant_id, "the widget shows an error", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert chat.clarification_count == 0

    props = _turn_props(events)
    assert props["escalated"] is True
    assert props["handoff_stood_down"] is False
    assert props["clarifying_reply"] is False
    assert props["turn_outcome"] == "escalate"


def test_explicit_human_request_still_escalates_immediately(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outright ask for a human never reaches the RAG stand-down."""
    from backend.chat.handlers.base import ChatTurnOutcome
    from backend.chat.handlers.escalation import EscalationStateMachine

    tenant_id, api_key = _setup(tenant, db_session, "clarify-human@example.com")
    session_id = uuid.uuid4()
    _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=CLARIFYING_ANSWER,
        retrieval=_weak_retrieval(),
        escalation_recommended=True,
        escalation_trigger=EscalationTrigger.low_similarity,
        llm_clarifying=True,
    )

    async def _human_request(*_a: Any, **_k: Any) -> Any:
        return type(
            "HumanRequest",
            (),
            {
                "human_request": True,
                "human_request_explicit": True,
                "message_has_request_content": True,
                "tokens_used": 0,
            },
        )()

    monkeypatch.setattr("backend.chat.service.detect_human_request", _human_request)

    captured: dict[str, Any] = {}

    def _fake_handoff(_self: Any, ctx: Any, **kwargs: Any) -> ChatTurnOutcome:
        captured.update(kwargs)
        return ChatTurnOutcome(
            text="HANDOFF",
            document_ids=[],
            tokens_used=0,
            chat_ended=False,
            chat_id=str(ctx.chat.id),
        )

    monkeypatch.setattr(
        EscalationStateMachine, "_create_ticket_and_handoff", _fake_handoff
    )

    outcome = process_chat_message(
        tenant_id,
        "billing is broken, connect me to a human please",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert outcome.text == "HANDOFF"
    assert captured["escalation_reason"] == "explicit_human_request"
