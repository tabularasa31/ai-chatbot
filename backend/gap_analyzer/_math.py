"""Pure math utilities shared across gap_analyzer modules.

No I/O, no SQLAlchemy, no side effects.

Canonical _tokenize returns list[str]. Callers that need set[str] wrap with set().
Note: orchestrator previously returned set[str] from _tokenize; its callers have been
updated to wrap in set() explicitly.
"""

from __future__ import annotations

import math
import re

from backend.utils.math import coerce_vector as _vector_from_unknown  # noqa: F401
from backend.utils.math import cosine_similarity_with_norms as _cosine_similarity  # noqa: F401

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")


def _tokenize(value: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(value.casefold()) if token]


def _token_overlap(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _vector_norm(vector: list[float] | None) -> float:
    if vector is None:
        return 0.0
    return math.sqrt(sum(value * value for value in vector))


