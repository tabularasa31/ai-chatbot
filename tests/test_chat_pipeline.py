"""Tests for chat pipeline orchestration: process_chat_message."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.language import LocalizationResult
from backend.chat.service import (
    ChatPipelineResult,
    RetrievalContext,
    process_chat_message,
)
from backend.escalation.openai_escalation import complete_escalation_openai_turn
from backend.search.service import build_reliability_assessment
from tests._async_utils import as_async as _as_async
from tests.conftest import register_and_verify_user, set_client_openai_key


def test_process_chat_message_ends_followup_span_on_exception(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Chat, Tenant, EscalationTicket, EscalationTrigger, EscalationStatus

    class FakeSpan:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.followup_span = FakeSpan()

        def span(self, **kwargs: object) -> FakeSpan:
            if kwargs["name"] == "escalation-followup":
                return self.followup_span
            return FakeSpan()

        def update(self, **kwargs: object) -> None:
            return None

    token = register_and_verify_user(tenant, db_session, email="trace-followup@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Tenant"},
    )
    set_client_openai_key(tenant, token)
    client_row = db_session.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None

    chat = Chat(
        tenant_id=client_row.id,
        session_id=uuid.uuid4(),
        user_context={},
        escalation_followup_pending=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    ticket = EscalationTicket(
        tenant_id=client_row.id,
        ticket_number="ESC-0001",
        primary_question="Need support",
        trigger=EscalationTrigger.user_request,
        status=EscalationStatus.open,
        chat_id=chat.id,
        session_id=chat.session_id,
    )
    db_session.add(ticket)
    db_session.commit()

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: fake_trace)
    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        process_chat_message(
            client_row.id,
            "no thanks",
            chat.session_id,
            db_session,
            api_key=cl_resp.json()["api_key"],
        )

    assert fake_trace.followup_span.end_calls == [
        {
            "output": {"error": True},
            "level": "ERROR",
            "status_message": "boom",
        }
    ]


def test_process_chat_message_adds_variant_summary_to_trace(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Tenant
    from backend.search.service import ContradictionPair, build_reliability_assessment

    class FakeSpan:
        def end(self, **kwargs: object) -> None:
            return None

    class FakeTrace:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, object]] = []

        def span(self, **kwargs: object) -> FakeSpan:
            return FakeSpan()

        def update(self, **kwargs: object) -> None:
            self.update_calls.append(kwargs)

        def promote(self, **kwargs: object) -> None:
            return None

    token = register_and_verify_user(tenant, db_session, email="trace-chat@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Chat Tenant"},
    )
    set_client_openai_key(tenant, token)
    client_row = db_session.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None

    fake_trace = FakeTrace()
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: fake_trace)
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(lambda *args, **kwargs: RetrievalContext(
            chunk_texts=["reset password in settings"],
            document_ids=[uuid.uuid4()],
            scores=[0.93],
            mode="hybrid",
            best_rank_score=0.93,
            best_confidence_score=0.91,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(
                top_score=0.93,
                result_count=5,
                contradiction_pairs=(
                    ContradictionPair(
                        chunk_a_id="a",
                        chunk_b_id="b",
                        basis="effective_date",
                        value_a="2024-03-01",
                        value_b="2025-03-01",
                    ),
                    ContradictionPair(
                        chunk_a_id="a",
                        chunk_b_id="b",
                        basis="version",
                        value_a="v2",
                        value_b="v3",
                    ),
                ),
            ),
            variant_mode="multi",
            query_variant_count=3,
            extra_embedded_queries=2,
            extra_embedding_api_requests=0,
            extra_vector_search_calls=2,
            bm25_expansion_mode="symmetric_variants",
            bm25_query_variant_count=2,
            bm25_variant_eval_count=2,
            extra_bm25_variant_evals=1,
            bm25_merged_hit_count_before_cap=4,
            bm25_merged_hit_count_after_cap=3,
            retrieval_duration_ms=18.4,
        )),
    )
    monkeypatch.setattr(
        "backend.chat.service.generate_answer",
        lambda *args, **kwargs: ("Use the reset link in settings.", 17),
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *args, **kwargs: (False, None),
    )

    outcome = process_chat_message(
        client_row.id,
        "How do I reset my password?",
        uuid.uuid4(),
        db_session,
        api_key=cl_resp.json()["api_key"],
    )

    assert outcome.text == "Use the reset link in settings."
    assert outcome.tokens_used == 17
    assert outcome.chat_ended is False
    assert fake_trace.update_calls[-1]["metadata"]["variant_mode"] == "multi"
    assert fake_trace.update_calls[-1]["metadata"]["query_variant_count"] == 3
    assert fake_trace.update_calls[-1]["metadata"]["extra_embedded_queries"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["extra_embedding_api_requests"] == 0
    assert fake_trace.update_calls[-1]["metadata"]["extra_vector_search_calls"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["bm25_expansion_mode"] == "symmetric_variants"
    assert fake_trace.update_calls[-1]["metadata"]["bm25_query_variant_count"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["bm25_variant_eval_count"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["extra_bm25_variant_evals"] == 1
    assert fake_trace.update_calls[-1]["metadata"]["bm25_merged_hit_count_before_cap"] == 4
    assert fake_trace.update_calls[-1]["metadata"]["bm25_merged_hit_count_after_cap"] == 3
    assert fake_trace.update_calls[-1]["metadata"]["retrieval_duration_ms"] == 18.4
    assert fake_trace.update_calls[-1]["metadata"]["reliability"] == {
        "base_score": "high",
        "score": "low",
        "cap": "low",
        "cap_reason": "contradiction",
        "signals": [{"kind": "contradiction"}],
        "evidence": {
            "contradiction": {
                "pairs": [
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "effective_date",
                        "value_a": "2024-03-01",
                        "value_b": "2025-03-01",
                    },
                    {
                        "chunk_a_id": "a",
                        "chunk_b_id": "b",
                        "basis": "version",
                        "value_a": "v2",
                        "value_b": "v3",
                    },
                ]
            }
        },
    }
    assert fake_trace.update_calls[-1]["metadata"]["contradiction_detected"] is True
    assert fake_trace.update_calls[-1]["metadata"]["contradiction_count"] == 2
    assert fake_trace.update_calls[-1]["metadata"]["contradiction_pair_count"] == 1
    assert fake_trace.update_calls[-1]["metadata"]["contradiction_basis_types"] == [
        "effective_date",
        "version",
    ]
    assert fake_trace.update_calls[-1]["tags"] == ["variants:multi"]


def test_process_chat_message_returns_plain_answer_when_model_asks_to_clarify(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="clarify-domain@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Clarify Domain Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = cl_resp.json()["api_key"]
    session_id = uuid.uuid4()

    monkeypatch.setattr(
        "backend.chat.service.detect_injection",
        lambda *args, **kwargs: Mock(detected=False, level=None, method=None, score=None),
    )
    async def _fake_async_pipeline(*args, **kwargs):
        return _make_pipeline_result(
            final_answer="Which domain provider are you trying to configure?",
            reliability_score="medium",
        )

    monkeypatch.setattr(
        "backend.chat.service.async_run_chat_pipeline",
        _fake_async_pipeline,
    )

    outcome = process_chat_message(
        tenant_id,
        "How to connect domain?",
        session_id,
        db_session,
        api_key=api_key,
    )

    assert outcome.text == "Which domain provider are you trying to configure?"
    assert outcome.tokens_used == 3

def test_process_chat_message_passes_kyc_locale_fallback_before_language_signal(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Tenant

    token = register_and_verify_user(tenant, db_session, email="locale-fallback@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Locale Fallback Tenant"},
    )
    set_client_openai_key(tenant, token)
    client_row = db_session.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None

    captured_kwargs: dict[str, object] = {}

    def fake_localize_text_to_question_language_result(**kwargs: object) -> LocalizationResult:
        captured_kwargs.update(kwargs)
        return LocalizationResult(text="Bonjour", tokens_used=4)

    monkeypatch.setattr(
        "backend.chat.handlers.greeting.generate_greeting_in_language_result",
        fake_localize_text_to_question_language_result,
    )

    outcome = process_chat_message(
        client_row.id,
        "",
        uuid.uuid4(),
        db_session,
        api_key=cl_resp.json()["api_key"],
        user_context={"locale": "fr-FR"},
        browser_locale="de-DE",
    )

    assert outcome.text == "Bonjour"
    assert outcome.tokens_used == 4
    assert captured_kwargs["target_language"] == "fr-FR"


def test_complete_escalation_openai_turn_localizes_fallback_to_question_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_openai_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.localize_text_to_language_result",
        lambda **kwargs: __import__("backend.chat.language", fromlist=["LocalizationResult"]).LocalizationResult(
            text="Nous n'avons pas pu charger une reponse complete pour le moment.",
            tokens_used=17,
        ),
    )

    result = complete_escalation_openai_turn(
        phase=__import__("backend.models", fromlist=["EscalationPhase"]).EscalationPhase.handoff_email_known,
        chat_messages=[],
        fact_json={"ticket_number": "ESC-1234"},
        latest_user_text="J'ai besoin d'aide",
        api_key="sk-test",
    )

    assert result.message_to_user.startswith(
        "Nous n'avons pas pu charger une reponse complete pour le moment."
    )
    assert result.tokens_used == 17


def _make_retrieval_context(*, reliability_score: str = "medium") -> RetrievalContext:
    top_score = {"high": 0.9, "medium": 0.6, "low": 0.3}[reliability_score]
    result_count = {"high": 3, "medium": 3, "low": 1}[reliability_score]
    return RetrievalContext(
        chunk_texts=["retrieved docs"],
        document_ids=[uuid.uuid4()],
        scores=[top_score],
        mode="vector",
        best_rank_score=top_score,
        best_confidence_score=top_score,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=top_score, result_count=result_count),
        vector_similarities=[top_score],
    )


def _make_pipeline_result(
    *,
    final_answer: str,
    reliability_score: str = "medium",
    is_reject: bool = False,
    reject_reason: str | None = None,
) -> ChatPipelineResult:
    retrieval = None if is_reject and reject_reason == "not_relevant" else _make_retrieval_context(
        reliability_score=reliability_score
    )
    return ChatPipelineResult(
        raw_answer=final_answer,
        final_answer=final_answer,
        tokens_used=3,
        strategy="guard_reject" if is_reject else "rag_only",
        reject_reason=reject_reason,  # type: ignore[arg-type]
        is_reject=is_reject,
        is_faq_direct=False,
        retrieval=retrieval,
        escalation_recommended=False,
        escalation_trigger=None,
    )
