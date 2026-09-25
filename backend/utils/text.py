"""Shared tokenization for text matching (search, reranking, gap analyzer, FAQ).

Unicode-aware `\\w+` over `casefold()` — no language-specific logic (no
per-language regexes, keyword lists, or script sniffing).
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)
_COMPOUND_RE = re.compile(r"\w+(?:-\w+)*", flags=re.UNICODE)


def word_tokens(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens, in order (duplicates kept)."""
    return _WORD_RE.findall((text or "").casefold())


def token_set(text: str) -> set[str]:
    """Tokenize text into a deduplicated set of lowercase word tokens."""
    return set(word_tokens(text))


def compound_tokens(text: str) -> list[str]:
    """Tokenize text into lowercase tokens, keeping hyphen-joined compounds whole.

    Used by the gap analyzer, where splitting "rate-limit" into two tokens can
    let a chunk satisfy the matched-terms threshold without covering the
    compound term. Not used by search/BM25/reranking/FAQ tokenization.
    """
    return _COMPOUND_RE.findall((text or "").casefold())
