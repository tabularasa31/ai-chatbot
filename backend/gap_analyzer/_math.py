"""Pure math utilities shared across gap_analyzer modules.

No I/O, no SQLAlchemy, no side effects.

Canonical _tokenize returns list[str]. Callers that need set[str] wrap with set().
"""

from __future__ import annotations

import json
import math

from backend.utils.math import cosine_similarity_with_norms as _cosine_similarity  # noqa: F401
from backend.utils.text import word_tokens as _tokenize  # noqa: F401


def _vector_from_unknown(raw: object) -> list[float] | None:
    """Convert an embedding stored in an unknown format to list[float] | None.

    Behaviour vs. original split implementations:
    - list/tuple with non-numeric elements: now returns None (caught by the unified
      try-block) instead of raising TypeError/ValueError at the call site.
    - tolist() raising something other than ValueError/TypeError (e.g. AttributeError
      on a broken __tolist__ implementation): now propagates, previously swallowed by
      bare `except Exception`. In practice tolist() on numpy/pgvector arrays only raises
      ValueError/TypeError, so this is safe for all DB-sourced embeddings.
    - json.JSONDecodeError is a subclass of ValueError, listed explicitly for clarity.
    """
    if raw is None:
        return None
    try:
        if isinstance(raw, (list, tuple)):
            return [float(value) for value in raw]
        if hasattr(raw, "tolist"):
            parsed = raw.tolist()
            if isinstance(parsed, list):
                return [float(value) for value in parsed]
        if isinstance(raw, str):
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [float(value) for value in parsed]
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return None


def _vector_norm(vector: list[float] | None) -> float:
    if vector is None:
        return 0.0
    return math.sqrt(sum(value * value for value in vector))


