"""Pure math utilities shared across gap_analyzer modules.

No I/O, no SQLAlchemy, no side effects.

Canonical _tokenize returns list[str]. Callers that need set[str] wrap with set().
"""

from __future__ import annotations

import math

from backend.utils.math import coerce_vector as _vector_from_unknown  # noqa: F401
from backend.utils.math import cosine_similarity_with_norms as _cosine_similarity  # noqa: F401
from backend.utils.text import compound_tokens as _tokenize  # noqa: F401


def _vector_norm(vector: list[float] | None) -> float:
    if vector is None:
        return 0.0
    return math.sqrt(sum(value * value for value in vector))


