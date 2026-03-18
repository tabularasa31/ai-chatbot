"""Business logic for vector similarity search."""

from __future__ import annotations

import math
import uuid
from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import Document, Embedding

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def embed_query(query: str, *, api_key: str) -> list[float]:
    """
    Embed a search query using OpenAI embeddings API.

    Args:
        query: Text to embed.

    Returns:
        1536-dimensional embedding vector.
    """
    openai_client = get_openai_client(api_key)
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    return response.data[0].embedding


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.

    Args:
        vec1: First vector.
        vec2: Second vector.

    Returns:
        Similarity in [0, 1]. Returns 0 for zero vectors.
    """
    if len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    raw = dot / (norm1 * norm2)
    return max(0.0, min(1.0, raw))


def search_similar_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """
    Search for similar chunks by embedding the query and comparing to stored vectors.

    Loads all embeddings for the client, computes cosine similarity in Python,
    returns top_k results sorted by similarity descending.

    Args:
        client_id: Client ID for tenant isolation.
        query: Search query text.
        top_k: Number of top results to return.
        db: Database session.

    Returns:
        List of (embedding, similarity) tuples, sorted by similarity DESC.
    """
    query_vector = embed_query(query, api_key=api_key)

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        meta = emb.metadata_json or {}
        vector = meta.get("vector")
        if not vector or not isinstance(vector, list):
            continue
        sim = cosine_similarity(query_vector, vector)
        scored.append((emb, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
