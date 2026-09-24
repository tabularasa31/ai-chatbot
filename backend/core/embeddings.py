"""Retry-wrapped, batched OpenAI embeddings helper."""

from __future__ import annotations

from openai import OpenAI

from backend.core.openai_retry import call_openai_with_retry


def embed_texts(
    texts: list[str],
    client: OpenAI,
    *,
    model: str,
    batch_size: int = 100,
) -> list[list[float]]:
    if not texts:
        return []
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = call_openai_with_retry(
            "embed_texts",
            lambda b=batch: client.embeddings.create(model=model, input=b),
        )
        vectors.extend(item.embedding for item in response.data)
    return vectors
