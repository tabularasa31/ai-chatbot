"""Tests for parse-time document language detection.

The detection lives in ``backend.documents.language_detection`` and is the
seed for :func:`detect_tenant_kb_script` / ``detect_tenant_kb_scripts`` in
``backend.search.service`` — those rely on accurate per-document language
labels instead of query-time chunk sampling.
"""

from __future__ import annotations

from backend.documents.language_detection import detect_document_language


def test_detects_english_text() -> None:
    text = (
        "The quick brown fox jumps over the lazy dog. "
        "Customers can reset their password via the account settings page. "
        "If two-factor authentication is enabled, a verification code is required."
    )
    assert detect_document_language(text) == "en"


def test_detects_russian_text() -> None:
    text = (
        "Сайт не открывается после подключения к CDN. "
        "Проверьте NS-пропагацию и статус SSL-сертификата. "
        "A-запись домена должна указывать на IP-адреса CDN."
    )
    assert detect_document_language(text) == "ru"


def test_returns_none_for_empty_text() -> None:
    assert detect_document_language(None) is None
    assert detect_document_language("") is None
    assert detect_document_language("   \n\t  ") is None


def test_returns_none_for_unreliable_input() -> None:
    # Single short token / pure punctuation — langdetect / heuristic mark it
    # as unreliable; we must not persist a guess.
    assert detect_document_language("???") is None


def test_truncates_long_input() -> None:
    """Detection should still work on huge documents (cap at 4096 chars internally)."""
    big = ("English documentation paragraph. " * 1000)
    assert detect_document_language(big) == "en"
