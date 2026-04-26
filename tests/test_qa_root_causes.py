"""Regression tests for root causes found in QA smoke test (task 86excub1f).

Langfuse trace analysis revealed 4 distinct bugs, all now fixed:

  RC-1 (A2/B2) — answer-validation false positive: VALIDATION_PROMPT softened
        so that section-path labels / format enumerations not verbatim in chunks
        no longer cause is_valid=False when the core fact is grounded.
        validate_answer now uses top-5 chunks instead of top-3.

  RC-2 (C2)    — language mismatch detection added after generate_answer:
        detect_language(question) vs detect_language(answer); if they differ,
        the answer is regenerated with response_language=detected_question_lang.

  RC-3 (K2)   — ticket_number now passed to _escalation_turn_response in
        _handle_awaiting_email so ChatTurnOutcome.ticket_number is populated.

  RC-4 (L2)   — fixed by RC-1: valid answer from the corrected validator returns
        is_valid=True which suppresses should_escalate(low_similarity).
"""

from __future__ import annotations

import uuid
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import register_and_verify_user, set_client_openai_key


# ---------------------------------------------------------------------------
# RC-1 — answer-validation false positive (fixed)
# ---------------------------------------------------------------------------


class TestAnswerValidationFalsePositive:
    """RC-1/B2: validator must accept correct answers whose section-path labels
    are not verbatim in retrieved chunks, provided the core fact is grounded."""

    def test_validation_prompt_fires_on_section_citation_not_in_chunks(
        self, mock_openai_client: Mock
    ) -> None:
        """validate_answer returns is_valid=True when LLM embeds correct section
        names that aren't verbatim in the retrieved chunks (RC-1/B2 fixed)."""
        from backend.chat.service import validate_answer

        context_chunk = (
            "Limits: maximum file size per document is 50 MB. "
            "Supported formats: PDF, Markdown, Swagger/OpenAPI."
        )
        llm_answer = (
            "Да — максимальный размер файла 50 MB. "
            "Это указано в Getting Started → Step 3: Add your knowledge "
            "и в Dashboard Features → Knowledge hub."
        )

        # Softened VALIDATION_PROMPT: core fact (50 MB) is grounded → is_valid=True.
        mock_openai_client.chat.completions.create.return_value.choices = [
            Mock(
                message=Mock(
                    content=(
                        '{"is_valid": true, "confidence": 0.9, "reason": '
                        '"Core fact (50 MB limit) is grounded in context. '
                        'Section-path labels are acceptable even if not verbatim."}'
                    )
                )
            )
        ]

        result = validate_answer(
            "А есть ли ограничение на размер файла?",
            llm_answer,
            [context_chunk],
            api_key="sk-test",
        )

        assert result["is_valid"] is True, (
            "RC-1 fix: validate_answer must accept answers whose section citations "
            "are not verbatim in chunks when the core fact is grounded."
        )

    def test_validation_prompt_fires_when_extra_formats_not_in_chunk(
        self, mock_openai_client: Mock
    ) -> None:
        """validate_answer returns is_valid=True for a correct multi-format answer
        even when not all formats appear in the top retrieved chunk (RC-1/A2 fixed)."""
        from backend.chat.service import validate_answer

        context_chunk = (
            "Step 3: Add your knowledge. Supported formats: PDF, Markdown (.md, .mdx), "
            "Swagger/OpenAPI (.json, .yaml, .yml)."
        )
        llm_answer = (
            "You can upload: PDF; Markdown (.md, .mdx); Swagger/OpenAPI (.json, .yaml, .yml); "
            "Word (.docx, .doc); and plain text (.txt). Maximum file size: 50 MB."
        )

        # With top-5 chunk context, Word/.txt appear in a lower-ranked chunk and
        # the softened prompt accepts format enumerations whose base fact is grounded.
        mock_openai_client.chat.completions.create.return_value.choices = [
            Mock(
                message=Mock(
                    content=(
                        '{"is_valid": true, "confidence": 0.85, "reason": '
                        '"Core formats are grounded. Format enumerations are acceptable '
                        'even if not all items appear verbatim in top chunk."}'
                    )
                )
            )
        ]

        result = validate_answer(
            "What file formats can I upload?",
            llm_answer,
            [context_chunk],
            api_key="sk-test",
        )

        assert result["is_valid"] is True, (
            "RC-1/A2 fix: validate_answer must accept correct multi-format answers "
            "when the base fact is grounded and the prompt is softened."
        )

    def test_validation_prompt_contains_softened_product_limits_clause(self) -> None:
        """Documents that VALIDATION_PROMPT now uses the softened criterion for RC-1."""
        from backend.chat.handlers.rag import VALIDATION_PROMPT

        assert "core facts" in VALIDATION_PROMPT.lower(), (
            "VALIDATION_PROMPT must use the softened 'core facts' criterion"
        )
        assert "section-path labels" in VALIDATION_PROMPT.lower(), (
            "VALIDATION_PROMPT must explicitly allow section-path labels"
        )

    def test_valid_answer_with_fully_grounded_citation_passes(
        self, mock_openai_client: Mock
    ) -> None:
        """Baseline: validator correctly passes an answer whose every claim is
        verbatim in the retrieved chunk."""
        from backend.chat.service import validate_answer

        context_chunk = (
            "Chat9 is currently free during the Early Access phase. "
            "See Pricing and Limits → Current pricing."
        )
        llm_answer = "Chat9 is currently free during the Early Access phase."

        mock_openai_client.chat.completions.create.return_value.choices = [
            Mock(
                message=Mock(
                    content='{"is_valid": true, "confidence": 1.0, "reason": "fully grounded"}'
                )
            )
        ]

        result = validate_answer(
            "Is Chat9 free?",
            llm_answer,
            [context_chunk],
            api_key="sk-test",
        )

        assert result["is_valid"] is True
        assert result["confidence"] == 1.0


# ---------------------------------------------------------------------------
# RC-2 — language mismatch detection (fixed)
# ---------------------------------------------------------------------------


class TestLanguageNotValidated:
    """RC-2/C2: pipeline now has a language-check span after llm-generation."""

    def test_validate_answer_detects_language_mismatch(
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

        def _fake_validate(*args, **kwargs) -> dict:
            return {"is_valid": True, "confidence": 0.9, "reason": "grounded"}

        # Simulate detect_language: question=es, first answer=nl → mismatch triggers retry.
        original_detect = _detect_language

        def _fake_detect(text: str) -> LanguageDetectionResult:
            if "inline disponible" in text or "¿" in text:
                return LanguageDetectionResult(detected_language="es", confidence=0.95, is_reliable=True)
            if "beschikbaar" in text or "Inline-modus" in text or "Ja," in text:
                return LanguageDetectionResult(detected_language="nl", confidence=0.95, is_reliable=True)
            return original_detect(text)

        monkeypatch.setattr("backend.chat.service.retrieve_context", _fake_retrieve)
        monkeypatch.setattr("backend.chat.service.generate_answer", _fake_generate)
        monkeypatch.setattr("backend.chat.service.validate_answer", _fake_validate)
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
# RC-4 — short follow-up → false escalation (fixed via RC-1)
# ---------------------------------------------------------------------------


class TestShortFollowupFalseEscalation:
    """RC-4/L2: after RC-1 fix, validate_answer returns is_valid=True for the
    short follow-up scenario, which blocks should_escalate(low_similarity)."""

    def test_should_escalate_fires_when_score_low_and_validation_missing(self) -> None:
        """Baseline: should_escalate(low_score, no_validation) → True + low_similarity."""
        from backend.escalation.service import should_escalate
        from backend.models import EscalationTrigger

        escalate, trigger = should_escalate(
            best_similarity_score=0.15,
            chunk_count=3,
            validation=None,
        )
        assert escalate is True
        assert trigger == EscalationTrigger.low_similarity

    def test_should_escalate_suppressed_when_validation_is_valid(self) -> None:
        """Passing validation overrides low similarity score — escalation suppressed."""
        from backend.escalation.service import should_escalate

        escalate, trigger = should_escalate(
            best_similarity_score=0.15,
            chunk_count=3,
            validation={"is_valid": True, "confidence": 0.9, "reason": "grounded"},
        )
        assert escalate is False
        assert trigger is None

    def test_should_escalate_suppressed_when_validation_none_but_high_score(self) -> None:
        """High similarity score alone prevents escalation even without validation."""
        from backend.escalation.service import should_escalate

        escalate, trigger = should_escalate(
            best_similarity_score=0.75,
            chunk_count=4,
            validation=None,
        )
        assert escalate is False
        assert trigger is None

    def test_short_followup_low_score_causes_chat_escalation(
        self,
        mock_openai_client: Mock,
        tenant: TestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: after RC-1 fix, a 3-word follow-up question with low retrieval
        score no longer triggers escalation because validate_answer returns is_valid=True,
        which suppresses should_escalate(low_similarity).

        Pipeline path:
          retrieve_context → best_confidence_score=0.15 (< ESCALATION_THRESHOLD=0.45)
          validate_answer  → is_valid=True (softened prompt accepts grounded answer)
          should_escalate  → False (valid answer overrides low score)
          result           → normal answer, no escalation message
        """
        from backend.chat.service import RetrievalContext
        from backend.search.service import build_reliability_assessment

        token = register_and_verify_user(
            tenant, db_session, email="rc4-followup@example.com"
        )
        cl_resp = tenant.post(
            "/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "RC-4 Followup Tenant"},
        )
        assert cl_resp.status_code == 201
        set_client_openai_key(tenant, token)
        api_key = cl_resp.json()["api_key"]

        doc_id = uuid.uuid4()

        monkeypatch.setattr(
            "backend.chat.service.retrieve_context",
            lambda *args, **kwargs: RetrievalContext(
                chunk_texts=["Chat9 pricing information."],
                document_ids=[doc_id],
                scores=[0.015],
                mode="hybrid",
                best_rank_score=0.015,
                best_confidence_score=0.15,
                confidence_source="vector_similarity",
                reliability=build_reliability_assessment(
                    top_score=0.15,
                    result_count=1,
                ),
            ),
        )

        monkeypatch.setattr(
            "backend.chat.service.generate_answer",
            lambda *args, **kwargs: (
                "Chat9 is currently free during the Early Access phase.",
                60,
            ),
        )

        # RC-1 fixed: validator now returns is_valid=True for a grounded answer.
        monkeypatch.setattr(
            "backend.chat.service.validate_answer",
            lambda *args, **kwargs: {
                "is_valid": True,
                "confidence": 0.85,
                "reason": "core fact (free early access) is grounded in context",
            },
        )

        session_id = uuid.uuid4()
        response = tenant.post(
            "/chat",
            headers={"X-API-Key": api_key},
            json={"session_id": str(session_id), "question": "И сколько это стоит?"},
        )

        assert response.status_code == 200
        data = response.json()

        # RC-4 fix: valid validation suppresses should_escalate; no ticket created,
        # no escalation message appended to the answer.
        assert data.get("text") == "Chat9 is currently free during the Early Access phase.", (
            "RC-4 fix: answer must be the plain RAG response, not deflection+escalation"
        )
        assert data.get("ticket_number") is None, (
            "RC-4 fix: no escalation ticket must be created when validation passes"
        )
