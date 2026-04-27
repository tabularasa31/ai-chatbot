"""Unit tests for RAG prompt building, answer generation, and validation."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.handlers import rag as rag_handler
from backend.chat.language import LocalizationResult
from backend.chat.service import (
    _quick_answer_keys_for_question,
    _quick_answers_context,
    build_rag_messages,
    build_rag_prompt,
    generate_answer,
    validate_answer,
)
from backend.core.config import settings
from backend.models import QuickAnswer, SourceSchedule, SourceStatus, UrlSource
from tests.conftest import register_and_verify_user


def test_build_rag_prompt() -> None:
    """build_rag_prompt produces correct format with chunks."""
    chunks = ["chunk1", "chunk2", "chunk3"]
    result = build_rag_prompt("What is X?", chunks)
    assert "Hard limits" in result
    assert "[Response level: standard]" in result
    assert "technical support agent" in result
    assert "Answer using ONLY the provided context" in result
    assert "Treat the provided context as the source of truth" in result
    assert "ask exactly one short clarifying question instead of guessing" in result
    assert "chunk1" in result
    assert "chunk2" in result
    assert "chunk3" in result
    assert "---" in result
    assert "Question: What is X?" in result
    assert "Answer:" in result


def test_build_rag_prompt_empty_chunks() -> None:
    """build_rag_prompt handles empty chunks."""
    result = build_rag_prompt("Q?", [])
    assert "Question: Q?" in result
    assert "(none)" in result
    assert "[Response level: standard]" in result


def test_build_rag_messages_splits_system_and_user_parts() -> None:
    system_prompt, user_message = build_rag_messages("What is X?", ["chunk1", "chunk2"])
    assert "Hard limits" in system_prompt
    assert "Context:" not in system_prompt
    assert "chunk1" in user_message
    assert "chunk2" in user_message
    assert "Question: What is X?" in user_message


def test_generate_answer_no_context(mock_openai_client: Mock) -> None:
    """Empty chunks → canonical fallback, no OpenAI call."""
    answer, tokens = generate_answer("question", [], api_key="sk-test")
    assert answer == "I don't have information about this."
    assert tokens == 0
    mock_openai_client.chat.completions.create.assert_not_called()


def test_generate_answer_allows_quick_answers_without_retrieval_chunks(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="Documentation: https://docs.example.com/"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=42)

    answer, tokens = generate_answer(
        "Where is the documentation?",
        [],
        api_key="sk-test",
        quick_answer_items=["Documentation: https://docs.example.com/"],
    )

    assert answer == "Documentation: https://docs.example.com/"
    assert tokens == 42
    mock_openai_client.chat.completions.create.assert_called_once()


def test_validate_answer_no_context(mock_openai_client: Mock) -> None:
    """Empty context → invalid + no_context; no OpenAI call."""
    result = validate_answer("q", "a", [], api_key="sk-test")
    assert result == {"is_valid": False, "confidence": 0.0, "reason": "no_context"}
    mock_openai_client.chat.completions.create.assert_not_called()


def test_quick_answers_context_returns_structured_lines(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="quick-answer-docs@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Quick Answer Docs"},
    )
    tenant_id = uuid.UUID(create_resp.json()["id"])
    source = UrlSource(
        tenant_id=tenant_id,
        name="Docs",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add(source)
    db_session.flush()
    db_session.add(
        QuickAnswer(
            tenant_id=tenant_id,
            source_id=source.id,
            key="documentation_url",
            value="https://docs.example.com/",
            source_url="https://docs.example.com/",
            metadata_json={"method": "source_url"},
        )
    )
    db_session.add(
        QuickAnswer(
            tenant_id=tenant_id,
            source_id=source.id,
            key="support_email",
            value="help@example.com",
            source_url="https://docs.example.com/contact",
            metadata_json={"method": "mailto"},
        )
    )
    db_session.commit()

    answer = _quick_answers_context(tenant_id, "Where is your documentation?", db_session)

    assert answer == ["Documentation: https://docs.example.com/"]


def test_quick_answer_keys_for_question_filters_by_topic() -> None:
    assert _quick_answer_keys_for_question("Where can I find pricing and trial details?") == [
        "pricing_url",
        "trial_info",
    ]
    assert _quick_answer_keys_for_question("How can I contact support?") == [
        "support_email",
        "support_chat",
        "status_page_url",
    ]
    assert _quick_answer_keys_for_question("Show me the documentation") == [
        "documentation_url",
    ]


def test_quick_answers_context_prefers_higher_quality_documentation_source_over_newer_fallback(
    tenant: TestClient,
    db_session: Session,
) -> None:
    token = register_and_verify_user(tenant, db_session, email="quick-answer-quality@example.com")
    create_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Quick Answer Quality"},
    )
    tenant_id = uuid.UUID(create_resp.json()["id"])
    docs_source = UrlSource(
        tenant_id=tenant_id,
        name="Documentation",
        url="https://docs.example.com/",
        normalized_domain="docs.example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    blog_source = UrlSource(
        tenant_id=tenant_id,
        name="Blog",
        url="https://example.com/blog/start",
        normalized_domain="example.com",
        status=SourceStatus.ready,
        crawl_schedule=SourceSchedule.manual,
        pages_indexed=0,
        chunks_created=0,
        tokens_used=0,
        metadata_json={},
    )
    db_session.add_all([docs_source, blog_source])
    db_session.flush()
    db_session.add(
        QuickAnswer(
            tenant_id=tenant_id,
            source_id=docs_source.id,
            key="documentation_url",
            value="https://docs.example.com/guide",
            source_url="https://docs.example.com/guide",
            metadata_json={"method": "anchor"},
        )
    )
    db_session.add(
        QuickAnswer(
            tenant_id=tenant_id,
            source_id=blog_source.id,
            key="documentation_url",
            value="https://example.com/blog/start",
            source_url="https://example.com/blog/start",
            metadata_json={"method": "source_url"},
        )
    )
    db_session.commit()

    answer = _quick_answers_context(tenant_id, "Where is the documentation?", db_session)

    assert answer == ["Documentation: https://docs.example.com/guide"]


def test_build_rag_prompt_includes_structured_quick_answers() -> None:
    prompt = build_rag_prompt(
        "Where is the documentation?",
        ["Chunk about setup."],
        quick_answer_items=["Documentation: https://docs.example.com/"],
    )

    assert "STRUCTURED QUICK ANSWERS" in prompt
    assert "Documentation: https://docs.example.com/" in prompt

def test_build_rag_prompt_requires_exact_setting_names_from_docs() -> None:
    prompt = build_rag_prompt(
        "Which setting should I use?",
        ["Use the setting named API Base URL in the Connection section."],
    )

    assert "name the exact setting or field as written in the documentation" in prompt


def test_build_rag_prompt_prefers_quick_answers_for_short_facts() -> None:
    prompt = build_rag_prompt(
        "Where can I find pricing?",
        ["Pricing details are available in the docs."],
        quick_answer_items=["Pricing: https://example.com/pricing"],
    )

    assert "prefer STRUCTURED QUICK ANSWERS when relevant" in prompt
    assert "Pricing: https://example.com/pricing" in prompt


def test_build_rag_prompt_disallows_saying_unknown_when_context_has_answer() -> None:
    prompt = build_rag_prompt(
        "How do I reset my password?",
        ["Go to Settings > Security and click Reset password."],
    )

    assert "Do not say you do not know when relevant evidence is present" in prompt


def test_build_rag_prompt_handles_conflicting_sources_conservatively() -> None:
    prompt = build_rag_prompt(
        "What is the file limit?",
        ["The file limit is 10 MB.", "The file limit is 20 MB."],
    )

    assert "If sources in the provided context appear inconsistent" in prompt
    assert "answer conservatively from the clearest supported part only" in prompt


def test_validate_answer_openai_error_returns_invalid(
    mock_openai_client: Mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OpenAI/JSON errors are logged and treated as validation failures."""
    mock_openai_client.chat.completions.create.side_effect = RuntimeError("boom")
    with caplog.at_level("ERROR", logger="backend.chat.handlers.rag"):
        result = validate_answer("q", "a", ["chunk"], api_key="sk-test")
    assert result["is_valid"] is False
    assert result["confidence"] == 0.0
    assert result["reason"] == "validation_error"
    assert "Answer validation failed" in caplog.text


def test_validate_answer_prompt_allows_single_clarifying_question(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content='{"is_valid": true, "confidence": 0.9, "reason": "clarifying_question_allowed"}'))
    ]

    result = validate_answer(
        "How do I connect this?",
        "Which integration are you trying to connect?",
        ["Integration setup depends on the integration type."],
        api_key="sk-test",
    )

    assert result["is_valid"] is True
    prompt = mock_openai_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "asks exactly one short clarifying question" in prompt
    assert "materially blocks a correct answer" in prompt
    assert "unsupported core facts" in prompt
    assert "section-path labels" in prompt


def test_generate_answer_with_context(mock_openai_client: Mock) -> None:
    """With chunks, calls OpenAI and returns answer + tokens."""
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)

    answer, tokens = generate_answer("What?", ["chunk1"], api_key="sk-test")
    assert answer == "The answer is 42"
    assert tokens == 100
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == settings.chat_model
    assert call_kwargs["messages"][0]["role"] == "system"
    assert call_kwargs["messages"][1]["role"] == "user"
    # gpt-5-mini is a reasoning model — temperature is omitted, larger token budget used
    assert "temperature" not in call_kwargs
    assert call_kwargs["max_completion_tokens"] == settings.chat_response_max_tokens_reasoning


def test_generate_answer_traces_summary_not_full_prompt(mock_openai_client: Mock) -> None:
    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_input: object | None = None
            self.generation_handle = FakeGeneration()

        def generation(self, **kwargs: object) -> FakeGeneration:
            self.generation_input = kwargs["input"]
            return self.generation_handle

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)
    trace = FakeTrace()
    from backend.chat import service as chat_service

    assert chat_service.settings.observability_capture_full_prompts is False

    generate_answer("What?", ["secret internal KB chunk"], api_key="sk-test", trace=trace)

    assert trace.generation_input == {
        "question_preview": "What?",
        "context_chunk_count": 1,
        "quick_answer_count": 0,
    }


def test_generate_answer_can_trace_full_prompt_when_enabled(
    mock_openai_client: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_input: object | None = None
            self.generation_metadata: object | None = None
            self.generation_handle = FakeGeneration()

        def generation(self, **kwargs: object) -> FakeGeneration:
            self.generation_input = kwargs["input"]
            self.generation_metadata = kwargs["metadata"]
            return self.generation_handle

    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)
    trace = FakeTrace()

    monkeypatch.setattr(
        "backend.chat.service.settings.observability_capture_full_prompts",
        True,
    )

    generate_answer("What?", ["secret internal KB chunk"], api_key="sk-test", trace=trace)

    system_prompt, user_message = build_rag_messages("What?", ["secret internal KB chunk"])
    assert trace.generation_input == [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    assert trace.generation_metadata == {
        # gpt-5-mini is a reasoning model — temperature omitted, larger token budget
        "max_completion_tokens": settings.chat_response_max_tokens_reasoning,
        "response_language": "en",
        "context_chunk_count": 1,
        "quick_answer_count": 0,
        "captures_full_prompt": True,
        "finish_reason_expected": "stop_or_length",
        "system_prompt": system_prompt,
        "context_chunks": ["secret internal KB chunk"],
    }


def test_generate_answer_ends_generation_on_openai_error(mock_openai_client: Mock) -> None:
    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_handle = FakeGeneration()

        def generation(self, **kwargs: object) -> FakeGeneration:
            return self.generation_handle

    trace = FakeTrace()
    mock_openai_client.chat.completions.create.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        generate_answer("What?", ["chunk1"], api_key="sk-test", trace=trace)

    assert len(trace.generation_handle.end_calls) == 1
    end_call = trace.generation_handle.end_calls[0]
    assert end_call["level"] == "ERROR"
    assert end_call["status_message"] == "boom"
    assert "duration_ms" in end_call["metadata"]


def test_generate_answer_logs_tokens_with_operation_generate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO"):
        answer, tokens = generate_answer(
            "What is X?",
            ["chunk"],
            api_key="sk-test",
            response_language="fr",
        )

    assert answer == "AI response"
    assert tokens == 100
    assert any(
        getattr(record, "operation", None) == "generate"
        and getattr(record, "target_language", None) == "fr"
        and getattr(record, "tokens", None) == 100
        and getattr(record, "model", None) == settings.chat_model
        for record in caplog.records
        if record.msg == "llm_tokens_used"
    )


# ─── Output-language enforcement tests ──────────────────────────────────────
# Regression coverage for bug 86exdd2gw: bot must reply in the user's language
# even when retrieved context is in a different language (PR #513 made this
# scenario common — cross-lingual retrieval works, so RU chunks now reach
# prompts for EN questions and the model echoed the context language).


def test_build_rag_prompt_language_directive_uses_full_language_name() -> None:
    """The output-language rule must use the human-readable name (English/Russian),
    not the bare ISO code — full names steer the model far more reliably."""
    prompt = build_rag_prompt("Q?", ["chunk"], response_language="en")
    head = prompt[:600]
    assert "CRITICAL — OUTPUT LANGUAGE" in head
    assert "English" in head
    # Bare two-letter directive removed; must not appear as a standalone rule.
    assert "Respond strictly in en" not in prompt


def test_build_rag_prompt_warns_about_context_language_mismatch() -> None:
    """The prompt must explicitly tell the model that context may be in a
    different language and that it must translate setting names / menu paths."""
    prompt = build_rag_prompt("Q?", ["chunk"], response_language="en")
    assert "may be in a different language" in prompt
    assert "translate" in prompt.lower()


def test_build_rag_prompt_repeats_language_reminder_after_context() -> None:
    """A second reminder must appear AFTER the context block. Long retrieved
    context biases attention toward recent tokens; the top-of-prompt rule alone
    is not enough."""
    prompt = build_rag_prompt(
        "Q?", ["chunk-text"], response_language="en"
    )
    context_idx = prompt.index("Context:")
    question_idx = prompt.index("Question:")
    tail = prompt[context_idx:question_idx]
    assert "REMINDER" in tail
    assert "English" in tail


def test_build_rag_prompt_full_language_name_for_russian() -> None:
    prompt = build_rag_prompt("Q?", ["chunk"], response_language="ru")
    assert "Russian" in prompt
    assert "REMINDER" in prompt


def test_enforce_response_language_translates_when_language_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the model produced text in a different language than response_language,
    the post-generation guard must translate it back."""
    russian_answer = (
        "Откройте панель управления TurboFlare, перейдите в раздел CDN и проверьте "
        "статус сертификата в подразделе SSL — это самый надёжный способ."
    )
    captured: dict[str, str | None] = {}

    def _fake_translate(*, source_text: str, target_language: str, api_key: str | None) -> LocalizationResult:
        captured["source_text"] = source_text
        captured["target_language"] = target_language
        return LocalizationResult(text="TRANSLATED-EN", tokens_used=12)

    monkeypatch.setattr(rag_handler, "translate_text_result", _fake_translate)
    out = rag_handler._enforce_response_language(
        russian_answer, response_language="en", api_key="sk-test"
    )
    assert out == "TRANSLATED-EN"
    assert captured["target_language"] == "en"
    assert captured["source_text"] == russian_answer


def test_enforce_response_language_noop_when_languages_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the answer language already matches, no translation call is issued."""

    def _should_not_be_called(**_kwargs: object) -> LocalizationResult:
        raise AssertionError("translate_text_result must not be called when languages match")

    monkeypatch.setattr(rag_handler, "translate_text_result", _should_not_be_called)
    russian = "Я покажу вам, как настроить SSL-сертификат для основного домена."
    out = rag_handler._enforce_response_language(
        russian, response_language="ru", api_key="sk-test"
    )
    assert out == russian


def test_enforce_response_language_skips_without_api_key() -> None:
    """No api_key → cannot translate → return original text unchanged."""
    russian = "Я не знаю, как ответить на этот вопрос."
    out = rag_handler._enforce_response_language(
        russian, response_language="en", api_key=None
    )
    assert out == russian


def test_enforce_response_language_skips_unreliable_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short / ambiguous text → langdetect unreliable → leave answer untouched
    rather than risk a wrong forced translation."""

    def _should_not_be_called(**_kwargs: object) -> LocalizationResult:
        raise AssertionError("translate_text_result must not be called for unreliable detection")

    monkeypatch.setattr(rag_handler, "translate_text_result", _should_not_be_called)
    out = rag_handler._enforce_response_language(
        "OK.", response_language="ru", api_key="sk-test"
    )
    assert out == "OK."
