"""Async query-embedding helpers backed by the in-process embedding cache."""

from __future__ import annotations

from typing import Any

from backend.core.config import settings
from backend.core.openai_client import get_async_openai_client
from backend.core.openai_retry import async_call_openai_with_retry
from backend.search import embedding_cache as _emb_cache


async def async_embed_query(
    query: str,
    *,
    api_key: str,
    timeout: float | None = None,
    max_attempts: int | None = None,
    langfuse_observation: Any | None = None,
) -> list[float]:
    """Embed a search query using the OpenAI embeddings API.

    Returns the cached vector when the query text was embedded before;
    otherwise makes one embeddings call and caches the result.
    """
    cached = _emb_cache.get(query)
    if cached is not None:
        return cached
    client = get_async_openai_client(api_key, timeout=timeout)
    response = await async_call_openai_with_retry(
        "search_embed_query",
        lambda: client.embeddings.create(
            model=settings.embedding_model,
            input=query,
        ),
        call_type="embedding",
        max_attempts=max_attempts,
        langfuse_observation=langfuse_observation,
    )
    vector = response.data[0].embedding
    _emb_cache.put(query, vector)
    return vector


async def async_embed_queries(
    queries: list[str],
    *,
    api_key: str,
    timeout: float | None = None,
    langfuse_observation: Any | None = None,
) -> list[list[float]]:
    """Embed multiple search queries in one OpenAI API round-trip.

    Vectors for texts already in the in-process cache are returned without
    an API call; only unique cache misses are sent to OpenAI as a single batch.
    """
    if not queries:
        return []
    cached_map: dict[str, list[float] | None] = {q: _emb_cache.get(q) for q in queries}
    misses = [q for q in queries if cached_map[q] is None]
    if not misses:
        return [cached_map[q] for q in queries]  # type: ignore[return-value]
    unique_misses = list(dict.fromkeys(misses))
    client = get_async_openai_client(api_key, timeout=timeout)
    response = await async_call_openai_with_retry(
        "search_embed_queries",
        lambda: client.embeddings.create(
            model=settings.embedding_model,
            input=unique_misses,
        ),
        call_type="embedding",
        langfuse_observation=langfuse_observation,
    )
    for text, item in zip(unique_misses, response.data, strict=True):
        _emb_cache.put(text, item.embedding)
        cached_map[text] = item.embedding
    return [cached_map[q] for q in queries]  # type: ignore[return-value]


async def async_embed_queries_with_stats(
    queries: list[str], *, api_key: str, timeout: float | None = None
) -> tuple[list[list[float]], int]:
    """Embed multiple queries and return the actual API request count used."""
    if not queries:
        return [], 0
    any_miss = any(_emb_cache.get(q) is None for q in queries)
    vectors = await async_embed_queries(queries, api_key=api_key, timeout=timeout)
    return vectors, (1 if any_miss else 0)
