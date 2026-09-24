"""Shared, retry-wrapped OpenAI embeddings helper.

Batches ``texts`` into calls of at most ``batch_size`` and returns the
embedding vectors in the same order, one per input text (no filtering —
callers that need to drop blank/empty texts do so before calling in, so the
result stays index-aligned with their own bookkeeping).
"""

from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from backend.core.openai_client import get_async_openai_client, get_openai_client
from backend.core.openai_retry import async_call_openai_with_retry, call_openai_with_retry


def embed_texts(
    texts: list[str],
    api_key_or_client: str | OpenAI,
    *,
    model: str,
    batch_size: int = 100,
) -> list[list[float]]:
    """Embed ``texts`` via the OpenAI embeddings API, batched and retried.

    ``api_key_or_client`` accepts either an encrypted API key (a cached
    client is fetched via :func:`get_openai_client`) or an already-built
    ``OpenAI`` client, for callers that already hold one.
    """
    if not texts:
        return []
    client = (
        api_key_or_client
        if isinstance(api_key_or_client, OpenAI)
        else get_openai_client(api_key_or_client)
    )
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = call_openai_with_retry(
            "embed_texts",
            lambda b=batch: client.embeddings.create(model=model, input=b),
        )
        vectors.extend(item.embedding for item in response.data)
    return vectors


async def async_embed_texts(
    texts: list[str],
    api_key_or_client: str | AsyncOpenAI,
    *,
    model: str,
    batch_size: int = 100,
) -> list[list[float]]:
    """Async counterpart of :func:`embed_texts`."""
    if not texts:
        return []
    client = (
        api_key_or_client
        if isinstance(api_key_or_client, AsyncOpenAI)
        else get_async_openai_client(api_key_or_client)
    )
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]

        async def _call(b: list[str] = batch) -> object:
            return await client.embeddings.create(model=model, input=b)

        response = await async_call_openai_with_retry("embed_texts", _call)
        vectors.extend(item.embedding for item in response.data)
    return vectors
