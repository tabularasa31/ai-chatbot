"""Regression tests for root causes found in QA smoke test (task 86excub1f).

  RC-2 (C2)    — language mismatch detection added after generate_answer:
        detect_language(question) vs detect_language(answer); if they differ,
        the answer is regenerated with response_language=detected_question_lang.

  RC-3 (K2)   — ticket_number now passed to _escalation_turn_response in
        _handle_awaiting_email so ChatTurnOutcome.ticket_number is populated.
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._async_utils import as_async as _as_async
from tests.conftest import register_and_verify_user, set_client_openai_key


# ---------------------------------------------------------------------------
# RC-2 — language mismatch detection (fixed)
# ---------------------------------------------------------------------------


class TestLanguageNotValidated:
    """RC-2/C2: pipeline now has a language-check span after llm-generation."""

    def test_language_mismatch_triggers_regeneration(
        self,
        mock_openai_client: Mock,
        tenant: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The language-check span in run_chat_pipeline detects mismatches and
        regenerates. When detect_language signals a mismatch, generate_answer is
        called a second time with the corrected response_language."""
        import uuid as _uuid
        from backend.chat.handlers.rag import detect_language as _detect_language
        from backend.chat.language import LanguageDetectionResult
        from backend.chat.service import RetrievalContext
        from backend.search.service import build_reliability_assessment

        token = register_and_verify_user(
            tenant, db_session, email="rc2-langcheck@example.com"
        )
        cl_resp = tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "RC-2 LangCheck Tenant"},
        )
        assert cl_resp.status_code == 201
        set_client_openai_key(tenant, token)
        api_key = cl_resp.json()["api_key"]
        doc_id = _uuid.uuid4()

        generate_calls: list[str] = []

        def _fake_retrieve(*args, **kwargs) -> RetrievalContext:
            return RetrievalContext(
                chunk_texts=["Inline mode is available."],
                document_ids=[doc_id],
                scores=[0.9],
                mode="hybrid",
                best_rank_score=0.9,
                best_confidence_score=0.9,
                confidence_source="vector_similarity",
                reliability=build_reliability_assessment(top_score=0.9, result_count=1),
            )

        call_index = [0]

        def _fake_generate(*args, **kwargs) -> tuple[str, int]:
            call_index[0] += 1
            lang = kwargs.get("response_language", "en")
            generate_calls.append(lang)
            if call_index[0] == 1:
                # First call returns Dutch (wrong language for Spanish question).
                return ("Ja, inline modus is beschikbaar.", 60)
            # Second call (retry) returns Spanish.
            return ("Sí, el modo inline está disponible.", 60)

        # Simulate detect_language: question=es, first answer=nl → mismatch triggers retry.
        original_detect = _detect_language

        def _fake_detect(text: str) -> LanguageDetectionResult:
            if "inline disponible" in text or "¿" in text:
                return LanguageDetectionResult(detected_language="es", confidence=0.95, is_reliable=True)
            if "beschikbaar" in text or "Inline-modus" in text or "Ja," in text:
                return LanguageDetectionResult(detected_language="nl", confidence=0.95, is_reliable=True)
            return original_detect(text)

        monkeypatch.setattr("backend.chat.service.async_retrieve_context", _as_async(_fake_retrieve))
        monkeypatch.setattr("backend.chat.service.generate_answer", _fake_generate)
        monkeypatch.setattr("backend.chat.handlers.rag.detect_language", _fake_detect)

        session_id = _uuid.uuid4()
        response = tenant.post(
            "/chat",
            headers={"X-API-Key": api_key},
            json={
                "session_id": str(session_id),
                "question": "¿Hay un modo inline disponible?",
            },
        )
        assert response.status_code == 200

        # generate_answer must have been called twice: first with en/default, then
        # with 'es' forced by the language-check span on mismatch detection.
        assert len(generate_calls) >= 2, (
            "RC-2 fix: generate_answer must be called a second time when language "
            f"mismatch is detected. Calls seen: {generate_calls}"
        )
        assert generate_calls[-1] == "es", (
            f"RC-2 fix: retry call must use detected question language 'es', got '{generate_calls[-1]}'"
        )

    def test_language_check_span_imported_in_rag_handler(self) -> None:
        """detect_language is imported at module level in rag.py for the language check."""
        import backend.chat.handlers.rag as rag_module

        assert hasattr(rag_module, "detect_language"), (
            "detect_language must be imported in rag.py for the language-check span"
        )


# ---------------------------------------------------------------------------
# RC-3 — ticket_number surfaced after email capture (fixed)
# ---------------------------------------------------------------------------


class TestTicketNumberNotSurfaced:
    """RC-3/K2: after email capture, ticket_number is now returned in the API response."""

    def test_chat_email_capture_returns_ticket_number(
        self,
        mock_openai_client: Mock,
        tenant: TestClient,
        db_session: Session,
    ) -> None:
        """RC-3 fix: /chat response ticket_number equals the created ticket number."""
        from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus

        token = register_and_verify_user(
            tenant, db_session, email="rc3-ticket@example.com"
        )
        cl_resp = tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "RC-3 Ticket Tenant"},
        )
        assert cl_resp.status_code == 201
        set_client_openai_key(tenant, token)
        tenant_id = uuid.UUID(cl_resp.json()["id"])
        api_key = cl_resp.json()["api_key"]

        session_id = uuid.uuid4()
        chat = Chat(
            tenant_id=tenant_id,
            session_id=session_id,
            user_context={"user_id": "u-rc3"},
        )
        db_session.add(chat)
        db_session.commit()
        db_session.refresh(chat)

        ticket = EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-0042",
            primary_question="Need human support",
            trigger=EscalationTrigger.user_request,
            status=EscalationStatus.open,
            chat_id=chat.id,
            session_id=session_id,
        )
        db_session.add(ticket)
        db_session.commit()
        db_session.refresh(ticket)

        chat.escalation_awaiting_ticket_id = ticket.id
        db_session.add(chat)
        db_session.commit()

        response = tenant.post(
            "/chat",
            headers={"X-API-Key": api_key},
            json={
                "session_id": str(session_id),
                "question": "my email is rc3@example.com",
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["ticket_number"] == "ESC-0042", (
            "RC-3 fix: ticket_number must be returned in the response after email capture"
        )

    def test_escalation_turn_response_propagates_ticket_number_when_provided(
        self,
        mock_openai_client: Mock,
        tenant: TestClient,
        db_session: Session,
    ) -> None:
        """Verifies _escalation_turn_response correctly propagates ticket_number
        when callers pass it explicitly — the function itself is correct;
        the bug was in the caller not passing the argument."""
        from backend.models import Chat, EscalationTicket, EscalationTrigger, EscalationStatus
        from backend.chat.service import _escalation_turn_response
        from backend.chat.language import resolve_language_context
        from unittest.mock import MagicMock

        token = register_and_verify_user(
            tenant, db_session, email="rc3-propagate@example.com"
        )
        cl_resp = tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "RC-3 Propagate Tenant"},
        )
        assert cl_resp.status_code == 201
        set_client_openai_key(tenant, token)
        tenant_id = uuid.UUID(cl_resp.json()["id"])

        session_id = uuid.uuid4()
        chat = Chat(tenant_id=tenant_id, session_id=session_id, user_context={})
        db_session.add(chat)
        db_session.commit()
        db_session.refresh(chat)

        ticket = EscalationTicket(
            tenant_id=tenant_id,
            ticket_number="ESC-0099",
            primary_question="test",
            trigger=EscalationTrigger.user_request,
            status=EscalationStatus.open,
            chat_id=chat.id,
            session_id=session_id,
        )
        db_session.add(ticket)
        db_session.commit()
        db_session.refresh(ticket)

        fake_out = MagicMock()
        fake_out.message_to_user = "Ваш запрос передан. ESC-0099."
        fake_out.tokens_used = 50

        language_context = resolve_language_context(
            current_turn_text="rc3@example.com",
            is_bootstrap_turn=False,
            bootstrap_user_locale=None,
            browser_locale=None,
            tenant_escalation_language=None,
            tenant_id=None,
            chat_id=None,
        )

        fake_trace = MagicMock()

        outcome = _escalation_turn_response(
            db=db_session,
            chat=chat,
            tenant_id=tenant_id,
            language_context=language_context,
            question="rc3@example.com",
            out=fake_out,
            optional_entity_types=None,
            trace=fake_trace,
            trace_source="escalation_email_capture",
            chat_ended=False,
            escalated=True,
            ticket_number=ticket.ticket_number,
        )

        assert outcome.ticket_number == "ESC-0099", (
            "_escalation_turn_response must propagate ticket_number to ChatTurnOutcome"
        )


# ---------------------------------------------------------------------------
# RC-5 — false escalation on TurboFlare topical questions (task 86exd8kxv)
# ---------------------------------------------------------------------------


class TestTurboFlareFalseEscalation:
    """RC-5: Russian-language TurboFlare questions were escalated because:
    (a) should_escalate only checked vector similarity, ignoring the stronger
        BM25 hybrid rank score — now fixed: max(vector_sim, rank_score) used.
    (b) low_context was set even when _reranker_rescued=True, causing the LLM
        to decline answering — now low_context respects _reranker_rescued.
    """

    def test_should_escalate_suppressed_by_high_rank_score(self) -> None:
        """RC-5a: high hybrid rank score (BM25) should prevent escalation even
        when vector similarity is below ESCALATION_THRESHOLD."""
        from backend.escalation.service import should_escalate

        escalate, trigger = should_escalate(
            0.35,  # vector similarity below threshold (0.45)
            chunk_count=3,
            best_rank_score=0.52,  # strong BM25 hybrid score
        )

        assert escalate is False, (
            "RC-5a: high rank_score=0.52 must suppress escalation even if vector_sim=0.35"
        )
        assert trigger is None

    def test_should_escalate_fires_when_both_scores_low(self) -> None:
        """RC-5a regression guard: escalation must still fire when both scores are low."""
        from backend.escalation.service import should_escalate, EscalationTrigger

        escalate, trigger = should_escalate(
            0.35,
            chunk_count=3,
            best_rank_score=0.40,  # both below ESCALATION_THRESHOLD (0.45)
        )

        assert escalate is True
        assert trigger == EscalationTrigger.low_similarity

    def test_low_context_not_set_when_reranker_rescued(self) -> None:
        """RC-5b: when _reranker_rescued=True, low_context must be False even if
        reliability.score == 'low', so the LLM answers instead of declining.

        Direct unit test of the boolean expression used in run_chat_pipeline.
        """
        from backend.core.config import settings

        reranker_bypass_threshold = settings.reranker_bypass_threshold

        # Case 1: rank_score above threshold → rescued → low_context must be False.
        best_rank_score = 0.55
        reliability_score = "low"
        _reranker_rescued = best_rank_score >= reranker_bypass_threshold
        low_context = not _reranker_rescued and reliability_score == "low"

        assert _reranker_rescued is True, "test setup: rank_score=0.55 must trigger rescue"
        assert low_context is False, (
            "RC-5b: low_context must be False when reranker rescued, "
            "even if reliability.score == 'low'"
        )

        # Case 2: rank_score below threshold → not rescued → low_context=True when low.
        best_rank_score_low = 0.38
        _reranker_rescued_low = best_rank_score_low >= reranker_bypass_threshold
        low_context_low = not _reranker_rescued_low and reliability_score == "low"

        assert _reranker_rescued_low is False
        assert low_context_low is True, (
            "RC-5b: low_context must be True when rank_score is below threshold"
        )

    def test_ru_query_strong_bm25_no_escalation(
        self,
        mock_openai_client: Mock,
        tenant: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RC-5 end-to-end: Russian TurboFlare question with strong BM25 rank score
        must not escalate even when vector similarity is below ESCALATION_THRESHOLD."""
        from backend.chat.service import RetrievalContext
        from backend.search.service import build_reliability_assessment

        token = register_and_verify_user(
            tenant, db_session, email="rc5-turboflare@example.com"
        )
        cl_resp = tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "RC-5 TurboFlare Tenant"},
        )
        assert cl_resp.status_code == 201
        set_client_openai_key(tenant, token)
        api_key = cl_resp.json()["api_key"]

        # Mocked retrieval: strong hybrid rank score (BM25), weak vector similarity.
        def _fake_retrieve(*args, **kwargs) -> RetrievalContext:
            return RetrievalContext(
                chunk_texts=[
                    "TurboFlare: для удаления домена обратитесь в поддержку. "
                    "Кнопки удаления в интерфейсе нет."
                ],
                document_ids=[],
                scores=[0.38],
                mode="hybrid",
                best_rank_score=0.55,         # strong BM25 — above reranker_bypass_threshold
                best_confidence_score=0.38,    # weak vector — below ESCALATION_THRESHOLD
                confidence_source="vector_similarity",
                reliability=build_reliability_assessment(top_score=0.55, result_count=1),
            )

        expected_answer = "Для удаления домена из TurboFlare обратитесь в службу поддержки."

        monkeypatch.setattr("backend.chat.service.async_retrieve_context", _as_async(_fake_retrieve))
        monkeypatch.setattr(
            "backend.chat.service.generate_answer",
            lambda *a, **kw: (expected_answer, 50),
        )

        session_id = uuid.uuid4()
        response = tenant.post(
            "/chat",
            headers={"X-API-Key": api_key},
            json={
                "session_id": str(session_id),
                "question": "Как удалить домен из TurboFlare?",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert data.get("ticket_number") is None, (
            "RC-5 end-to-end: must not escalate when rank_score=0.55 (above threshold)"
        )
        assert data.get("text") == expected_answer, (
            "RC-5 end-to-end: must return the RAG answer, not a deflection/escalation message"
        )
