from __future__ import annotations

import math


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
