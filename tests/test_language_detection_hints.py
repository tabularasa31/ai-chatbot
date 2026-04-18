"""Tests for the Latin-word hint suppression on single English-token inputs.

Rule: for single-token inputs, English hints (hello/pricing/thanks) are
suppressed because they are common cross-language words that cause false
positives in established conversations.  Non-English hints (bonjour/hola/…)
are retained even for single tokens because they are strong first-turn signals
and there is no sticky history to fall back on yet.
"""
from __future__ import annotations

import pytest

from backend.chat.language import (
    _detect_language_cached,
    detect_language,
)


@pytest.fixture(autouse=True)
def clear_cache():
    _detect_language_cached.cache_clear()
    yield
    _detect_language_cached.cache_clear()


# ---------------------------------------------------------------------------
# Single-word English hints must NOT fire (false-positive prevention)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["hello", "pricing", "thanks"])
def test_single_english_hint_word_not_forced(word: str):
    """Single English hint words must not produce a reliable signal via the hint path.

    These words appear in any language context (copied text, UI labels, debug
    output) and would cause false language flips in established conversations.
    The single-ASCII-token guard in _detect_language_uncached returns "unknown"
    for them regardless; the hint must not override that.
    """
    result = detect_language(word)
    assert not result.is_reliable or result.detected_language == "unknown"


# ---------------------------------------------------------------------------
# Single-word non-English hints MUST still fire (first-turn signal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word, expected_lang",
    [
        ("bonjour", "fr"),
        ("merci", "fr"),
        ("hola", "es"),
        ("gracias", "es"),
        ("hallo", "de"),
        ("guten", "de"),
        ("obrigado", "pt"),
    ],
)
def test_single_non_english_hint_word_detected(word: str, expected_lang: str):
    """Single non-English hint words must resolve to the correct language.

    On a first real turn with no sticky history these are the only cheap signal
    available; suppressing them would regress the user's language to English.
    """
    result = detect_language(word)
    assert result.detected_language == expected_lang
    assert result.is_reliable


# ---------------------------------------------------------------------------
# Multi-token inputs: all hints fire normally
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_lang",
    [
        ("hello world", "en"),
        ("merci beaucoup", "fr"),
        ("hola amigo", "es"),
        ("guten morgen", "de"),
    ],
)
def test_multi_token_hints_fire(text: str, expected_lang: str):
    """Multi-token inputs must trigger all hints, including English ones."""
    result = detect_language(text)
    assert result.detected_language == expected_lang
    assert result.is_reliable


# ---------------------------------------------------------------------------
# Very short tokens: no reliable signal (sticky safety)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["ok", "hi", "yes", "no"])
def test_short_single_word_unreliable(word: str):
    """Very short (≤3-char) words must yield no reliable signal.

    Callers with sticky logic cannot flip an established language on these;
    callers without history fall back safely to English.
    """
    result = detect_language(word)
    assert not result.is_reliable
