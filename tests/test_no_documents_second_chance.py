"""The slow-path ``no_documents`` verdict gets the same second chance as ``low_similarity``.

"Nothing found in the knowledge base" is detected twice: by the zero-hits fast
path, which asks the user to rephrase once before it escalates, and by
``should_escalate``'s ``chunk_count == 0`` on a turn an FAQ or quick answer
carried while the document search came back empty. The second one used to
offer a support ticket on its very first occurrence. It now shares the
``low_similarity`` two-strike tracker: the first such turn keeps the generated
answer, and the handoff waits for a second weak turn of either flavour.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import LanguageDetectionResult
from backend.chat.service import (
    ChatPipelineResult,
    RetrievalContext,
    process_chat_message,
)
from backend.models import Chat, EscalationTrigger
from backend.search.service import build_reliability_assessment
from tests.conftest import register_and_verify_user, set_client_openai_key

GENERATED_ANSWER = "The FAQ entry says the limit is per workspace."
REPHRASE_PROMPT = "REPHRASE_PROMPT"
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


def _empty_retrieval() -> RetrievalContext:
    """The document search returned no chunks at all."""
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


def _setup(tenant: TestClient, db_session: Session, email: str) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Documents Tenant"},
    ).json()
    set_client_openai_key(tenant, token)
    return uuid.UUID(created["id"]), created["api_key"]


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
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
    answer: str = GENERATED_ANSWER,
    retrieval: RetrievalContext | None = None,
    escalation_recommended: bool = True,
    escalation_trigger: EscalationTrigger | None = EscalationTrigger.no_documents,
    llm_needs_human: bool = False,
    is_reject: bool = False,
    reject_reason: str | None = None,
) -> None:
    _retrieval = _empty_retrieval() if retrieval is None else retrieval

    async def _pipeline(*args, **kwargs) -> ChatPipelineResult:
        return ChatPipelineResult(
            raw_answer=answer,
            final_answer=answer,
            tokens_used=3,
            strategy="rag_only",
            reject_reason=reject_reason,
            is_reject=is_reject,
            is_faq_direct=False,
            retrieval=_retrieval,
            escalation_recommended=escalation_recommended,
            escalation_trigger=escalation_trigger,
            llm_needs_human=llm_needs_human,
        )

    monkeypatch.setattr("backend.chat.service.async_run_chat_pipeline", _pipeline)


def _turn_props(events: list[dict]) -> dict:
    return next(e for e in events if e["event"] == "chat.turn")["properties"]


def _chat(db_session: Session, session_id: uuid.UUID) -> Chat:
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    return chat


def test_first_zero_chunk_turn_keeps_its_answer(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An FAQ-carried turn with no document chunks answers instead of offering a ticket."""
    tenant_id, api_key = _setup(tenant, db_session, "nodocs-first@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(monkeypatch)

    outcome = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == GENERATED_ANSWER
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None
    # ...and the shared tracker is armed so the next weak turn escalates.
    assert chat.last_reply_was_low_confidence is True

    props = _turn_props(events)
    assert props["escalated"] is False
    assert props["handoff_stood_down"] is False


def test_second_zero_chunk_turn_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _setup(tenant, db_session, "nodocs-second@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(monkeypatch)

    first = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )
    assert first.text == GENERATED_ANSWER
    events.clear()
    outcome = process_chat_message(
        tenant_id, "and per seat?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.no_documents.value
    )
    assert _turn_props(events)["escalated"] is True


def test_zero_chunk_then_weak_turn_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two strikes count across both weak flavours, not one counter each."""
    tenant_id, api_key = _setup(tenant, db_session, "nodocs-then-weak@example.com")
    session_id = uuid.uuid4()
    _patch_common(monkeypatch)
    _patch_pipeline(monkeypatch)

    process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )
    _patch_pipeline(
        monkeypatch,
        retrieval=_weak_retrieval(),
        escalation_trigger=EscalationTrigger.low_similarity,
    )
    outcome = process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.low_similarity.value
    )


def test_weak_then_zero_chunk_turn_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same in the other order, so a conversation cannot alternate forever."""
    tenant_id, api_key = _setup(tenant, db_session, "weak-then-nodocs@example.com")
    session_id = uuid.uuid4()
    _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        retrieval=_weak_retrieval(),
        escalation_trigger=EscalationTrigger.low_similarity,
    )

    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    _patch_pipeline(monkeypatch)
    outcome = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.no_documents.value
    )


def test_zero_hits_fast_path_escalation_is_not_deferred(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast path already spent its own second chance on the rephrase prompt.

    Its escalation reaches the handler with the same shape as the slow path, so
    the deferral must read ``last_reply_was_rephrase_prompt`` as a strike
    already taken and let the offer through.
    """
    tenant_id, api_key = _setup(tenant, db_session, "nodocs-fastpath@example.com")
    session_id = uuid.uuid4()
    _patch_common(monkeypatch)
    _patch_pipeline(
        monkeypatch,
        answer=REPHRASE_PROMPT,
        escalation_recommended=False,
        escalation_trigger=None,
        is_reject=True,
        reject_reason="rephrase",
    )

    first = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )
    assert first.text == REPHRASE_PROMPT
    assert _chat(db_session, session_id).last_reply_was_rephrase_prompt is True

    _patch_pipeline(monkeypatch, answer=REPHRASE_PROMPT)
    outcome = process_chat_message(
        tenant_id, "the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == PRE_CONFIRM
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.no_documents.value
    )


def test_needs_human_marker_still_offers_the_handoff(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead-end reply gets its offer on the first zero-chunk turn regardless."""
    tenant_id, api_key = _setup(tenant, db_session, "nodocs-needs-human@example.com")
    session_id = uuid.uuid4()
    events = _patch_common(monkeypatch)
    _patch_pipeline(monkeypatch, llm_needs_human=True)

    outcome = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert PRE_CONFIRM in outcome.text
    chat = _chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.llm_self_offer.value
    )
    assert _turn_props(events)["handoff_stood_down"] is False
