"""Shared tokenization for text matching (search, reranking, gap analyzer, FAQ).

Unicode-aware `\\w+` over `casefold()` — no language-specific logic (no
per-language regexes, keyword lists, or script sniffing).
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


def word_tokens(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens, in order (duplicates kept)."""
    return _WORD_RE.findall((text or "").casefold())


def token_set(text: str) -> set[str]:
    """Tokenize text into a deduplicated set of lowercase word tokens."""
    return set(word_tokens(text))
