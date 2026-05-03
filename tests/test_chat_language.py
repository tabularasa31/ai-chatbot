"""Tests for language detection, localization, reject_response, and related utilities."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import backend.chat.language as language_module
from backend.chat.language import (
    LanguageDetectionResult,
    LangDetectError,
    LocalizationResult,
    detect_language,
    localize_text_result,
    localize_text_to_language_result,
    localize_text_to_question_language_result,
    render_direct_faq_answer_result,
    resolve_language_context,
)
from backend.chat.service import (
    _resolve_fallback_locale,
)
from backend.core.config import Settings, settings
from backend.guards.reject_response import RejectReason, build_reject_response


# ---------------------------------------------------------------------------
# build_reject_response — new formulations
# ---------------------------------------------------------------------------


def test_build_reject_response_not_relevant_no_profile() -> None:
    text = build_reject_response(reason=RejectReason.NOT_RELEVANT, profile=None)
    assert "Sorry" in text
    assert "this product" in text
    assert "Я отвечаю только" not in text


def test_build_reject_response_not_relevant_with_product_name() -> None:
    profile = Mock()
    profile.product_name = "WidgetPro"
    profile.topics = []
    text = build_reject_response(reason=RejectReason.NOT_RELEVANT, profile=profile)
    assert "WidgetPro" in text
    assert "Sorry" in text
    assert "Я отвечаю только" not in text


def test_build_reject_response_not_relevant_with_topic_hint() -> None:
    profile = Mock()
    profile.product_name = "WidgetPro"
    profile.topics = ["API", "Billing", "Auth"]
    text = build_reject_response(reason=RejectReason.NOT_RELEVANT, profile=profile)
    assert "API" in text or "Billing" in text
    assert "WidgetPro" in text


def test_build_reject_response_injection_detected() -> None:
    text = build_reject_response(reason=RejectReason.INJECTION_DETECTED, profile=None)
    assert "Sorry" in text
    assert "Я не могу выполнить" not in text


def test_build_reject_response_localizes_to_question_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.guards.reject_response.localize_text_to_language",
        lambda **kwargs: "Je ne peux pas aider avec cette demande.",
    )
    text = build_reject_response(
        reason=RejectReason.INJECTION_DETECTED,
        profile=None,
        fallback_locale="fr-FR",
        api_key="sk-test",
    )
    assert text == "Je ne peux pas aider avec cette demande."


def test_build_reject_response_insufficient_confidence_no_profile() -> None:
    text = build_reject_response(reason=RejectReason.INSUFFICIENT_CONFIDENCE, profile=None)
    assert "don't have enough information" in text
    assert "clarify your question" in text


def test_build_reject_response_insufficient_confidence_with_hint() -> None:
    profile = Mock()
    profile.product_name = "WidgetPro"
    profile.topics = ["Webhooks", "Auth"]
    text = build_reject_response(reason=RejectReason.INSUFFICIENT_CONFIDENCE, profile=profile)
    assert "don't have enough information" in text
    assert "Webhooks" in text or "Auth" in text


def test_build_reject_response_uses_canonical_english_without_question() -> None:
    text = build_reject_response(
        reason=RejectReason.NOT_RELEVANT,
        profile=None,
    )
    assert "Sorry" in text
    assert "this product" in text


# ---------------------------------------------------------------------------
# localize_text_to_language_result
# ---------------------------------------------------------------------------


def test_localize_to_language_falls_back_to_locale_hint_when_target_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs: object) -> Mock:
                    nonlocal captured_messages
                    captured_messages = kwargs["messages"]  # type: ignore[assignment]
                    return Mock(
                        choices=[Mock(message=Mock(content="Bonjour"))],
                        usage=Mock(total_tokens=21),
                    )

    monkeypatch.setattr(
        "backend.chat.language.get_openai_client",
        lambda _api_key: FakeClient(),
    )

    result = localize_text_to_language_result(
        canonical_text="Hello",
        target_language=None,
        api_key="sk-test",
        fallback_locale="fr-FR",
    )

    assert result == LocalizationResult(text="Bonjour", tokens_used=21)
    assert captured_messages[0]["content"].endswith("strictly in fr-FR. Preserve meaning, tone, product names, module names, placeholders, quoted config keys, commands, code snippets, links, and ticket tokens exactly. Return only the localized assistant message.")
    assert captured_messages[1]["content"] == "Assistant message to localize:\nHello"


def test_localize_to_language_uses_resolved_language_without_question_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_messages: list[dict[str, str]] = []

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs: object) -> Mock:
                    nonlocal captured_messages
                    captured_messages = kwargs["messages"]  # type: ignore[assignment]
                    return Mock(
                        choices=[Mock(message=Mock(content="Bonjour"))],
                        usage=Mock(total_tokens=21),
                    )

    monkeypatch.setattr(
        "backend.chat.language.get_openai_client",
        lambda _api_key: FakeClient(),
    )

    localize_text_to_language_result(
        canonical_text="Hello",
        target_language="ru",
        api_key="sk-test",
        fallback_locale=None,
    )

    assert "same language as the user's question" not in captured_messages[0]["content"]
    assert "PWNED" not in captured_messages[0]["content"]
    assert "PWNED" not in captured_messages[1]["content"]
    assert captured_messages[1]["content"] == "Assistant message to localize:\nHello"


def test_localize_to_language_short_circuits_for_english_target(
    mock_openai_client: Mock,
) -> None:
    result = localize_text_to_language_result(
        canonical_text="Hello",
        target_language="en-US",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Hello", tokens_used=0)
    mock_openai_client.chat.completions.create.assert_not_called()


def test_deprecated_shim_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("fr", 0.99, True),
    )
    monkeypatch.setattr(
        "backend.chat.language.localize_text_to_language_result",
        lambda **kwargs: LocalizationResult(text="Bonjour", tokens_used=5),
    )

    with pytest.warns(DeprecationWarning):
        result = localize_text_to_question_language_result(
            canonical_text="Hello",
            question="bonjour",
            api_key="sk-test",
            fallback_locale="de",
        )

    assert result == LocalizationResult(text="Bonjour", tokens_used=5)


def test_localize_skips_when_text_already_in_target_ru(
    monkeypatch: pytest.MonkeyPatch,
    mock_openai_client: Mock,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("ru", 0.99, True),
    )

    result = localize_text_result(
        canonical_text="Привет, мир",
        response_language="ru",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Привет, мир", tokens_used=0)
    mock_openai_client.chat.completions.create.assert_not_called()


def test_localize_still_calls_llm_when_language_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs: object) -> Mock:
                    captured.update(kwargs)
                    return Mock(
                        choices=[Mock(message=Mock(content="Bonjour"))],
                        usage=Mock(total_tokens=7),
                    )

    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("unknown", 0.0, False),
    )
    monkeypatch.setattr(
        "backend.chat.language.get_openai_client",
        lambda _api_key: FakeClient(),
    )

    result = localize_text_result(
        canonical_text="abc",
        response_language="fr",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Bonjour", tokens_used=7)
    assert captured["model"] == settings.localization_model


def test_translate_skips_when_already_in_target(
    monkeypatch: pytest.MonkeyPatch,
    mock_openai_client: Mock,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("fr", 0.99, True),
    )

    result = language_module.translate_text_result(
        source_text="Bonjour",
        target_language="fr-FR",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Bonjour", tokens_used=0)
    mock_openai_client.chat.completions.create.assert_not_called()


def test_localize_skips_when_detection_confident_but_target_is_root_match(
    monkeypatch: pytest.MonkeyPatch,
    mock_openai_client: Mock,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("zh", 0.95, True),
    )

    result = localize_text_result(
        canonical_text="你好",
        response_language="zh-Hant",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="你好", tokens_used=0)
    mock_openai_client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_fallback_locale
# ---------------------------------------------------------------------------


def test_resolve_fallback_locale_prefers_kyc_then_browser_locale() -> None:
    assert (
        _resolve_fallback_locale(
            {"locale": "fr-FR", "browser_locale": "de-DE"},
            "en-US",
        )
        == "fr-FR"
    )
    assert _resolve_fallback_locale({"browser_locale": "de-DE"}, "en-US") == "de-DE"
    assert _resolve_fallback_locale({}, "en-US") == "en-US"
    assert _resolve_fallback_locale({}, None) is None


# ---------------------------------------------------------------------------
# resolve_language_context
# ---------------------------------------------------------------------------


def test_resolve_language_context_bootstrap_sets_unknown_detection() -> None:
    context = resolve_language_context(
        current_turn_text="",
        is_bootstrap_turn=True,
        bootstrap_user_locale="fr-FR",
        browser_locale="de-DE",
        tenant_escalation_language="es",
    )

    assert context.detected_language == "unknown"
    assert context.confidence == 0.0
    assert context.is_reliable is False
    assert context.response_language == "fr-FR"
    assert context.response_language_resolution_reason == "bootstrap_user_locale"
    assert context.escalation_language == "es"
    assert context.escalation_language_source == "tenant"


def test_resolve_language_context_detector_failure_returns_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When detect_language raises LangDetectError, resolver falls back to English."""
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: (_ for _ in ()).throw(LangDetectError(0, "detector unavailable")),
    )

    ctx = resolve_language_context(
        current_turn_text="bonjour",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
    )

    assert ctx.response_language == "en"
    assert ctx.response_language_resolution_reason == "detector_failure"
    assert ctx.detected_language == "unknown"
    assert ctx.confidence == 0.0
    assert ctx.is_reliable is False


# ---------------------------------------------------------------------------
# render_direct_faq_answer_result
# ---------------------------------------------------------------------------


def test_render_direct_faq_answer_result_translates_when_detection_is_unreliable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult(
            detected_language="en",
            confidence=0.4,
            is_reliable=False,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.language.translate_text_result",
        lambda **kwargs: LocalizationResult(text="Bonjour", tokens_used=8),
    )

    result = render_direct_faq_answer_result(
        answer_text="Direct FAQ answer",
        response_language="fr",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Bonjour", tokens_used=8)


def test_render_direct_faq_answer_result_translates_when_detection_throws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "backend.chat.language.translate_text_result",
        lambda **kwargs: LocalizationResult(text="Bonjour", tokens_used=5),
    )

    result = render_direct_faq_answer_result(
        answer_text="Direct FAQ answer",
        response_language="fr",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Bonjour", tokens_used=5)


def test_render_direct_faq_answer_result_translates_when_detected_differs_from_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detection returns English reliably, but target is French → translate is called."""
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult(
            detected_language="en",
            confidence=0.99,
            is_reliable=True,
        ),
    )
    monkeypatch.setattr(
        "backend.chat.language.translate_text_result",
        lambda **kwargs: LocalizationResult(text="Direct FAQ answer", tokens_used=0),
    )

    result = render_direct_faq_answer_result(
        answer_text="Direct FAQ answer",
        response_language="fr",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Direct FAQ answer", tokens_used=0)


# ---------------------------------------------------------------------------
# detect_language — caching and ASCII non-English
# ---------------------------------------------------------------------------


def test_detect_language_is_cached_for_short_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_detect_langs(_text: str) -> list[Mock]:
        calls["count"] += 1
        return [Mock(lang="fr", prob=0.99)]

    monkeypatch.setattr("backend.chat.language.detect_langs", fake_detect_langs)

    first = detect_language("résumé monde")
    second = detect_language("résumé monde")

    assert first == second
    assert calls["count"] == 1


def test_detect_language_bypasses_cache_for_long_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_detect_langs(_text: str) -> list[Mock]:
        calls["count"] += 1
        return [Mock(lang="fr", prob=0.99)]

    monkeypatch.setattr("backend.chat.language.detect_langs", fake_detect_langs)
    long_text = "résumé " * 80

    detect_language(long_text)
    detect_language(long_text)

    assert calls["count"] == 2


def test_detect_language_cache_respects_whitespace_stripping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_detect_langs(_text: str) -> list[Mock]:
        calls["count"] += 1
        return [Mock(lang="fr", prob=0.99)]

    monkeypatch.setattr("backend.chat.language.detect_langs", fake_detect_langs)

    detect_language("résumé monde")
    detect_language(" résumé monde ")

    assert calls["count"] == 1


@pytest.mark.parametrize(
    "text, expected",
    [
        # Acceptance criteria from 86excmfke.
        ("Quiero hablar con un agente", "es"),
        ("Necesito hablar con un agente humano", "es"),
        ("Nein, ich brauche noch Hilfe", "de"),
        ("Non, j'ai encore besoin d'aide", "fr"),
        # Other realistic ASCII-only escalation phrases.
        ("Mi cuenta no funciona", "es"),
        ("Bitte helfen Sie mir", "de"),
        ("Je m'appelle Jean", "fr"),
        ("Mein Name ist Hans", "de"),
        # Inputs that overlap with English on case-folded short tokens — the
        # stop-word list deliberately excludes "i" / "me" / "am" / "was" / "do" /
        # "has" / "will" so that these still fall through to langdetect.
        ("Me siento muy mal hoy", "es"),
        ("I bambini sono qui", "it"),
    ],
)
def test_detect_language_handles_ascii_non_english_multitoken(text: str, expected: str) -> None:
    result = detect_language(text)
    assert result.detected_language == expected, (
        f"Expected {expected} for {text!r}, got {result.detected_language}"
    )
    assert result.is_reliable


@pytest.mark.parametrize(
    "text",
    [
        # Short ASCII English fragments where langdetect is known to mis-fire
        # ("Reset password" -> af, "I cannot login" -> it, "question about
        # product" -> fr). The heuristic protects these.
        "Reset password",
        "I cannot login",
        "question about product",
        "Need help",
        "pricing question",
        # 4+ token English with stop words — heuristic short-circuits because
        # of the positive English signal even though we now consider longer
        # ASCII text.
        "How do I get started",
        "Help me reset password",
        "Why is it broken",
        "Cannot access my account",
        "Reset my password please",
        # Tech English with no stop words but where langdetect wouldn't claim
        # a trusted non-English language confidently — heuristic keeps en.
        "API returns error code 500",
        "login screen broken after update",
    ],
)
def test_detect_language_protects_short_ascii_english(text: str) -> None:
    result = detect_language(text)
    assert result.detected_language == "en", (
        f"Expected en for {text!r}, got {result.detected_language}"
    )


# ---------------------------------------------------------------------------
# Token logging
# ---------------------------------------------------------------------------


def test_localize_logs_tokens_with_operation_label(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("unknown", 0.0, False),
    )

    with caplog.at_level("INFO"):
        result = localize_text_result(
            canonical_text="Hello",
            response_language="ru",
            api_key="sk-test",
        )

    assert result.tokens_used == 20
    records = [record for record in caplog.records if record.msg == "llm_tokens_used"]
    assert any(
        getattr(record, "operation", None) == "localize"
        and getattr(record, "target_language", None) == "ru"
        and getattr(record, "tokens", None) == 20
        for record in records
    )


def test_translate_logs_tokens_with_operation_label(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    mock_openai_client: Mock,
) -> None:
    monkeypatch.setattr(
        "backend.chat.language.detect_language",
        lambda _text: LanguageDetectionResult("unknown", 0.0, False),
    )
    mock_openai_client.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content="Bonjour"))],
        usage=Mock(total_tokens=8),
    )

    with caplog.at_level("INFO"):
        result = language_module.translate_text_result(
            source_text="Hello",
            target_language="fr",
            api_key="sk-test",
        )

    assert result.tokens_used == 8
    assert any(
        getattr(record, "operation", None) == "translate"
        and getattr(record, "target_language", None) == "fr"
        and getattr(record, "tokens", None) == 8
        for record in caplog.records
        if record.msg == "llm_tokens_used"
    )


def test_localization_model_overridden_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALIZATION_MODEL", "gpt-4o")
    fresh_settings = Settings()
    monkeypatch.setattr(language_module, "settings", fresh_settings)

    captured: dict[str, object] = {}

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs: object) -> Mock:
                    captured.update(kwargs)
                    return Mock(
                        choices=[Mock(message=Mock(content="Bonjour"))],
                        usage=Mock(total_tokens=21),
                    )

    monkeypatch.setattr(language_module, "get_openai_client", lambda _api_key: FakeClient())
    monkeypatch.setattr(
        language_module,
        "detect_language",
        lambda _text: LanguageDetectionResult("unknown", 0.0, False),
    )

    result = localize_text_result(
        canonical_text="Hello",
        response_language="fr",
        api_key="sk-test",
    )

    assert result == LocalizationResult(text="Bonjour", tokens_used=21)
    assert captured["model"] == "gpt-4o"
