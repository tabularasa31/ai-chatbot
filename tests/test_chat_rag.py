"""Unit tests for RAG prompt building, answer generation, and validation."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.handlers import rag as rag_handler
from backend.chat.handlers.rag import async_generate_answer
from backend.chat.language import LocalizationResult
from backend.chat.service import (
    RetrievalContext,
    _quick_answer_keys_for_question,
    _quick_answers_context,
    async_retrieve_context,
    build_rag_messages,
    build_rag_prompt,
)
from backend.chat.types import QuestionIntentResult
from backend.core.config import settings
from backend.models import (
    MessageRole,
    QuickAnswer,
    SourceSchedule,
    SourceStatus,
    UrlSource,
)
from tests._async_utils import as_async
from tests.conftest import register_and_verify_user, set_client_openai_key


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
    answer, tokens, *_ = asyncio.run(async_generate_answer("question", [], api_key="sk-test"))
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

    answer, tokens, *_ = asyncio.run(
        async_generate_answer(
            "Where is the documentation?",
            [],
            api_key="sk-test",
            quick_answer_items=["Documentation: https://docs.example.com/"],
        )
    )

    assert answer == "Documentation: https://docs.example.com/"
    assert tokens == 42
    mock_openai_client.chat.completions.create.assert_called_once()


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

    answer = _quick_answers_context(
        tenant_id, db_session, QuestionIntentResult(documentation=True)
    )

    assert answer == ["Documentation: https://docs.example.com/"]


def test_quick_answer_keys_for_question_filters_by_topic() -> None:
    # Key selection reads the classifier verdict only — the question text and
    # the language it is written in never reach this function.
    assert _quick_answer_keys_for_question(QuestionIntentResult(pricing=True)) == [
        "pricing_url",
        "trial_info",
    ]
    assert _quick_answer_keys_for_question(QuestionIntentResult()) == []
    assert _quick_answer_keys_for_question(None) == []
    # ``support_chat`` is intentionally never surfaced.
    assert _quick_answer_keys_for_question(
        QuestionIntentResult(support_contact=True)
    ) == [
        "support_email",
        "status_page_url",
    ]
    assert _quick_answer_keys_for_question(QuestionIntentResult(documentation=True)) == [
        "documentation_url",
    ]
    assert _quick_answer_keys_for_question(QuestionIntentResult(service_status=True)) == [
        "status_page_url",
    ]
    # Several axes at once de-duplicate while keeping first-seen order.
    assert _quick_answer_keys_for_question(
        QuestionIntentResult(pricing=True, service_status=True, support_contact=True)
    ) == [
        "pricing_url",
        "trial_info",
        "status_page_url",
        "support_email",
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

    answer = _quick_answers_context(
        tenant_id, db_session, QuestionIntentResult(documentation=True)
    )

    assert answer == ["Documentation: https://docs.example.com/guide"]


def test_build_rag_prompt_includes_structured_quick_answers() -> None:
    """STRUCTURED QUICK ANSWERS rules are static template text — both the
    section marker and the "prefer" instruction render together whenever any
    quick-answer items are supplied, regardless of which items they are."""
    prompt = build_rag_prompt(
        "Where is the documentation?",
        ["Chunk about setup."],
        quick_answer_items=[
            "Documentation: https://docs.example.com/",
            "Pricing: https://example.com/pricing",
        ],
    )

    assert "STRUCTURED QUICK ANSWERS" in prompt
    assert "prefer STRUCTURED QUICK ANSWERS when relevant" in prompt
    assert "Documentation: https://docs.example.com/" in prompt
    assert "Pricing: https://example.com/pricing" in prompt


def test_build_rag_prompt_static_answer_rules() -> None:
    """These instructions are static template text, present in every prompt
    regardless of the question or chunks — asserted together in one call."""
    prompt = build_rag_prompt(
        "Which setting should I use?",
        ["Use the setting named API Base URL in the Connection section."],
    )

    assert "name the exact setting or field as written in the documentation" in prompt
    assert "Do not say you do not know when relevant evidence is present" in prompt
    assert "If sources in the provided context appear inconsistent" in prompt
    assert "answer conservatively from the clearest supported part only" in prompt


def test_generate_answer_with_context(mock_openai_client: Mock) -> None:
    """With chunks, calls OpenAI and returns answer + tokens."""
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(total_tokens=100)

    answer, tokens, *_ = asyncio.run(async_generate_answer("What?", ["chunk1"], api_key="sk-test"))
    assert answer == "The answer is 42"
    assert tokens == 100
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == settings.chat_model
    assert call_kwargs["messages"][0]["role"] == "system"
    assert call_kwargs["messages"][1]["role"] == "user"
    assert "prompt_cache_key" not in call_kwargs
    # gpt-5-mini is a reasoning model — temperature is omitted, larger token budget used,
    # reasoning effort and verbosity are capped for latency. verbosity rides in
    # extra_body: SDK versions allowed by requirements.txt predate the typed kwarg.
    assert "temperature" not in call_kwargs
    assert call_kwargs["max_completion_tokens"] == settings.chat_response_max_tokens_reasoning
    assert call_kwargs["reasoning_effort"] == settings.chat_reasoning_effort
    assert "verbosity" not in call_kwargs
    assert call_kwargs["extra_body"]["verbosity"] == settings.chat_verbosity


def test_generate_answer_emits_cached_tokens_to_posthog(
    mock_openai_client: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_events: list[dict[str, object]] = []

    def fake_capture(event: str, **kwargs: object) -> None:
        captured_events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.events.capture_event", fake_capture)
    mock_openai_client.chat.completions.create.return_value.choices = [
        Mock(message=Mock(content="The answer is 42"))
    ]
    mock_openai_client.chat.completions.create.return_value.usage = Mock(
        total_tokens=120,
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=Mock(cached_tokens=64),
    )

    asyncio.run(
        async_generate_answer(
            "What?",
            ["chunk1"],
            api_key="sk-test",
            metrics_tenant_id="ck_test",
            metrics_bot_id="bot_test",
        )
    )

    events = [event for event in captured_events if event["event"] == "$ai_generation"]
    assert len(events) == 1
    props = events[0]["properties"]
    assert props["$ai_cached_tokens"] == 64
    assert props["prompt_cache_cached_tokens"] == 64
    assert props["prompt_cache_hit"] is True
    assert isinstance(props["prompt_cache_prefix_tokens_estimate"], int)
    # Fingerprint of the system prompt: byte-stable across identical bot
    # config, so adjacent turns must report the same value.
    fingerprint = props["prompt_cache_prefix_fingerprint"]
    assert isinstance(fingerprint, str) and len(fingerprint) == 16
    system_prompt, _ = build_rag_messages("What?", ["chunk1"])
    assert fingerprint == hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    # With a bot id present, the cache key is forwarded via extra_body (not a
    # named kwarg) so it works on every SDK version allowed by requirements.txt.
    # verbosity shares extra_body for the same SDK-compat reason.
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert "prompt_cache_key" not in call_kwargs
    assert call_kwargs["extra_body"] == {
        "prompt_cache_key": "bot_test",
        "verbosity": settings.chat_verbosity,
    }


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

    asyncio.run(
        async_generate_answer("What?", ["secret internal KB chunk"], api_key="sk-test", trace=trace)
    )

    assert isinstance(trace.generation_input, dict)
    prefix_estimate = trace.generation_input.pop("prompt_cache_prefix_tokens_estimate")
    assert isinstance(prefix_estimate, int)
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

    asyncio.run(
        async_generate_answer("What?", ["secret internal KB chunk"], api_key="sk-test", trace=trace)
    )

    system_prompt, user_message = build_rag_messages("What?", ["secret internal KB chunk"])
    assert trace.generation_input == [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    assert isinstance(trace.generation_metadata, dict)
    prefix_estimate = trace.generation_metadata.pop("prompt_cache_prefix_tokens_estimate")
    assert isinstance(prefix_estimate, int)
    prefix_fingerprint = trace.generation_metadata.pop("prompt_cache_prefix_fingerprint")
    assert prefix_fingerprint == hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
    assert trace.generation_metadata == {
        # gpt-5-mini is a reasoning model — temperature omitted, larger token budget,
        # reasoning effort and verbosity capped for latency
        "reasoning_effort": settings.chat_reasoning_effort,
        "verbosity": settings.chat_verbosity,
        "max_completion_tokens": settings.chat_response_max_tokens_reasoning,
        "response_language": "en",
        "context_chunk_count": 1,
        "quick_answer_count": 0,
        "prompt_cache_prefix_meets_minimum": True,
        "captures_full_prompt": True,
        "finish_reason_expected": "stop_or_length",
        "system_prompt": system_prompt,
        "context_chunks": ["secret internal KB chunk"],
    }


class _ProviderStampTrace:
    """Trace double that only records the generation's ``end`` payload."""

    class _Generation:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    def __init__(self) -> None:
        self.generation_handle = self._Generation()

    def generation(self, **kwargs: object) -> "_ProviderStampTrace._Generation":
        return self.generation_handle


def _generation_end_metadata(trace: _ProviderStampTrace) -> dict[str, object]:
    assert len(trace.generation_handle.end_calls) == 1
    metadata = trace.generation_handle.end_calls[0]["metadata"]
    assert isinstance(metadata, dict)
    return metadata


def test_generate_answer_traces_provider_identifiers_non_streaming(
    mock_openai_client: Mock,
) -> None:
    mock_openai_client.chat.completions.create.return_value = Mock(
        id="chatcmpl-abc123",
        system_fingerprint="fp_44709d6f",
        choices=[Mock(message=Mock(content="The answer is 42"), finish_reason="stop")],
        usage=Mock(total_tokens=100, prompt_tokens=60, completion_tokens=40),
    )
    trace = _ProviderStampTrace()

    asyncio.run(async_generate_answer("What?", ["ctx"], api_key="sk-test", trace=trace))

    metadata = _generation_end_metadata(trace)
    assert metadata["provider_request_id"] == "chatcmpl-abc123"
    assert metadata["system_fingerprint"] == "fp_44709d6f"


def test_generate_answer_traces_provider_identifiers_streaming(
    mock_openai_client: Mock,
) -> None:
    chunks = [
        Mock(
            id="chatcmpl-stream1",
            system_fingerprint="fp_stream",
            choices=[Mock(delta=Mock(content="The answer "), finish_reason=None)],
            usage=None,
        ),
        Mock(
            id="chatcmpl-stream1",
            system_fingerprint="fp_stream",
            choices=[Mock(delta=Mock(content="is 42"), finish_reason="stop")],
            usage=None,
        ),
        Mock(
            id="chatcmpl-stream1",
            system_fingerprint="fp_stream",
            choices=[],
            usage=Mock(total_tokens=100, prompt_tokens=60, completion_tokens=40),
        ),
    ]
    mock_openai_client.chat.completions.create.side_effect = lambda *a, **kw: list(chunks)
    trace = _ProviderStampTrace()

    asyncio.run(
        async_generate_answer(
            "What?", ["ctx"], api_key="sk-test", trace=trace, stream_callback=lambda _: None
        )
    )

    metadata = _generation_end_metadata(trace)
    assert metadata["provider_request_id"] == "chatcmpl-stream1"
    assert metadata["system_fingerprint"] == "fp_stream"


@pytest.mark.parametrize("streaming", [False, True])
def test_generate_answer_traces_none_when_provider_identifiers_absent(
    mock_openai_client: Mock, streaming: bool
) -> None:
    """The default conftest mocks carry no string ``id`` / ``system_fingerprint``:
    both keys must still be present, as ``None``, on either path."""
    trace = _ProviderStampTrace()

    asyncio.run(
        async_generate_answer(
            "What?",
            ["ctx"],
            api_key="sk-test",
            trace=trace,
            stream_callback=(lambda _: None) if streaming else None,
        )
    )

    metadata = _generation_end_metadata(trace)
    assert metadata["provider_request_id"] is None
    assert metadata["system_fingerprint"] is None


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
        asyncio.run(async_generate_answer("What?", ["chunk1"], api_key="sk-test", trace=trace))

    assert len(trace.generation_handle.end_calls) == 1
    end_call = trace.generation_handle.end_calls[0]
    assert end_call["level"] == "ERROR"
    assert end_call["status_message"] == "boom"
    assert "duration_ms" in end_call["metadata"]


def test_generate_answer_logs_tokens_with_operation_generate(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This test targets the *generate* token-log emission specifically. Disable
    # the post-gen language guard so its translation call (and its own log)
    # does not perturb the assertion under test.
    async def _identity_enforce(text, *, response_language, api_key):
        return (text, 0)

    monkeypatch.setattr(
        rag_handler,
        "_enforce_response_language",
        _identity_enforce,
    )
    with caplog.at_level("INFO"):
        answer, tokens, *_ = asyncio.run(
            async_generate_answer(
                "What is X?",
                ["chunk"],
                api_key="sk-test",
                response_language="fr",
            )
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
    not the bare ISO code — full names steer the model far more reliably.

    The language-agnostic translation policy lives in the system message (stable
    prompt-cache prefix); the concrete target language NAME is injected into the
    user-message section (after Context:) so the system prefix stays byte-identical
    across languages. Both appear in the full prompt — check the full prompt.
    """
    prompt = build_rag_prompt("Q?", ["chunk"], response_language="en")
    assert "CRITICAL — OUTPUT LANGUAGE" in prompt
    assert "English" in prompt
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
    the post-generation guard must translate it back AND return the translation
    cost so callers can keep token accounting accurate."""
    russian_answer = (
        "Откройте панель управления TurboFlare, перейдите в раздел CDN и проверьте "
        "статус сертификата в подразделе SSL — это самый надёжный способ."
    )
    captured: dict[str, str | None] = {}

    async def _fake_translate(*, source_text: str, target_language: str, api_key: str | None) -> LocalizationResult:
        captured["source_text"] = source_text
        captured["target_language"] = target_language
        return LocalizationResult(text="TRANSLATED-EN", tokens_used=12)

    monkeypatch.setattr(rag_handler, "translate_text_result", _fake_translate)
    text, extra_tokens = asyncio.run(rag_handler._enforce_response_language(
        russian_answer, response_language="en", api_key="sk-test"
    ))
    assert text == "TRANSLATED-EN"
    assert extra_tokens == 12
    assert captured["target_language"] == "en"
    assert captured["source_text"] == russian_answer


def test_enforce_response_language_noop_when_languages_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the answer language already matches, no translation call is issued
    and zero extra tokens are reported."""

    def _should_not_be_called(**_kwargs: object) -> LocalizationResult:
        raise AssertionError("translate_text_result must not be called when languages match")

    monkeypatch.setattr(rag_handler, "translate_text_result", _should_not_be_called)
    russian = "Я покажу вам, как настроить SSL-сертификат для основного домена."
    text, extra_tokens = asyncio.run(rag_handler._enforce_response_language(
        russian, response_language="ru", api_key="sk-test"
    ))
    assert text == russian
    assert extra_tokens == 0


def test_enforce_response_language_skips_without_api_key() -> None:
    """No api_key → cannot translate → return original text unchanged, 0 tokens."""
    russian = "Я не знаю, как ответить на этот вопрос."
    text, extra_tokens = asyncio.run(rag_handler._enforce_response_language(
        russian, response_language="en", api_key=None
    ))
    assert text == russian
    assert extra_tokens == 0


def test_enforce_response_language_skips_unreliable_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short / ambiguous text → langdetect unreliable → leave answer untouched
    rather than risk a wrong forced translation."""

    def _should_not_be_called(**_kwargs: object) -> LocalizationResult:
        raise AssertionError("translate_text_result must not be called for unreliable detection")

    monkeypatch.setattr(rag_handler, "translate_text_result", _should_not_be_called)
    text, extra_tokens = asyncio.run(rag_handler._enforce_response_language(
        "OK.", response_language="ru", api_key="sk-test"
    ))
    assert text == "OK."
    assert extra_tokens == 0


def test_generate_answer_skips_language_guard_when_streaming(
    mock_openai_client: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In streaming flows the answer is already emitted to the client chunk-by-
    chunk; rewriting the final text would produce a UI/history mismatch.

    The guard MUST NOT run when ``stream_callback`` is provided. Streaming
    relies on the prompt-level directive (Layer 1) for language enforcement.
    """

    def _should_not_be_called(**_kwargs: object) -> LocalizationResult:
        raise AssertionError("language guard must not invoke translation in stream mode")

    monkeypatch.setattr(rag_handler, "translate_text_result", _should_not_be_called)

    russian_text = (
        "Откройте панель управления TurboFlare и перейдите в раздел CDN. "
        "Проверьте статус сертификата в подразделе SSL — это самый надёжный способ."
    )
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content=russian_text))],
        usage=Mock(total_tokens=88, prompt_tokens=40, completion_tokens=48),
    )

    streamed: list[str] = []
    answer, tokens, *_ = asyncio.run(
        async_generate_answer(
            "How do I check SSL status?",
            ["ctx"],
            api_key="sk-test",
            response_language="en",
            stream_callback=streamed.append,
        )
    )

    assert answer == russian_text
    assert tokens == 88
    assert streamed and "".join(streamed) == russian_text


def test_generate_answer_adds_translation_tokens_to_total(
    mock_openai_client: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the guard translates a drifted answer, the translation's
    ``tokens_used`` MUST be added to the total so chat-level accounting and
    cost dashboards do not under-report usage."""
    russian_text = (
        "Это длинный ответ полностью на русском языке. Откройте панель управления "
        "и перейдите в раздел CDN, проверьте статус SSL сертификата в подразделе."
    )
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content=russian_text))],
        usage=Mock(total_tokens=80, prompt_tokens=30, completion_tokens=50),
    )

    async def _fake_translate(**_kwargs: object) -> LocalizationResult:
        return LocalizationResult(text="Translated answer in English.", tokens_used=25)

    monkeypatch.setattr(rag_handler, "translate_text_result", _fake_translate)

    answer, tokens, *_ = asyncio.run(
        async_generate_answer(
            "How do I check SSL status?",
            ["ctx"],
            api_key="sk-test",
            response_language="en",
        )
    )

    assert answer == "Translated answer in English."
    assert tokens == 80 + 25


def test_build_rag_prompt_does_not_volunteer_a_support_ticket() -> None:
    """A gap in the documentation is answered honestly, not handed to a person.
    The only rule that may put a handoff in front of the user is the
    `<needs_human/>` one, which fires when the user has nowhere else to go."""
    prompt = build_rag_prompt("Q?", ["chunk"])
    assert "Do NOT volunteer a support ticket" in prompt
    assert "not by itself a reason to hand the conversation to a person" in prompt
    assert "A definitive negative answer is a resolved answer" in prompt
    assert "that rule is the ONLY reason to put a handoff in front of the user" in prompt
    assert "`<needs_human/>`" in prompt


def test_build_rag_prompt_strong_context_line_goes_to_the_user_message() -> None:
    """strong_context tells the model the retrieval cleared the handoff bar, so
    it answers from the context instead of reporting a documentation gap. It
    lives in the user message so the cached system prefix stays byte-identical."""
    system_prompt, user_message = build_rag_messages(
        "Q?", ["chunk"], strong_context=True
    )
    assert "CONTEXT MATCH (this turn)" in user_message
    assert "rather than reporting it as undocumented" in user_message
    assert "does not silence the `<needs_human/>` marker" in user_message
    assert "CONTEXT MATCH (this turn)" not in system_prompt

    baseline_system, baseline_user = build_rag_messages("Q?", ["chunk"])
    assert "CONTEXT MATCH (this turn)" not in baseline_user
    assert baseline_system == system_prompt


def test_generate_answer_sends_cost_with_usage(mock_openai_client: Mock) -> None:
    """Langfuse prices observations from its own model table, which does not
    know the models we run — so the turn's cost has to travel with the usage
    payload, and the cached part of the prompt at its cheaper rate."""

    class FakeGeneration:
        def __init__(self) -> None:
            self.end_calls: list[dict[str, object]] = []

        def end(self, **kwargs: object) -> None:
            self.end_calls.append(kwargs)

    class FakeTrace:
        def __init__(self) -> None:
            self.generation_handle = FakeGeneration()
            self.model: object | None = None

        def generation(self, **kwargs: object) -> FakeGeneration:
            self.model = kwargs["model"]
            return self.generation_handle

    response = mock_openai_client.chat.completions.create.return_value
    response.choices = [Mock(message=Mock(content="The answer is 42"))]
    response.model = "gpt-5-mini"
    response.usage = Mock(
        total_tokens=12_000,
        prompt_tokens=10_000,
        completion_tokens=2_000,
        prompt_tokens_details=Mock(cached_tokens=8_000),
    )
    trace = FakeTrace()

    asyncio.run(async_generate_answer("What?", ["chunk1"], api_key="sk-test", trace=trace))

    usage = trace.generation_handle.end_calls[-1]["usage"]
    expected = settings.compute_cost_breakdown("gpt-5-mini", 10_000, 2_000, 8_000)
    assert usage == {
        "unit": "TOKENS",
        "input": 10_000,
        "output": 2_000,
        "inputCost": expected["input"],
        "outputCost": expected["output"],
        "totalCost": expected["total"],
    }
    assert usage["totalCost"] > 0
    # The cached 8k of the prompt cost a tenth of the fresh 2k.
    assert usage["inputCost"] < 10_000 / 1_000_000 * settings.model_cost_rates("gpt-5-mini")["input"]


def test_classified_intent_reaches_generation_as_quick_answers(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end seam: classifier verdict -> pipeline -> generation kwargs.

    The unit tests above cover the verdict-to-keys mapping in isolation; this one
    fails if the verdict is dropped anywhere on the way to the prompt.
    """
    from backend.chat.service import RetrievalContext
    from backend.search.service import build_reliability_assessment

    token = register_and_verify_user(tenant, db_session, email="intent-e2e@example.com")
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Intent E2E"},
    )
    assert created.status_code == 201
    set_client_openai_key(tenant, token)
    api_key = created.json()["api_key"]
    tenant_id = uuid.UUID(created.json()["id"])

    source = UrlSource(
        tenant_id=tenant_id,
        name="Site",
        url="https://example.com/",
        normalized_domain="example.com",
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
            key="pricing_url",
            value="https://example.com/pricing",
            source_url="https://example.com/",
            metadata_json={"method": "anchor"},
        )
    )
    db_session.commit()

    mock_openai_client.embeddings.create.return_value.data = [Mock(embedding=[0.1] * 1536)]

    async def _fake_retrieve(*_args, **_kwargs) -> RetrievalContext:
        return RetrievalContext(
            chunk_texts=["Plans and limits overview."],
            document_ids=[],
            scores=[0.8],
            mode="hybrid",
            best_rank_score=0.8,
            best_confidence_score=0.8,
            confidence_source="vector_similarity",
            reliability=build_reliability_assessment(top_score=0.8, result_count=1),
        )

    monkeypatch.setattr("backend.chat.service.async_retrieve_context", _fake_retrieve)

    async def _fake_classifier(*_args, **_kwargs) -> QuestionIntentResult:
        return QuestionIntentResult(pricing=True)

    monkeypatch.setattr(
        "backend.chat.service.classify_question_intent", _fake_classifier
    )

    seen: list[list[str] | None] = []

    async def _fake_generate(*_args, **kwargs):
        seen.append(kwargs.get("quick_answer_items"))
        return ("Answer.", 50, 20, 30, False, False, False)

    monkeypatch.setattr(
        "backend.chat.handlers.rag.async_generate_answer", _fake_generate
    )

    response = tenant.post(
        "/chat",
        headers={"X-API-Key": api_key},
        json={"session_id": str(uuid.uuid4()), "question": "?"},
    )

    assert response.status_code == 200
    assert seen == [["Pricing: https://example.com/pricing"]]


# ---------------------------------------------------------------------------
# async_retrieve_context — absorbed from the deleted test_chat_retrieval.py
# ---------------------------------------------------------------------------

def test_retrieve_context_propagates_reliability_cap_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import SearchResultBundle, build_reliability_assessment

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="reset password in settings panel",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.search.service.search_similar_chunks_detailed_async",
        as_async(lambda *args, **kwargs: SearchResultBundle(
            results=[(embedding, 0.88)],
            best_vector_similarity=0.88,
            query_variants=["reset password"],
            reliability=build_reliability_assessment(
                top_score=0.88,
                result_count=5,
                source_overlap_detected=True,
            ),
        )),
    )

    class FakeBind:
        url = "postgresql://test"

    class FakeDB:
        bind = FakeBind()

    context = asyncio.run(
        async_retrieve_context(
            tenant_id=uuid.uuid4(),
            question="reset password",
            db=FakeDB(),
            api_key="sk-test",
        )
    )

    assert context.reliability.source_overlap_detected is True
    assert context.reliability.source_overlap_pairs == []
    assert context.reliability.score == "medium"
    assert context.reliability.cap_reason == "source_overlap"


def test_retrieve_context_uses_vector_confidence_and_lexical_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.models import Embedding
    from backend.search.service import SearchResultBundle

    embedding = Embedding(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_text="secret number explanation",
        metadata_json={"chunk_index": 0},
    )

    monkeypatch.setattr(
        "backend.search.service.search_similar_chunks_detailed_async",
        as_async(lambda *args, **kwargs: SearchResultBundle(
            results=[(embedding, 0.77)],
            best_vector_similarity=0.0,
            best_keyword_score=1.0,
            has_lexical_signal=True,
            query_variants=["secret number"],
        )),
    )

    class FakeBind:
        url = "sqlite://test"

    class FakeDB:
        bind = FakeBind()

    context = asyncio.run(
        async_retrieve_context(
            tenant_id=uuid.uuid4(),
            question="secret number",
            db=FakeDB(),
            api_key="sk-test",
        )
    )

    assert context.mode == "hybrid"
    assert context.best_rank_score == 0.77
    assert context.best_confidence_score == 0.0
    assert context.confidence_source == "vector_similarity"


# ---------------------------------------------------------------------------
# OpenAPI schema contract for /chat vs /widget/chat -- absorbed from the
# deleted test_chat_schema_unified.py
# ---------------------------------------------------------------------------

def _component_ref_for(spec: dict, path: str, method: str, media_type: str) -> str | None:
    operation = spec["paths"][path][method]
    response = operation["responses"]["200"]
    schema = response.get("content", {}).get(media_type, {}).get("schema")
    if schema is None:
        return None
    if "$ref" in schema:
        return schema["$ref"]
    if "items" in schema and "$ref" in schema["items"]:
        return schema["items"]["$ref"]
    return None


def test_private_and_widget_chat_advertise_distinct_turn_schemas(tenant: TestClient) -> None:
    """Private /chat and widget /chat must expose schemas that match their wire payloads.

    Each endpoint advertises the schema under the media type it actually serves —
    `application/json` for the private API, `text/event-stream` for the widget —
    so OpenAPI client generators see the right wire protocol on each side.
    """
    spec = tenant.get("/openapi.json").json()

    private_ref = _component_ref_for(spec, "/chat", "post", "application/json")
    widget_ref = _component_ref_for(spec, "/widget/chat", "post", "text/event-stream")

    assert private_ref is not None, "private /chat should advertise an application/json schema"
    assert widget_ref is not None, (
        "widget /chat should advertise a text/event-stream schema for the SSE done payload"
    )
    assert private_ref.endswith("/ChatTurnResponse"), (
        f"private /chat must reference ChatTurnResponse, got {private_ref}"
    )
    assert widget_ref.endswith("/WidgetChatTurnResponse"), (
        f"widget /chat must reference WidgetChatTurnResponse, got {widget_ref}"
    )

    # Widget must NOT advertise itself as application/json (it streams SSE).
    widget_json_ref = _component_ref_for(spec, "/widget/chat", "post", "application/json")
    assert widget_json_ref is None, (
        "widget /chat must not advertise application/json — it streams SSE; "
        f"got {widget_json_ref}"
    )

    private_schema = spec["components"]["schemas"]["ChatTurnResponse"]
    private_properties = private_schema["properties"]
    # delivered_to_operator is on BOTH contours, unlike source_documents /
    # tokens_used. It is not a trace field: without it this contour cannot
    # tell "a human is handling this" (empty text by design) from "the turn
    # broke", and custom server-side integrations need that as much as the
    # widget does.
    assert set(private_properties.keys()) == {
        "text",
        "session_id",
        "chat_ended",
        "ticket_number",
        "delivered_to_operator",
        "source_documents",
        "tokens_used",
    }
    # `validation` was removed — guard against accidental reintroduction.
    assert "validation" not in private_properties

    widget_schema = spec["components"]["schemas"]["WidgetChatTurnResponse"]
    widget_properties = widget_schema["properties"]
    # outcome + failure_state are degraded-state extensions for the
    # LLM-unavailable path; populated only when the OpenAI provider fails
    # mid-turn. Old widgets that ignore them still render `text`.
    # delivered_to_operator marks the muted path — a human operator holds the
    # chat, so the visitor's message was recorded and handed on and `text` is
    # empty by design. Old widgets that ignore it render nothing, which is the
    # correct behaviour anyway.
    # escalation_offered says the reply put a handoff offer on the table and is
    # waiting for a yes/no. It is the backend's own record (the pre-confirm
    # gate), which is what lets any consumer — the widget, the eval driver —
    # score an offer without matching the reply text per language.
    assert set(widget_properties.keys()) == {
        "text",
        "session_id",
        "chat_ended",
        "ticket_number",
        "outcome",
        "failure_state",
        "delivered_to_operator",
        "escalation_offered",
    }
    assert "source_documents" not in widget_properties
    assert "tokens_used" not in widget_properties


# ---------------------------------------------------------------------------
# Dialog-context bridge (_assemble_chat_messages, _build_prior_messages_for_llm,
# build_dialog_context) -- absorbed from the deleted test_chat_followup.py
# ---------------------------------------------------------------------------

class _StubMessage:
    def __init__(self, role: MessageRole, content: str, *, idx: int = 0):
        self.role = role
        self.content = content
        self.id = idx
        self.created_at = None


# ---------------------------------------------------------------------------
# _assemble_chat_messages: system → prior_messages → current user
# ---------------------------------------------------------------------------


def test_assemble_chat_messages_inserts_prior_between_system_and_user() -> None:
    from backend.chat.handlers.rag import _assemble_chat_messages

    prior = [
        {"role": "user", "content": "как настроить виджет"},
        {"role": "assistant", "content": "вот как — хотите помогу с темой?"},
    ]
    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Question: да",
        prior_messages=prior,
    )
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == prior[0]
    assert out[2] == prior[1]
    assert out[-1] == {"role": "user", "content": "Question: да"}


def test_assemble_chat_messages_without_prior_keeps_legacy_shape() -> None:
    from backend.chat.handlers.rag import _assemble_chat_messages

    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Question: цена?",
        prior_messages=None,
    )
    assert [m["role"] for m in out] == ["system", "user"]


def test_assemble_chat_messages_empty_prior_treated_as_none() -> None:
    from backend.chat.handlers.rag import _assemble_chat_messages

    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Q",
        prior_messages=[],
    )
    assert [m["role"] for m in out] == ["system", "user"]


# ---------------------------------------------------------------------------
# _build_prior_messages_for_llm: trims, caps, filters empties
# ---------------------------------------------------------------------------


def test_build_prior_messages_for_llm_trims_to_max_messages_and_caps_chars() -> None:
    from datetime import datetime, timedelta

    from backend.chat.handlers.rag import _build_prior_messages_for_llm

    base = datetime(2026, 1, 1, 12, 0, 0)
    msgs = []
    for i, role in enumerate(
        [
            MessageRole.user,
            MessageRole.assistant,
            MessageRole.user,
            MessageRole.assistant,
        ]
    ):
        # Plain prose, not a 200-char alphanumeric blob: the egress redactor
        # this function now runs would mask the latter as an [API_KEY] and the
        # cap would have nothing left to trim.
        long_text = "the quick brown fox jumps over the lazy dog. " * 6
        m = _StubMessage(role, long_text if i == 3 else f"msg{i}", idx=i + 1)
        m.created_at = base + timedelta(seconds=i)
        msgs.append(m)
    chat_stub = SimpleNamespace(messages=msgs)
    out = _build_prior_messages_for_llm(chat_stub, max_messages=2, char_cap=50)
    # Last 2 (msg2 and the long assistant text) win; long one is capped.
    assert len(out) == 2
    assert out[0]["role"] == "user" and out[0]["content"] == "msg2"
    assert out[1]["role"] == "assistant"
    assert out[1]["content"].endswith("…")
    assert len(out[1]["content"]) <= 51  # 50 chars + ellipsis


def test_build_prior_messages_for_llm_returns_none_for_empty_chat() -> None:
    from backend.chat.handlers.rag import _build_prior_messages_for_llm

    assert _build_prior_messages_for_llm(None, max_messages=6, char_cap=1500) is None
    chat_stub = SimpleNamespace(messages=[])
    assert _build_prior_messages_for_llm(chat_stub, max_messages=6, char_cap=1500) is None


def test_build_prior_messages_for_llm_skips_empty_content() -> None:
    from datetime import datetime

    from backend.chat.handlers.rag import _build_prior_messages_for_llm

    base = datetime(2026, 1, 1)
    blank = _StubMessage(MessageRole.user, "   ", idx=1)
    blank.created_at = base
    real = _StubMessage(MessageRole.assistant, "real reply", idx=2)
    real.created_at = base
    chat_stub = SimpleNamespace(messages=[blank, real])
    out = _build_prior_messages_for_llm(chat_stub, max_messages=6, char_cap=1500)
    assert out == [{"role": "assistant", "content": "real reply"}]


# ---------------------------------------------------------------------------
# build_dialog_context
# ---------------------------------------------------------------------------


def test_build_dialog_context_renders_last_turns_in_order() -> None:
    from backend.chat.followup import build_dialog_context

    messages = [
        _StubMessage(MessageRole.user, "old question", idx=1),
        _StubMessage(MessageRole.assistant, "old answer", idx=2),
        _StubMessage(MessageRole.user, "how do I set up SSL?", idx=3),
        _StubMessage(MessageRole.assistant, "Upload a certificate.", idx=4),
    ]
    ctx = build_dialog_context(messages, max_turns=1)
    assert ctx == "User: how do I set up SSL?\nAssistant: Upload a certificate."


def test_build_dialog_context_caps_message_length() -> None:
    from backend.chat.followup import build_dialog_context

    messages = [
        _StubMessage(MessageRole.user, "q", idx=1),
        _StubMessage(MessageRole.assistant, "a" * 1000, idx=2),
    ]
    ctx = build_dialog_context(messages, char_cap=50)
    assert ctx is not None
    for line in ctx.splitlines():
        assert len(line) <= 50 + len("Assistant: ")


def test_build_dialog_context_keeps_assistant_tail_question() -> None:
    # The bot's follow-up question sits at the END of its reply; truncation
    # must keep the tail, or continuation resolution ("да, как проверить?")
    # loses exactly the sentence it needs.
    from backend.chat.followup import build_dialog_context

    long_answer = "x" * 2000 + " Хотите помогу с настройкой делегации?"
    messages = [
        _StubMessage(MessageRole.user, "как подключить домен?", idx=1),
        _StubMessage(MessageRole.assistant, long_answer, idx=2),
    ]
    ctx = build_dialog_context(messages)
    assert ctx is not None
    assert "Хотите помогу с настройкой делегации?" in ctx


def test_build_dialog_context_keeps_user_head() -> None:
    # User messages state the topic up front — keep the head on truncation.
    from backend.chat.followup import build_dialog_context

    long_question = "как подключить домен к виджету " + "и " * 500
    messages = [
        _StubMessage(MessageRole.user, long_question, idx=1),
        _StubMessage(MessageRole.assistant, "ответ", idx=2),
    ]
    ctx = build_dialog_context(messages)
    assert ctx is not None
    assert "как подключить домен к виджету" in ctx


def test_build_dialog_context_orders_by_created_at_not_list_order() -> None:
    # Chat.messages has no DB ``order_by``; the helper must sort by
    # created_at instead of trusting iteration order.
    from datetime import datetime, timedelta

    from backend.chat.followup import build_dialog_context

    base = datetime(2026, 1, 1, 12, 0, 0)
    newer = _StubMessage(MessageRole.assistant, "свежий ответ", idx=2)
    newer.created_at = base + timedelta(minutes=5)
    older = _StubMessage(MessageRole.user, "старый вопрос", idx=1)
    older.created_at = base
    ctx = build_dialog_context([newer, older])  # intentionally reversed
    assert ctx == "User: старый вопрос\nAssistant: свежий ответ"


def test_build_dialog_context_empty_history_returns_none() -> None:
    from backend.chat.followup import build_dialog_context

    assert build_dialog_context([]) is None
    assert build_dialog_context([_StubMessage(MessageRole.user, "   ", idx=1)]) is None
