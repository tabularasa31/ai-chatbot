"""Tests for _heuristic_language_detection Latin-word hint suppression on single-token input.

Rule: _LATIN_WORD_HINTS only fire when len(tokens) >= 2.  A lone word like
"hello" or "bonjour" must not be forced to a language through the hint path —
it should fall back to "unknown" (relying on sticky or default English
resolution upstream).
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
# Single-word: hints must NOT fire
# ---------------------------------------------------------------------------


def test_single_word_hello_not_forced_en():
    """'hello' alone must not be forced to en via the hint path."""
    result = detect_language("hello")
    # The single-ASCII-token guard in _detect_language_uncached returns "unknown"
    # for all single-word ASCII inputs regardless; what we verify is that no
    # reliable English signal is asserted from the hint.
    assert not result.is_reliable or result.detected_language == "unknown"


def test_single_word_bonjour_not_forced_fr():
    """'bonjour' alone must not be forced to fr via the hint path."""
    result = detect_language("bonjour")
    assert not result.is_reliable or result.detected_language == "unknown"


def test_single_word_hola_not_forced_es():
    """'hola' alone must not be forced to es via the hint path."""
    result = detect_language("hola")
    assert not result.is_reliable or result.detected_language == "unknown"


def test_single_word_hallo_not_forced_de():
    """'hallo' alone must not be forced to de via the hint path."""
    result = detect_language("hallo")
    assert not result.is_reliable or result.detected_language == "unknown"


# ---------------------------------------------------------------------------
# Multi-word: hints SHOULD still fire
# ---------------------------------------------------------------------------


def test_two_token_hello_world_detected_en():
    """'hello world' (two tokens) should still trigger the en hint."""
    result = detect_language("hello world")
    assert result.detected_language == "en"
    assert result.is_reliable


def test_two_token_merci_beaucoup_detected_fr():
    """'merci beaucoup' (two tokens) should still trigger the fr hint."""
    result = detect_language("merci beaucoup")
    assert result.detected_language == "fr"
    assert result.is_reliable


def test_two_token_hola_amigo_detected_es():
    """'hola amigo' (two tokens) should still trigger the es hint."""
    result = detect_language("hola amigo")
    assert result.detected_language == "es"
    assert result.is_reliable


# ---------------------------------------------------------------------------
# Single-word short tokens: must produce no reliable signal (sticky safety)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["ok", "hi", "yes", "no"])
def test_short_single_word_unreliable(word: str):
    """Very short single words must yield no reliable detection signal.

    In a sticky-enabled caller, an unreliable result cannot override a
    previously established language, so Russian chats stay Russian even if
    the user types a brief English word.
    """
    result = detect_language(word)
    assert not result.is_reliable
