"""Unit tests for LanguageGateStreamFilter (task 86ey7x2p6).

The gate holds back the head of a streamed answer until its language is
verified, so a wrong-language generation is aborted BEFORE anything reaches
the client — replacing the old post-hoc full regeneration that visibly swapped
the answer after streaming it.
"""

from __future__ import annotations

import pytest

from backend.chat.language import LanguageDetectionResult
from backend.chat.handlers.rag import (
    LanguageGateStreamFilter,
    LanguageMismatchStreamAbortError,
)


def _detection(lang: str, reliable: bool = True) -> LanguageDetectionResult:
    return LanguageDetectionResult(
        detected_language=lang, confidence=0.95 if reliable else 0.3, is_reliable=reliable
    )


@pytest.fixture()
def emitted() -> list[str]:
    return []


def test_passthrough_when_language_matches(
    emitted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.chat.handlers.rag.detect_language", lambda text: _detection("ru")
    )
    gate = LanguageGateStreamFilter(emitted.append, expected_language="ru", min_chars=10)

    gate.feed("Привет, ")  # below threshold — held back
    assert emitted == []
    gate.feed("вот ответ на ваш вопрос.")  # crosses threshold — verified + flushed
    assert emitted == ["Привет, вот ответ на ваш вопрос."]
    gate.feed(" Ещё текст.")  # passthrough mode: forwarded immediately
    assert emitted[-1] == " Ещё текст."
    gate.flush_end()  # no-op after passthrough
    assert len(emitted) == 2


def test_abort_on_reliable_mismatch_before_any_emit(
    emitted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.chat.handlers.rag.detect_language", lambda text: _detection("en")
    )
    gate = LanguageGateStreamFilter(emitted.append, expected_language="kk", min_chars=10)

    with pytest.raises(LanguageMismatchStreamAbortError) as excinfo:
        gate.feed("Sure — here is the answer to your question.")
    assert excinfo.value.detected_language == "en"
    assert emitted == [], "nothing may reach the client before the language check"


def test_short_answer_checked_on_flush_end(
    emitted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.chat.handlers.rag.detect_language", lambda text: _detection("en")
    )
    gate = LanguageGateStreamFilter(emitted.append, expected_language="kk", min_chars=500)

    gate.feed("Short answer.")  # never crosses the threshold
    assert emitted == []
    with pytest.raises(LanguageMismatchStreamAbortError):
        gate.flush_end()
    assert emitted == []


def test_short_matching_answer_flushed_on_flush_end(
    emitted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.chat.handlers.rag.detect_language", lambda text: _detection("en")
    )
    gate = LanguageGateStreamFilter(emitted.append, expected_language="en", min_chars=500)

    gate.feed("Short answer.")
    gate.flush_end()
    assert emitted == ["Short answer."]


def test_unreliable_detection_fails_open(
    emitted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.chat.handlers.rag.detect_language",
        lambda text: _detection("en", reliable=False),
    )
    gate = LanguageGateStreamFilter(emitted.append, expected_language="kk", min_chars=10)

    gate.feed("Ambiguous text that langdetect is unsure about")
    assert emitted == ["Ambiguous text that langdetect is unsure about"]


def test_language_root_comparison_tolerates_regional_tags(
    emitted: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "backend.chat.handlers.rag.detect_language", lambda text: _detection("pt-BR")
    )
    gate = LanguageGateStreamFilter(emitted.append, expected_language="pt", min_chars=10)

    gate.feed("Claro — aqui está a resposta para a sua pergunta.")
    assert emitted, "pt-BR answer must pass a pt expectation (root comparison)"
