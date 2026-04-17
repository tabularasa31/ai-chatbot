"""Pure math utilities shared across gap_analyzer modules.

No I/O, no SQLAlchemy, no side effects.

Canonical _tokenize returns list[str]. Callers that need set[str] wrap with set().
Note: orchestrator previously returned set[str] from _tokenize; its callers have been
updated to wrap in set() explicitly.
"""

from __future__ import annotations

import json
import math
import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*")


def _tokenize(value: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(value.casefold()) if token]


def _token_overlap(query_tokens: set[str], chunk_tokens: set[str]) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    return len(query_tokens & chunk_tokens) / len(query_tokens)


def _vector_from_unknown(raw: object) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [float(value) for value in raw]
    if isinstance(raw, tuple):
        return [float(value) for value in raw]
    if hasattr(raw, "tolist"):
        try:
            parsed = raw.tolist()
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [float(value) for value in parsed]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, list):
            return [float(value) for value in parsed]
    return None


def _vector_norm(vector: list[float] | None) -> float:
    if vector is None:
        return 0.0
    return math.sqrt(sum(value * value for value in vector))


def _cosine_similarity(
    first: list[float] | None,
    second: list[float] | None,
    *,
    first_norm: float,
    second_norm: float,
) -> float:
    if first is None or second is None or len(first) != len(second):
        return 0.0
    if first_norm == 0.0 or second_norm == 0.0:
        return 0.0
    dot = 0.0
    for left, right in zip(first, second, strict=True):
        dot += left * right
    return max(0.0, min(1.0, dot / (first_norm * second_norm)))
