"""Unit tests for RAG prompt building, answer generation, and validation."""

from __future__ import annotations

from backend.chat.steps import generate as generate_step

import asyncio
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.chat.steps.generate import (
    async_generate_answer,
)
from backend.chat.language import LanguageDetectionResult, LocalizationResult
from backend.chat.service import (
    process_chat_message,
)
from backend.chat.types import (
    ChatPipelineResult,
    RetrievalContext,
)
from backend.chat.steps.pre_retrieval import (
    _quick_answer_keys_for_question,
    _quick_answers_context,
)
from backend.chat.steps.retrieval import (
    async_retrieve_context,
)
from backend.chat.prompts import (
    build_rag_messages,
    build_rag_prompt,
)
from backend.chat.types import QuestionIntentResult
from backend.core.config import settings
from backend.escalation.openai_escalation import complete_escalation_openai_turn
from backend.faq.faq_matcher import FAQMatchResult
from backend.guards.types import Verdict, VerdictReason
from backend.models import (
    Chat,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    EscalationTrigger,
    MessageRole,
    QuickAnswer,
    SourceSchedule,
    SourceStatus,
    Tenant,
    UrlSource,
)
from backend.search.service import build_reliability_assessment
from tests._async_utils import as_async, as_async as _as_async, as_async_generate
from tests.conftest import (
    get_default_bot_public_id,
    post_chat_message,
    register_and_verify_user,
    set_client_openai_key,
)


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

    monkeypatch.setattr("backend.observability.metrics.capture_event", fake_capture)
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

    monkeypatch.setattr(generate_step, "_enforce_response_language",
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

    monkeypatch.setattr(generate_step, "translate_text_result", _fake_translate)
    text, extra_tokens = asyncio.run(generate_step._enforce_response_language(
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

    monkeypatch.setattr(generate_step, "translate_text_result", _should_not_be_called)
    russian = "Я покажу вам, как настроить SSL-сертификат для основного домена."
    text, extra_tokens = asyncio.run(generate_step._enforce_response_language(
        russian, response_language="ru", api_key="sk-test"
    ))
    assert text == russian
    assert extra_tokens == 0


def test_enforce_response_language_skips_without_api_key() -> None:
    """No api_key → cannot translate → return original text unchanged, 0 tokens."""
    russian = "Я не знаю, как ответить на этот вопрос."
    text, extra_tokens = asyncio.run(generate_step._enforce_response_language(
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

    monkeypatch.setattr(generate_step, "translate_text_result", _should_not_be_called)
    text, extra_tokens = asyncio.run(generate_step._enforce_response_language(
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

    monkeypatch.setattr(generate_step, "translate_text_result", _should_not_be_called)

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

    monkeypatch.setattr(generate_step, "translate_text_result", _fake_translate)

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
    from backend.chat.types import RetrievalContext
    from backend.search.service import build_reliability_assessment

    token = register_and_verify_user(tenant, db_session, email="intent-e2e@example.com")
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Intent E2E"},
    )
    assert created.status_code == 201
    set_client_openai_key(tenant, token)
    bot_public_id = get_default_bot_public_id(tenant, token)
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

    monkeypatch.setattr("backend.chat.steps.retrieval.async_retrieve_context", _fake_retrieve)

    async def _fake_classifier(*_args, **_kwargs) -> QuestionIntentResult:
        return QuestionIntentResult(pricing=True)

    monkeypatch.setattr(
        "backend.chat.service.classify_question_intent", _fake_classifier
    )

    seen: list[list[str] | None] = []

    async def _fake_generate(*_args, **kwargs):
        seen.append(kwargs.get("quick_answer_items"))
        return ("Answer.", 50, 20, 30, False, False, False, False)

    monkeypatch.setattr(
        "backend.chat.steps.generate.async_generate_answer", _fake_generate
    )

    response = post_chat_message(tenant, bot_public_id=bot_public_id, question="?")

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
# OpenAPI schema contract for /widget/chat -- absorbed from the
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


def test_widget_chat_advertises_its_sse_turn_schema(tenant: TestClient) -> None:
    """Widget /chat must expose a schema that matches its wire payload.

    It advertises the schema under `text/event-stream` (SSE), the media type
    it actually serves, so OpenAPI client generators see the right wire
    protocol.
    """
    spec = tenant.get("/openapi.json").json()

    widget_ref = _component_ref_for(spec, "/widget/chat", "post", "text/event-stream")

    assert widget_ref is not None, (
        "widget /chat should advertise a text/event-stream schema for the SSE done payload"
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
    from backend.chat.steps.generate import (
        _assemble_chat_messages,
    )

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
    from backend.chat.steps.generate import (
        _assemble_chat_messages,
    )

    out = _assemble_chat_messages(
        system_prompt="SYS",
        user_message="Question: цена?",
        prior_messages=None,
    )
    assert [m["role"] for m in out] == ["system", "user"]


def test_assemble_chat_messages_empty_prior_treated_as_none() -> None:
    from backend.chat.steps.generate import (
        _assemble_chat_messages,
    )

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

    from backend.chat.steps.generate import (
        _build_prior_messages_for_llm,
    )

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
    from backend.chat.steps.generate import (
        _build_prior_messages_for_llm,
    )

    assert _build_prior_messages_for_llm(None, max_messages=6, char_cap=1500) is None
    chat_stub = SimpleNamespace(messages=[])
    assert _build_prior_messages_for_llm(chat_stub, max_messages=6, char_cap=1500) is None


def test_build_prior_messages_for_llm_skips_empty_content() -> None:
    from datetime import datetime

    from backend.chat.steps.generate import (
        _build_prior_messages_for_llm,
    )

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


# ---------------------------------------------------------------------------
# Strict zero-RAG-hits fast path -- absorbed from the deleted
# test_chat_zero_hits_fast_path.py
#
# Covers:
# * First zero-hits turn returns a localized "rephrase" prompt instead of
#   calling the answer LLM, and sets ``chat.last_reply_was_rephrase_prompt``.
# * Consecutive zero-hits turn + LLM relevance verdict "relevant" triggers
#   pre-confirm escalation and resets the flag.
# * Consecutive zero-hits turn + LLM verdict "not relevant" emits the
#   NOT_RELEVANT off-topic reject and resets the flag.
# * Any non-zero-hits success resets the flag.
# * The previously misleading comment in ``relevance_checker.py`` describing
#   an unimplemented off-topic pattern exception is gone.
# ---------------------------------------------------------------------------


def _as_verdict(v: Verdict | tuple[bool, str, object]) -> Verdict:
    """Adapt a legacy (relevant, reason, profile) tuple into a guard Verdict.

    Falls back to relevant->RELEVANT / not-relevant->OFFTOPIC for reason tokens
    that predate the VerdictReason enum (e.g. the old "ok"/"in_domain" stubs).
    """
    if isinstance(v, Verdict):
        return v
    relevant, reason, _profile = v
    try:
        r = VerdictReason(reason)
    except ValueError:
        r = VerdictReason.RELEVANT if relevant else VerdictReason.OFFTOPIC
    return Verdict.of(r)


def _rr(verdict: Verdict) -> tuple[bool, str]:
    return not verdict.blocked, verdict.reason.value


def _zh_create_client(http: TestClient, db: Session, *, email: str) -> tuple[Tenant, str]:
    token = register_and_verify_user(http, db, email=email)
    cl_resp = http.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Zero Hits Tenant"},
    )
    assert cl_resp.status_code in (200, 201), cl_resp.text
    set_client_openai_key(http, token)
    api_key = "sk-test"
    client_row = db.get(Tenant, uuid.UUID(cl_resp.json()["id"]))
    assert client_row is not None
    return client_row, api_key


def _zh_insert_chunk(db: Session, *, tenant_id: uuid.UUID) -> None:
    doc = Document(
        tenant_id=tenant_id,
        filename="kb.md",
        file_type=DocumentType.markdown,
        status=DocumentStatus.ready,
        parsed_text="content",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    emb = Embedding(
        document_id=doc.id,
        chunk_text="Doc chunk",
        vector=None,
        metadata_json={"vector": [0.1] * 1536, "chunk_index": 0},
    )
    db.add(emb)
    db.commit()


def _zh_empty_retrieval() -> RetrievalContext:
    return RetrievalContext(
        chunk_texts=[],
        document_ids=[],
        scores=[],
        mode="none",
        best_rank_score=None,
        best_confidence_score=None,
        confidence_source="none",
        reliability=build_reliability_assessment(top_score=0.0, result_count=0),
        vector_similarities=None,
    )


def _zh_nonempty_retrieval() -> RetrievalContext:
    return RetrievalContext(
        chunk_texts=["A relevant chunk"],
        document_ids=[uuid.uuid4()],
        scores=[0.9],
        mode="hybrid",
        best_rank_score=0.9,
        best_confidence_score=0.9,
        confidence_source="vector_similarity",
        reliability=build_reliability_assessment(top_score=0.9, result_count=1),
        vector_similarities=None,
    )


def _zh_stub_pre_retrieval(
    monkeypatch: pytest.MonkeyPatch,
    *,
    relevance: tuple[bool, str, object] = (
        True,
        "ok",
        SimpleNamespace(product_name="Product", topics=["Topic"]),
    ),
) -> None:
    """Common monkeypatches: injection clean, FAQ no-match, no escalation, no rewrites."""
    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_detect_injection",
        _as_async(lambda *_a, **_kw: Verdict.of(VerdictReason.OK)),
    )
    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_check_relevance_with_profile",
        _as_async(lambda **_kw: _as_verdict(relevance)),
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_check_relevance_with_profile",
        _as_async(lambda **_kw: _as_verdict(relevance)),
    )
    monkeypatch.setattr(
        "backend.chat.steps.generate.should_escalate",
        lambda *_a, **_kw: (False, None),
    )
    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_match_faq",
        _as_async(lambda **_kw: FAQMatchResult(
            strategy="rag_only",
            faq_items=[],
            top_score=None,
            selected_score=None,
            selected_faq_id=None,
            direct_guard_used=False,
            direct_guard_passed=False,
            decision_reason="test",
        )),
    )
    monkeypatch.setattr(
        "backend.chat.post_turn._start_mode_b_followup",
        lambda _tenant_id: None,
    )

    async def _no_rewrite(*_a, **_kw):
        return None

    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_semantic_query_rewrite", _no_rewrite
    )
    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_semantic_query_rewrite_for_kb", _no_rewrite
    )


def test_first_zero_hits_emits_soft_reply_and_sets_flag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-first@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    _zh_stub_pre_retrieval(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_empty_retrieval()),
    )
    # Asserts the answer LLM is never reached on the zero-hits path.
    def _fail_generate(*_a, **_kw):  # pragma: no cover - asserts on hit
        raise AssertionError("answer LLM must not be called on zero hits")

    monkeypatch.setattr(
        "backend.chat.steps.generate.async_generate_answer", as_async_generate(_fail_generate)
    )

    session_id = uuid.uuid4()
    outcome = process_chat_message(
        cl_row.id, "Tell me about borscht recipe", session_id, db_session,
        api_key=api_key,
    )

    assert outcome.text  # localized soft-reply, exact wording goes through localization
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.last_reply_was_rephrase_prompt is True


def test_consecutive_zero_hits_relevant_escalates(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-esc@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    profile_stub = SimpleNamespace(product_name="Product", topics=["Topic"])
    _zh_stub_pre_retrieval(
        monkeypatch,
        relevance=(True, "ok", profile_stub),
    )

    # Distinct stub for the post-retrieval consecutive-failure relevance call:
    # this is the one that decides escalation. Profile is non-empty so the
    # guard's no_profile fast-path is skipped.
    consecutive_calls: list[dict] = []

    async def _post_retrieval_relevance(**kwargs):
        consecutive_calls.append(kwargs)
        return Verdict.of(VerdictReason.RELEVANT)

    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_empty_retrieval()),
    )

    # Seed an existing chat with the rephrase-prompt flag already on.
    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    # Pre-confirm rendering hits OpenAI in production; stub it.
    monkeypatch.setattr(
        "backend.chat.handlers.rag.render_pre_confirm_text",
        _as_async(
            lambda **_kw: SimpleNamespace(
                message_to_user="Want me to escalate this to a human?",
                tokens_used=1,
            )
        ),
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.render_pre_confirm_text",
        _as_async(
            lambda **_kw: SimpleNamespace(
                message_to_user="Want me to escalate this to a human?",
                tokens_used=1,
            )
        ),
    )

    outcome = process_chat_message(
        cl_row.id, "Question with no docs", session_id, db_session,
        api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is True
    assert chat.last_reply_was_rephrase_prompt is False
    assert "escalate" in (outcome.text or "").lower()
    # The post-retrieval relevance call must bypass the short-query fast path.
    assert any(call.get("force_llm_check") is True for call in consecutive_calls)


def test_pre_confirm_render_timeout_falls_back_to_canonical_template(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow pre-confirm localization call (observed up to 21s in prod) is cut
    by the hard deadline; the canonical English template is used and the
    escalation FSM stays armed instead of the turn stalling."""
    from backend.escalation.openai_escalation import PRE_CONFIRM_NO_ANSWER_EN

    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-esc-timeout@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    profile_stub = SimpleNamespace(product_name="Product", topics=["Topic"])
    _zh_stub_pre_retrieval(monkeypatch, relevance=(True, "ok", profile_stub))

    async def _post_retrieval_relevance(**_kwargs):
        return Verdict.of(VerdictReason.RELEVANT)

    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_empty_retrieval()),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    monkeypatch.setattr(
        "backend.core.config.settings.escalation_pre_confirm_render_timeout_seconds",
        0.05,
    )

    async def _slow_render(**_kw):
        await asyncio.sleep(0.5)
        return SimpleNamespace(message_to_user="too late", tokens_used=1)

    monkeypatch.setattr("backend.chat.handlers.rag.render_pre_confirm_text", _slow_render)
    monkeypatch.setattr("backend.chat.handlers.escalation.render_pre_confirm_text", _slow_render)

    outcome = process_chat_message(
        cl_row.id, "Question with no docs", session_id, db_session,
        api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is True, (
        "timeout must degrade the text, not drop the escalation"
    )
    assert outcome.text == PRE_CONFIRM_NO_ANSWER_EN
    assert outcome.text != "too late"


def test_consecutive_zero_hits_not_relevant_emits_offtopic_reject(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-ot@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    profile_stub = SimpleNamespace(product_name="Product", topics=["Topic"])
    _zh_stub_pre_retrieval(monkeypatch, relevance=(True, "ok", profile_stub))

    async def _post_retrieval_relevance(**_kwargs):
        return Verdict.of(VerdictReason.OFFTOPIC)

    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_check_relevance_with_profile",
        _post_retrieval_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_empty_retrieval()),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    outcome = process_chat_message(
        cl_row.id, "Some unrelated query", session_id, db_session,
        api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.escalation_pre_confirm_pending is False
    assert chat.last_reply_was_rephrase_prompt is False
    assert outcome.text


def test_successful_turn_resets_rephrase_flag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-reset@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    _zh_stub_pre_retrieval(monkeypatch)
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_nonempty_retrieval()),
    )
    monkeypatch.setattr(
        "backend.chat.steps.generate.async_generate_answer",
        _as_async(lambda *_a, **_kw: ("OK answer", 5, 10, 5, False, False, False, False)),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    process_chat_message(
        cl_row.id, "A real question", session_id, db_session, api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.last_reply_was_rephrase_prompt is False


def test_intervening_non_rag_turn_resets_rephrase_flag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the codex P1: handlers other than RagHandler (Greeting,
    Escalation) must also clear ``last_reply_was_rephrase_prompt`` when they
    persist a turn. Otherwise a one-word "hi" between two unrelated
    zero-hits turns would mis-classify the second as a *consecutive* miss and
    trigger forced relevance/escalation.

    Verified by simulating an intervening turn that persists via the same
    ``_persist_turn_with_response_language`` codepath without touching the
    flag explicitly: the centralized reset in ``_finalize_persisted_messages``
    must clear it.
    """
    from backend.chat.persistence import _persist_turn_with_response_language

    cl_row, _api_key = _zh_create_client(
        tenant, db_session, email="zh-intervene@example.com"
    )
    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    # Simulate a non-Rag handler (e.g. Greeting) persisting a turn with the
    # default ``set_rephrase_flag=False`` — the same call signature these
    # handlers already use, no opt-in needed.
    _persist_turn_with_response_language(
        db=db_session,
        chat=chat,
        tenant_id=cl_row.id,
        response_language="en",
        resolution_reason="default",
        user_content="hi",
        assistant_content="Hello!",
        document_ids=[],
        extra_tokens=0,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    assert chat.last_reply_was_rephrase_prompt is False


def test_no_profile_relevance_verdict_does_not_escalate(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code review #3: ``async_check_relevance_with_profile`` returns
    ``(True, "no_profile", None)`` for tenants without a profile, even with
    ``force_llm_check=True``. That fail-open verdict must NOT escalate —
    fresh tenants without an onboarded profile would otherwise get a support
    handoff armed on every consecutive zero-hits turn.
    """
    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-nopro@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    _zh_stub_pre_retrieval(monkeypatch)

    async def _no_profile_relevance(**_kwargs):
        return Verdict.of(VerdictReason.NO_PROFILE)

    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_check_relevance_with_profile",
        _no_profile_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_check_relevance_with_profile",
        _no_profile_relevance,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_empty_retrieval()),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
    )
    db_session.add(chat)
    db_session.commit()

    process_chat_message(
        cl_row.id, "Q two", session_id, db_session, api_key=api_key,
    )

    db_session.expire_all()
    chat = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .one()
    )
    # no_profile fail-open must NOT escalate — must fall through to off-topic.
    assert chat.escalation_pre_confirm_pending is False
    assert chat.last_reply_was_rephrase_prompt is False


def test_session_ended_event_stales_rephrase_flag(
    mock_openai_client: Mock,
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code review #2: a resumed chat session — sweeper has reported it
    ``session_ended_event_at`` — must treat the persisted rephrase flag as
    stale, so the user's first question after returning gets a fresh soft
    reply instead of jumping straight to escalation.
    """
    from datetime import datetime

    cl_row, api_key = _zh_create_client(tenant, db_session, email="zh-stale@example.com")
    _zh_insert_chunk(db_session, tenant_id=cl_row.id)

    _zh_stub_pre_retrieval(monkeypatch)

    # If the stale guard fails, the pipeline would invoke the post-retrieval
    # relevance check with ``force_llm_check=True`` — assert that never
    # happens on a freshly resumed session. The pre-retrieval check (called
    # without ``force_llm_check``) still runs normally and returns relevant.
    async def _no_force_check_allowed(**kwargs):
        if kwargs.get("force_llm_check"):
            raise AssertionError(
                "Force relevance check must not fire when the previous session "
                "was already reported ended by the sweeper"
            )
        return Verdict.of(VerdictReason.RELEVANT)

    monkeypatch.setattr(
        "backend.chat.steps.pre_retrieval.async_check_relevance_with_profile",
        _no_force_check_allowed,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_check_relevance_with_profile",
        _no_force_check_allowed,
    )
    monkeypatch.setattr(
        "backend.chat.steps.retrieval.async_retrieve_context",
        _as_async(lambda *_a, **_kw: _zh_empty_retrieval()),
    )

    session_id = uuid.uuid4()
    chat = Chat(
        tenant_id=cl_row.id,
        session_id=session_id,
        last_reply_was_rephrase_prompt=True,
        session_ended_event_at=datetime.utcnow(),
    )
    db_session.add(chat)
    db_session.commit()

    outcome = process_chat_message(
        cl_row.id, "Returning question", session_id, db_session, api_key=api_key,
    )

    # New soft-reply, not escalation. The sweeper marker now triggers
    # conversation rotation, so the turn lands in a fresh Chat row (same
    # session) with the rephrase flag re-armed for the freshly observed
    # zero-hits turn; the stale flag stays behind on the archived chat.
    assert outcome.text
    db_session.expire_all()
    chats = (
        db_session.query(Chat)
        .filter(Chat.tenant_id == cl_row.id, Chat.session_id == session_id)
        .order_by(Chat.created_at.asc())
        .all()
    )
    assert len(chats) == 2
    old_chat, new_chat = chats
    assert old_chat.session_ended_event_at is not None
    assert new_chat.last_reply_was_rephrase_prompt is True
    assert new_chat.escalation_pre_confirm_pending is False


def test_relevance_force_check_failure_does_not_pollute_circuit_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code review #4: a timeout on a forced relevance call must not
    increment the shared circuit-breaker counter — otherwise one tenant's
    pathological zero-hits stream during an OpenAI outage trips the
    breaker for every other tenant's regular relevance checks.
    """
    import asyncio
    from unittest.mock import AsyncMock

    from backend.guards import relevance_checker
    from backend.guards.relevance_checker import (
        _cache,
        async_check_relevance_with_profile,
    )

    _cache.clear()

    # Reset shared CB state.
    relevance_checker._circuit_breaker.record_success()

    async def _always_timeout(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise asyncio.TimeoutError()

    monkeypatch.setattr(
        "backend.guards.relevance_checker.async_call_openai_with_retry",
        _always_timeout,
    )
    monkeypatch.setattr(
        "backend.guards.relevance_checker.get_async_openai_client",
        lambda _key, **_kw: Mock(chat=Mock(completions=Mock(create=AsyncMock()))),
    )

    profile = Mock(product_name="Acme", topics=["billing"])
    tid = uuid.uuid4()

    async def _run():
        # 10 forced calls all time out — counter must NOT advance.
        for _ in range(10):
            relevant, reason = _rr(await async_check_relevance_with_profile(
                tenant_id=tid,
                user_question="any short q",
                profile=profile,
                api_key="sk-test",
                force_llm_check=True,
            ))
            assert relevant is True
            assert reason == "timeout"

    asyncio.run(_run())

    # No failures recorded, breaker still closed.
    assert "_global" not in relevance_checker._circuit_breaker._states


def test_relevance_checker_comment_hygiene() -> None:
    """The misleading 'Exception: queries that match an explicit off-topic
    pattern are still rejected' comment described unimplemented behavior;
    after the language-agnostic redesign it must be gone.
    """
    path = Path("backend/guards/relevance_checker.py")
    src = path.read_text(encoding="utf-8")
    assert "Exception: queries that match an explicit off-topic pattern" not in src


# ---------------------------------------------------------------------------
# The slow-path ``no_documents`` verdict gets the same second chance as
# ``low_similarity`` -- absorbed from the deleted test_no_documents_second_chance.py
#
# "Nothing found in the knowledge base" is detected twice: by the zero-hits
# fast path above, which asks the user to rephrase once before it escalates,
# and by ``should_escalate``'s ``chunk_count == 0`` on a turn an FAQ or quick
# answer carried while the document search came back empty. The second one
# used to offer a support ticket on its very first occurrence. It now shares
# the ``low_similarity`` two-strike tracker: the first such turn keeps the
# generated answer, and the handoff waits for a second weak turn of either
# flavour.
# ---------------------------------------------------------------------------

_NODOCS_GENERATED_ANSWER = "The FAQ entry says the limit is per workspace."
_NODOCS_REPHRASE_PROMPT = "REPHRASE_PROMPT"
_NODOCS_PRE_CONFIRM = "PRE_CONFIRM_QUESTION"


class _NoOpFakeSpan:
    def end(self, **kwargs: object) -> None:
        return None


class _NoOpFakeTrace:
    def span(self, **kwargs: object) -> _NoOpFakeSpan:
        return _NoOpFakeSpan()

    def update(self, **kwargs: object) -> None:
        return None

    def promote(self, **kwargs: object) -> None:
        return None


def _nodocs_empty_retrieval() -> RetrievalContext:
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


def _nodocs_weak_retrieval() -> RetrievalContext:
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


def _nodocs_setup(tenant: TestClient, db_session: Session, email: str) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "No Documents Tenant"},
    ).json()
    set_client_openai_key(tenant, token)
    return uuid.UUID(created["id"]), "sk-test"


def _nodocs_patch_common(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: _NoOpFakeTrace())
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda text: LanguageDetectionResult("en", 0.99, True),
    )
    monkeypatch.setattr("backend.chat.handlers.rag._try_ingest_gap_signal", lambda **kwargs: None)
    monkeypatch.setattr(
        "backend.chat.handlers.rag._trigger_log_analysis_threshold",
        lambda *_a, **_k: None,
    )

    events: list[dict] = []

    def _record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.observability.metrics.capture_event", _record)

    async def _fake_render_pre_confirm(**kwargs):
        return type(
            "EscalationOut", (), {"message_to_user": _NODOCS_PRE_CONFIRM, "tokens_used": 1}
        )()

    monkeypatch.setattr(
        "backend.chat.handlers.rag.render_pre_confirm_text", _fake_render_pre_confirm
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.render_pre_confirm_text", _fake_render_pre_confirm
    )
    return events


def _nodocs_patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answer: str = _NODOCS_GENERATED_ANSWER,
    retrieval: RetrievalContext | None = None,
    escalation_recommended: bool = True,
    escalation_trigger: EscalationTrigger | None = EscalationTrigger.no_documents,
    llm_needs_human: bool = False,
    is_reject: bool = False,
    reject_reason: str | None = None,
) -> None:
    _retrieval = _nodocs_empty_retrieval() if retrieval is None else retrieval

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


def _nodocs_turn_props(events: list[dict]) -> dict:
    return next(e for e in events if e["event"] == "chat.turn")["properties"]


def _nodocs_chat(db_session: Session, session_id: uuid.UUID) -> Chat:
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    return chat


def test_first_zero_chunk_turn_keeps_its_answer(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An FAQ-carried turn with no document chunks answers instead of offering a ticket."""
    tenant_id, api_key = _nodocs_setup(tenant, db_session, "nodocs-first@example.com")
    session_id = uuid.uuid4()
    events = _nodocs_patch_common(monkeypatch)
    _nodocs_patch_pipeline(monkeypatch)

    outcome = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == _NODOCS_GENERATED_ANSWER
    chat = _nodocs_chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is False
    assert chat.escalation_pre_confirm_context is None
    # ...and the shared tracker is armed so the next weak turn escalates.
    assert chat.last_reply_was_low_confidence is True

    props = _nodocs_turn_props(events)
    assert props["escalated"] is False
    assert props["handoff_stood_down"] is False


def test_second_zero_chunk_turn_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id, api_key = _nodocs_setup(tenant, db_session, "nodocs-second@example.com")
    session_id = uuid.uuid4()
    events = _nodocs_patch_common(monkeypatch)
    _nodocs_patch_pipeline(monkeypatch)

    first = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )
    assert first.text == _NODOCS_GENERATED_ANSWER
    events.clear()
    outcome = process_chat_message(
        tenant_id, "and per seat?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == _NODOCS_PRE_CONFIRM
    chat = _nodocs_chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.no_documents.value
    )
    assert _nodocs_turn_props(events)["escalated"] is True


def test_zero_chunk_then_weak_turn_escalates(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two strikes count across both weak flavours, not one counter each."""
    tenant_id, api_key = _nodocs_setup(tenant, db_session, "nodocs-then-weak@example.com")
    session_id = uuid.uuid4()
    _nodocs_patch_common(monkeypatch)
    _nodocs_patch_pipeline(monkeypatch)

    process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )
    _nodocs_patch_pipeline(
        monkeypatch,
        retrieval=_nodocs_weak_retrieval(),
        escalation_trigger=EscalationTrigger.low_similarity,
    )
    outcome = process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == _NODOCS_PRE_CONFIRM
    chat = _nodocs_chat(db_session, session_id)
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
    tenant_id, api_key = _nodocs_setup(tenant, db_session, "weak-then-nodocs@example.com")
    session_id = uuid.uuid4()
    _nodocs_patch_common(monkeypatch)
    _nodocs_patch_pipeline(
        monkeypatch,
        retrieval=_nodocs_weak_retrieval(),
        escalation_trigger=EscalationTrigger.low_similarity,
    )

    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )
    _nodocs_patch_pipeline(monkeypatch)
    outcome = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == _NODOCS_PRE_CONFIRM
    chat = _nodocs_chat(db_session, session_id)
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
    tenant_id, api_key = _nodocs_setup(tenant, db_session, "nodocs-fastpath@example.com")
    session_id = uuid.uuid4()
    _nodocs_patch_common(monkeypatch)
    _nodocs_patch_pipeline(
        monkeypatch,
        answer=_NODOCS_REPHRASE_PROMPT,
        escalation_recommended=False,
        escalation_trigger=None,
        is_reject=True,
        reject_reason="rephrase",
    )

    first = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )
    assert first.text == _NODOCS_REPHRASE_PROMPT
    assert _nodocs_chat(db_session, session_id).last_reply_was_rephrase_prompt is True

    _nodocs_patch_pipeline(monkeypatch, answer=_NODOCS_REPHRASE_PROMPT)
    outcome = process_chat_message(
        tenant_id, "the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == _NODOCS_PRE_CONFIRM
    chat = _nodocs_chat(db_session, session_id)
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
    tenant_id, api_key = _nodocs_setup(tenant, db_session, "nodocs-needs-human@example.com")
    session_id = uuid.uuid4()
    events = _nodocs_patch_common(monkeypatch)
    _nodocs_patch_pipeline(monkeypatch, llm_needs_human=True)

    outcome = process_chat_message(
        tenant_id, "what is the workspace limit?", session_id, db_session, api_key=api_key
    )

    assert _NODOCS_PRE_CONFIRM in outcome.text
    chat = _nodocs_chat(db_session, session_id)
    assert chat.escalation_pre_confirm_pending is True
    assert (
        chat.escalation_pre_confirm_context["trigger"]
        == EscalationTrigger.llm_self_offer.value
    )
    assert _nodocs_turn_props(events)["handoff_stood_down"] is False


# ---------------------------------------------------------------------------
# The weak-retrieval band offers a handoff only on a second consecutive miss
# -- absorbed from the deleted test_low_confidence_second_attempt.py.
#
# ``low_similarity`` means retrieval found something and scored it below the
# handoff floor. The first-weak-turn-keeps-its-answer and second-consecutive-
# weak-turn-escalates failure modes are already asserted above by the
# cross-flavour tests (test_zero_chunk_then_weak_turn_escalates and
# test_weak_then_zero_chunk_turn_escalates share the same two-strike tracker
# with the no_documents flavour); only the reset behaviour below is distinct.
# ---------------------------------------------------------------------------


def _lowconf_weak_retrieval() -> RetrievalContext:
    """Chunks came back, but below the 0.45 handoff floor."""
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


def _lowconf_setup(tenant: TestClient, db_session: Session, email: str) -> tuple[uuid.UUID, str]:
    token = register_and_verify_user(tenant, db_session, email=email)
    created = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Second Attempt Tenant"},
    ).json()
    set_client_openai_key(tenant, token)
    return uuid.UUID(created["id"]), "sk-test"


_LOWCONF_WEAK_ANSWER = "The docs only mention the limitations list."


def _lowconf_patch_weak_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every turn retrieves weakly and the pipeline recommends escalation."""
    monkeypatch.setattr("backend.chat.service.begin_trace", lambda **kwargs: _NoOpFakeTrace())
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda text: LanguageDetectionResult("en", 0.99, True),
    )
    monkeypatch.setattr("backend.chat.handlers.rag._try_ingest_gap_signal", lambda **kwargs: None)
    monkeypatch.setattr(
        "backend.chat.handlers.rag._trigger_log_analysis_threshold",
        lambda *_a, **_k: None,
    )

    async def _weak_pipeline(*args, **kwargs) -> ChatPipelineResult:
        return ChatPipelineResult(
            raw_answer=_LOWCONF_WEAK_ANSWER,
            final_answer=_LOWCONF_WEAK_ANSWER,
            tokens_used=3,
            strategy="rag_only",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=False,
            retrieval=_lowconf_weak_retrieval(),
            escalation_recommended=True,
            escalation_trigger=EscalationTrigger.low_similarity,
        )

    monkeypatch.setattr("backend.chat.service.async_run_chat_pipeline", _weak_pipeline)

    async def _fake_render_pre_confirm(**kwargs):
        return type(
            "EscalationOut", (), {"message_to_user": _NODOCS_PRE_CONFIRM, "tokens_used": 1}
        )()

    monkeypatch.setattr(
        "backend.chat.handlers.rag.render_pre_confirm_text", _fake_render_pre_confirm
    )
    monkeypatch.setattr(
        "backend.chat.handlers.escalation.render_pre_confirm_text", _fake_render_pre_confirm
    )


def test_a_good_turn_between_two_weak_ones_resets_the_tracker(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two weak turns separated by an answered one are not 'consecutive'."""
    tenant_id, api_key = _lowconf_setup(tenant, db_session, "weak-reset@example.com")
    session_id = uuid.uuid4()
    _lowconf_patch_weak_turn(monkeypatch)

    process_chat_message(
        tenant_id, "are Workers supported?", session_id, db_session, api_key=api_key
    )

    async def _confident_pipeline(*args, **kwargs) -> ChatPipelineResult:
        return ChatPipelineResult(
            raw_answer="Yes, here is how.",
            final_answer="Yes, here is how.",
            tokens_used=3,
            strategy="rag_only",
            reject_reason=None,
            is_reject=False,
            is_faq_direct=False,
            retrieval=_lowconf_weak_retrieval(),
            escalation_recommended=False,
            escalation_trigger=None,
        )

    monkeypatch.setattr(
        "backend.chat.service.async_run_chat_pipeline", _confident_pipeline
    )
    process_chat_message(
        tenant_id, "and how do I deploy?", session_id, db_session, api_key=api_key
    )
    chat = db_session.query(Chat).filter(Chat.session_id == session_id).one()
    db_session.refresh(chat)
    assert chat.last_reply_was_low_confidence is False

    _lowconf_patch_weak_turn(monkeypatch)
    outcome = process_chat_message(
        tenant_id, "what about custom domains?", session_id, db_session, api_key=api_key
    )

    assert outcome.text == _LOWCONF_WEAK_ANSWER
    db_session.refresh(chat)
    assert chat.escalation_pre_confirm_pending is False


# ---------------------------------------------------------------------------
# Chat pipeline orchestration (process_chat_message) -- absorbed from the
# deleted test_chat_pipeline.py
# ---------------------------------------------------------------------------


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
        "backend.chat.handlers.escalation.complete_escalation_openai_turn",
        _boom_escalation,
    )

    with pytest.raises(RuntimeError, match="boom"):
        process_chat_message(
            client_row.id,
            "no thanks",
            chat.session_id,
            db_session,
            api_key="sk-test",
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
        "backend.chat.steps.retrieval.async_retrieve_context",
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
        "backend.chat.steps.generate.async_generate_answer",
        as_async_generate(lambda *args, **kwargs: ("Use the reset link in settings.", 17)),
    )
    monkeypatch.setattr(
        "backend.chat.steps.generate.should_escalate",
        lambda *args, **kwargs: (False, None),
    )

    outcome = process_chat_message(
        client_row.id,
        "How do I reset my password?",
        uuid.uuid4(),
        db_session,
        api_key="sk-test",
    )

    assert outcome.text == "Use the reset link in settings."
    assert outcome.tokens_used == 17
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
        "backend.chat.steps.retrieval.async_retrieve_context",
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
        "backend.chat.steps.generate.async_generate_answer",
        as_async_generate(
            lambda *args, **kwargs: ("Откройте настройки и сбросьте пароль.", 12)
        ),
    )
    monkeypatch.setattr(
        "backend.chat.steps.generate.should_escalate",
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
            api_key="sk-test",
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


def test_trace_metadata_stamps_knowledge_base_updated_at(
    tenant: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ClickUp 86eytw2zn — every chat-turn trace carries the time the tenant's
    knowledge base last changed (max ``Document.updated_at``), so two traces of
    the same question a week apart show at a glance whether the base moved in
    between. Tenants with no documents get an explicit ``None``."""
    import datetime as dt

    from backend.models import Document, DocumentStatus, DocumentType

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

        def stamp(self) -> object:
            for call in self.update_calls:
                md = call.get("metadata")
                if isinstance(md, dict) and "knowledge_base_updated_at" in md:
                    return md["knowledge_base_updated_at"]
            raise AssertionError("knowledge_base_updated_at missing from trace metadata")

    token = register_and_verify_user(tenant, db_session, email="trace-kb-stamp@example.com")
    cl_resp = tenant.post(
        "/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Trace KB Stamp Tenant"},
    )
    set_client_openai_key(tenant, token)
    tenant_id = uuid.UUID(cl_resp.json()["id"])
    api_key = "sk-test"

    traces: list[FakeTrace] = []

    def _begin_trace(**kwargs: object) -> FakeTrace:
        trace = FakeTrace()
        traces.append(trace)
        return trace

    monkeypatch.setattr("backend.chat.service.begin_trace", _begin_trace)

    async def _fake_async_pipeline(*args, **kwargs):
        return _cp_make_pipeline_result(final_answer="Use the reset link in settings.")

    monkeypatch.setattr("backend.chat.service.async_run_chat_pipeline", _fake_async_pipeline)

    process_chat_message(tenant_id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert traces[-1].stamp() is None

    older = dt.datetime(2026, 9, 1, 8, 0, 0)
    newest = dt.datetime(2026, 9, 14, 3, 30, 0)
    for filename, updated_at in (("old.md", older), ("new.md", newest)):
        db_session.add(
            Document(
                tenant_id=tenant_id,
                filename=filename,
                file_type=DocumentType.markdown,
                status=DocumentStatus.ready,
                parsed_text="content",
                created_at=updated_at,
                updated_at=updated_at,
            )
        )
    db_session.commit()

    process_chat_message(tenant_id, "How do I reset my password?", uuid.uuid4(), db_session, api_key=api_key)
    assert traces[-1].stamp() == "2026-09-14T03:30:00Z"


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
    api_key = "sk-test"
    session_id = uuid.uuid4()

    async def _fake_async_pipeline(*args, **kwargs):
        return _cp_make_pipeline_result(
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
        api_key="sk-test",
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


def _cp_make_retrieval_context(*, reliability_score: str = "medium") -> RetrievalContext:
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


def _cp_make_pipeline_result(
    *,
    final_answer: str,
    reliability_score: str = "medium",
    is_reject: bool = False,
    reject_reason: str | None = None,
) -> ChatPipelineResult:
    retrieval = None if is_reject and reject_reason == "not_relevant" else _cp_make_retrieval_context(
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
