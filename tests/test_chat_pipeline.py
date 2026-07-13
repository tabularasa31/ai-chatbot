"""Tests for chat pipeline orchestration: process_chat_message."""

from __future__ import annotations

import uuid

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
from tests._async_utils import as_async as _as_async, as_async_generate
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
    async def _boom_escalation(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "backend.chat.service.complete_escalation_openai_turn",
        _boom_escalation,
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
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(lambda *args, **kwargs: ("Use the reset link in settings.", 17)),
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


def test_trace_metadata_language_confidence_and_response_language_across_turns(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for ClickUp 86exmtu8h — confidence=0 on follow-up traces.

    Two guarantees, checked across a 3-turn chat:

    * ``response_language`` is present in the trace metadata of **every** turn,
      not just the first.
    * Language-detection confidence is recorded under the unambiguous
      ``language_confidence`` key (the bare ``confidence`` key collided with the
      RAG handler's retrieval ``best_confidence_score``). On follow-up turns the
      chat is language-locked and detection is skipped, so the key is **omitted**
      rather than written as a false-negative ``0.0``.
    """
    from backend.models import Tenant

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

        @property
        def merged_metadata(self) -> dict:
            # Effective server-merged view: every update(metadata=...) this turn
            # layered onto one dict, later keys winning — mirrors how Langfuse
            # merges trace metadata across the pre-dispatch and handler writes.
            merged: dict = {}
            for call in self.update_calls:
                md = call.get("metadata")
                if isinstance(md, dict):
                    merged.update(md)
            return merged

    token = register_and_verify_user(
        tenant, db_session, email="trace-lang-conf@example.com"
    )
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace Lang Conf Tenant"},
    )
    set_client_openai_key(tenant, token)
    client_row = db_session.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None

    traces: list[FakeTrace] = []

    def _begin_trace(**kwargs: object) -> FakeTrace:
        trace = FakeTrace()
        traces.append(trace)
        return trace

    monkeypatch.setattr("backend.chat.service.begin_trace", _begin_trace)
    monkeypatch.setattr(
        "backend.chat.service.async_retrieve_context",
        _as_async(
            lambda *args, **kwargs: RetrievalContext(
                chunk_texts=["Чтобы сбросить пароль, откройте настройки аккаунта."],
                document_ids=[uuid.uuid4()],
                scores=[0.9],
                mode="hybrid",
                best_rank_score=0.9,
                best_confidence_score=0.88,
                confidence_source="vector_similarity",
                reliability=build_reliability_assessment(top_score=0.9, result_count=5),
            )
        ),
    )
    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer",
        as_async_generate(
            lambda *args, **kwargs: ("Откройте настройки и сбросьте пароль.", 12)
        ),
    )
    monkeypatch.setattr(
        "backend.chat.service.should_escalate",
        lambda *args, **kwargs: (False, None),
    )

    # A single session drives all three turns so the chat's language lock (set on
    # the first reliable Russian turn) carries into the follow-ups.
    session_id = uuid.uuid4()
    questions = [
        "Как мне сбросить пароль от моего аккаунта?",
        "А если я не помню электронную почту?",
        "Сколько времени занимает восстановление доступа?",
    ]
    for question in questions:
        process_chat_message(
            client_row.id,
            question,
            session_id,
            db_session,
            api_key=cl_resp.json()["api_key"],
        )

    assert len(traces) == 3
    metadatas = [trace.merged_metadata for trace in traces]

    for md in metadatas:
        # AC2: response_language present on every turn.
        assert md.get("response_language") == "ru"
        # The bare "confidence" key must never reappear at trace level.
        assert "confidence" not in md
        # Retrieval confidence keeps its own distinct key.
        assert md.get("best_confidence_score") == 0.88
        # AC1: language_confidence, when present, is a real measurement — never
        # the false-negative sentinel 0.0.
        if "language_confidence" in md:
            assert md["language_confidence"] > 0.0

    # Turn 1 runs detection → language_confidence recorded.
    assert metadatas[0].get("language_confidence", 0.0) > 0.0
    assert metadatas[0].get("language_is_reliable") is True

    # Follow-up turns are language-locked → detection skipped → the confidence
    # keys are omitted rather than written as 0.0.
    assert "language_confidence" not in metadatas[1]
    assert "language_is_reliable" not in metadatas[1]
    assert "language_confidence" not in metadatas[2]
    assert "language_is_reliable" not in metadatas[2]


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

    async def fake_generate_greeting_in_language_result(**kwargs: object) -> LocalizationResult:
        captured_kwargs.update(kwargs)
        return LocalizationResult(text="Bonjour", tokens_used=4)

    monkeypatch.setattr(
        "backend.chat.handlers.greeting.generate_greeting_in_language_result",
        fake_generate_greeting_in_language_result,
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


@pytest.mark.asyncio
async def test_complete_escalation_openai_turn_localizes_fallback_to_question_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.escalation.openai_escalation.get_async_openai_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    async def _fake_localize(**kwargs: object) -> LocalizationResult:
        return LocalizationResult(
            text="Nous n'avons pas pu charger une reponse complete pour le moment.",
            tokens_used=17,
        )

    monkeypatch.setattr(
        "backend.escalation.openai_escalation.async_localize_text_to_language_result",
        _fake_localize,
    )

    result = await complete_escalation_openai_turn(
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
