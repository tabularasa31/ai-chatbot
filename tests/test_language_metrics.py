"""Verify language.* PostHog events fire from resolve_language_context and localize."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.chat.language import (
    LocalizationResult,
    _invoke_localize_llm,
    resolve_language_context,
)


@pytest.fixture
def captured_events(monkeypatch):
    events: list[dict] = []

    def fake_capture(event, **kwargs):
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("backend.chat.language.capture_event", fake_capture)
    return events


def _events_named(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e["event"] == name]


def test_resolve_language_context_emits_resolved(captured_events):
    resolve_language_context(
        current_turn_text="Bonjour",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        recent_user_turn_texts=["Bonjour"],
        tenant_id="tnt_test",
        bot_id="bot_test",
        chat_id="chat_test",
    )

    resolved = _events_named(captured_events, "language.resolved")
    assert len(resolved) == 1
    e = resolved[0]
    assert e["distinct_id"] == "bot_test"
    assert e["tenant_id"] == "tnt_test"
    assert e["bot_id"] == "bot_test"
    props = e["properties"]
    assert props["chat_id"] == "chat_test"
    assert props["language"] == "fr"
    assert props["detected"] == "fr"
    assert props["final"] == "fr"
    assert props["source"] == "detector"
    assert "confidence" in props
    assert props["text_length"] == len("Bonjour")


def test_resolve_language_context_emits_switched(captured_events):
    resolve_language_context(
        current_turn_text="最新消息",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        previous_response_language="en",
        recent_user_turn_texts=["最新消息", "你好"],
        tenant_id="tnt_test",
    )

    switched = _events_named(captured_events, "language.switched")
    assert len(switched) == 1
    props = switched[0]["properties"]
    assert props["from"] == "en"
    assert props["to"] == "zh"
    assert isinstance(props["window_weights"], dict)
    assert props["margin"] >= 0


def test_resolve_language_context_emits_detect_fallback(captured_events):
    resolve_language_context(
        current_turn_text="?",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        recent_user_turn_texts=["?", "ok", "."],
        tenant_id="tnt_test",
    )

    fallbacks = _events_named(captured_events, "language.detect_fallback")
    assert len(fallbacks) == 1
    assert fallbacks[0]["properties"]["reason"] == "detector_returned_unknown"


def test_resolve_language_context_distinct_id_falls_back_to_tenant(captured_events):
    resolve_language_context(
        current_turn_text="Bonjour",
        is_bootstrap_turn=False,
        bootstrap_user_locale=None,
        browser_locale=None,
        tenant_escalation_language=None,
        recent_user_turn_texts=["Bonjour"],
        tenant_id="tnt_only",
    )

    resolved = _events_named(captured_events, "language.resolved")
    assert resolved[0]["distinct_id"] == "tnt_only"


def test_invoke_localize_llm_emits_localized(captured_events):
    fake_response = type(
        "R",
        (),
        {
            "usage": type("U", (), {"total_tokens": 42})(),
            "choices": [
                type(
                    "C",
                    (),
                    {"message": type("M", (), {"content": "Bonjour"})()},
                )()
            ],
        },
    )()

    with patch("backend.chat.language.get_openai_client", return_value=object()), patch(
        "backend.chat.language.call_openai_with_retry", return_value=fake_response
    ):
        result = _invoke_localize_llm(
            canonical_text="Hello there",
            target_language="fr",
            api_key="sk-test",
            operation="localize",
            tenant_id="tnt_test",
            bot_id="bot_test",
            chat_id="chat_test",
        )

    assert isinstance(result, LocalizationResult)
    localized = _events_named(captured_events, "language.localized")
    assert len(localized) == 1
    props = localized[0]["properties"]
    assert props["target_lang"] == "fr"
    assert props["input_chars"] == len("Hello there")
    assert props["output_chars"] == len("Bonjour")
    assert isinstance(props["latency_ms"], int)
    assert props["latency_ms"] >= 0
    assert props["operation"] == "localize"
    assert props["chat_id"] == "chat_test"


def test_invoke_localize_llm_skips_emit_when_no_identifiers(captured_events):
    fake_response = type(
        "R",
        (),
        {
            "usage": type("U", (), {"total_tokens": 1})(),
            "choices": [
                type("C", (), {"message": type("M", (), {"content": "Bonjour"})()})()
            ],
        },
    )()

    with patch("backend.chat.language.get_openai_client", return_value=object()), patch(
        "backend.chat.language.call_openai_with_retry", return_value=fake_response
    ):
        result = _invoke_localize_llm(
            canonical_text="Hello",
            target_language="fr",
            api_key="sk-test",
            operation="localize",
        )

    assert result.text == "Bonjour"
    assert _events_named(captured_events, "language.localized") == []


def test_invoke_localize_llm_returns_output_when_capture_event_raises(monkeypatch):
    fake_response = type(
        "R",
        (),
        {
            "usage": type("U", (), {"total_tokens": 1})(),
            "choices": [
                type("C", (), {"message": type("M", (), {"content": "Bonjour"})()})()
            ],
        },
    )()

    def boom(*args, **kwargs):
        raise RuntimeError("posthog down")

    monkeypatch.setattr("backend.chat.language.capture_event", boom)

    with patch("backend.chat.language.get_openai_client", return_value=object()), patch(
        "backend.chat.language.call_openai_with_retry", return_value=fake_response
    ):
        result = _invoke_localize_llm(
            canonical_text="Hello",
            target_language="fr",
            api_key="sk-test",
            operation="localize",
            tenant_id="tnt_test",
        )

    # Telemetry failure must NOT discard the localized output.
    assert result.text == "Bonjour"


def test_invoke_localize_llm_does_not_emit_on_failure(captured_events):
    with patch(
        "backend.chat.language.get_openai_client",
        side_effect=RuntimeError("boom"),
    ):
        result = _invoke_localize_llm(
            canonical_text="Hello",
            target_language="fr",
            api_key="sk-test",
            operation="localize",
            tenant_id="tnt_test",
        )

    assert result.text == "Hello"
    assert _events_named(captured_events, "language.localized") == []
