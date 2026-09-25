"""Shared tokenization for text matching (search, reranking, gap analyzer, FAQ).

Unicode-aware `\\w+` over `casefold()` — no language-specific logic (no
per-language regexes, keyword lists, or script sniffing).
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)
_COMPOUND_RE = re.compile(r"\w+(?:[-\u2010\u2011]\w+)*", flags=re.UNICODE)
_COMPOUND_HYPHEN_RE = re.compile(r"[\u2010\u2011]")


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

    The typographic hyphen (U+2010) and non-breaking hyphen (U+2011) join
    compounds like the ASCII hyphen-minus does, and are normalized to "-" in
    the returned tokens so equivalent spellings compare equal.
    """
    tokens = _COMPOUND_RE.findall((text or "").casefold())
    return [_COMPOUND_HYPHEN_RE.sub("-", token) for token in tokens]
