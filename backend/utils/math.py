from __future__ import annotations

import json
import math


def coerce_vector(raw: object) -> list[float] | None:
    """Convert an embedding stored in an unknown format to ``list[float] | None``.

    Handles the shapes an embedding column can come back as depending on
    dialect/driver: a native list/tuple, a numpy/pgvector array exposing
    ``tolist()``, or a JSON-encoded string (including SQLite, which stores
    pgvector columns as TEXT).
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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def cosine_similarity_with_norms(
    first: list[float] | None,
    second: list[float] | None,
    *,
    first_norm: float,
    second_norm: float,
) -> float:
    """Cosine similarity with pre-computed norms — avoids redundant sqrt in hot loops."""
    if first is None or second is None or len(first) != len(second):
        return 0.0
    if first_norm == 0.0 or second_norm == 0.0:
        return 0.0
    dot = 0.0
    for left, right in zip(first, second, strict=True):
        dot += left * right
    return max(0.0, min(1.0, dot / (first_norm * second_norm)))
