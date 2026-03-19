"""Business logic for vector similarity search."""

from __future__ import annotations

import re
import uuid
from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import Document, Embedding

VECTOR_CONFIDENCE_THRESHOLD = 0.70  # cosine_similarity >= 0.70 → use vector mode
# Note: pgvector returns cosine_distance (1 - similarity), so threshold = 1 - 0.70 = 0.30
DISTANCE_THRESHOLD = 1.0 - VECTOR_CONFIDENCE_THRESHOLD  # 0.30

MIN_KEYWORD_LEN = 3
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def embed_query(query: str, *, api_key: str) -> list[float]:
    """
    Embed a search query using OpenAI embeddings API.

    Args:
        query: Text to embed.
        api_key: OpenAI API key.

    Returns:
        1536-dimensional embedding vector.
    """
    openai_client = get_openai_client(api_key)
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    return response.data[0].embedding


def _extract_keywords(query: str) -> list[str]:
    """Extract simple keywords: split by whitespace/punctuation, lowercase, filter short tokens."""
    tokens = re.split(r"\W+", query.lower())
    return [t for t in tokens if len(t) >= MIN_KEYWORD_LEN]


def keyword_search_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    Keyword-based fallback search over chunk_text.

    Loads all chunks for client, counts keyword matches in Python.
    Used as fallback when vector search confidence is low.

    Args:
        client_id: Client ID for tenant isolation (filter at DB level).
        query: Search query text.
        top_k: Number of top results to return.
        db: Database session.

    Returns:
        List of (embedding, match_count) tuples, sorted by match_count DESC.
    """
    keywords = _extract_keywords(query)
    if not keywords:
        return []

    # Load all chunks for this client (filtering at DB level)
    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        chunk_lower = (emb.chunk_text or "").lower()
        count = sum(1 for kw in keywords if kw in chunk_lower)
        if count > 0:
            scored.append((emb, float(count)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def search_similar_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """
    Search for similar chunks using native pgvector cosine distance.

    Uses pgvector <=> operator for fast vector search at DB level.
    Falls back to keyword search if no confident vector results found.

    Args:
        client_id: Client ID (mandatory filter for tenant isolation).
        query: Search query text.
        top_k: Number of top results to return.
        db: Database session.
        api_key: OpenAI API key.

    Returns:
        List of (embedding, similarity) tuples, sorted by similarity DESC.
    """
    query_vector = embed_query(query, api_key=api_key)

    # Check if we're in SQLite (tests) — pgvector not available
    db_url = str(db.bind.url) if db.bind else ""
    if "sqlite" in db_url:
        results = _python_cosine_search(client_id, query_vector, top_k, db)
        # Apply same confidence/keyword fallback as PostgreSQL path
        if not results:
            return keyword_search_chunks(client_id, query, top_k, db)
        best_similarity = results[0][1]
        if best_similarity < VECTOR_CONFIDENCE_THRESHOLD:
            keyword_results = keyword_search_chunks(client_id, query, top_k, db)
            return keyword_results if keyword_results else results
        return results

    # pgvector native search (PostgreSQL)
    try:
        distance_expr = Embedding.vector.cosine_distance(query_vector)
        results_with_distance = (
            db.query(Embedding, distance_expr.label("distance"))
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.client_id == client_id)
            .filter(Embedding.vector.isnot(None))
            .order_by(distance_expr)
            .limit(top_k)
            .all()
        )
    except Exception:
        # Fallback to Python search if pgvector fails (e.g., extension not installed)
        results = _python_cosine_search(client_id, query_vector, top_k, db)
        if not results:
            return keyword_search_chunks(client_id, query, top_k, db)
        best_similarity = results[0][1]
        if best_similarity < VECTOR_CONFIDENCE_THRESHOLD:
            keyword_results = keyword_search_chunks(client_id, query, top_k, db)
            return keyword_results if keyword_results else results
        return results

    if not results_with_distance:
        # No vector results → try keyword search
        return keyword_search_chunks(client_id, query, top_k, db)

    # Convert distance to similarity (cosine_distance = 1 - cosine_similarity)
    results: list[tuple[Embedding, float]] = [
        (emb, max(0.0, 1.0 - distance))
        for emb, distance in results_with_distance
    ]

    # Check confidence: if best result is not confident, use keyword fallback
    best_similarity = results[0][1] if results else 0.0
    if best_similarity < VECTOR_CONFIDENCE_THRESHOLD:
        keyword_results = keyword_search_chunks(client_id, query, top_k, db)
        return keyword_results if keyword_results else results

    return results


def _python_cosine_search(
    client_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    Fallback: Python-based cosine similarity search.

    Used for SQLite (tests) or when pgvector is not available.
    Not recommended for production with large datasets.

    Args:
        client_id: Client ID for filtering.
        query_vector: Pre-computed query embedding.
        top_k: Number of results.
        db: Database session.
    """
    import math

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        # Try Vector column first, fall back to metadata_json["vector"]
        vector = None
        if emb.vector is not None:
            vector = list(emb.vector)
        else:
            meta = emb.metadata_json or {}
            vector = meta.get("vector")

        if not vector or not isinstance(vector, list):
            continue

        # Cosine similarity
        if len(vector) != len(query_vector):
            continue
        dot = sum(a * b for a, b in zip(query_vector, vector))
        norm1 = math.sqrt(sum(a * a for a in query_vector))
        norm2 = math.sqrt(sum(b * b for b in vector))
        if norm1 == 0 or norm2 == 0:
            continue
        sim = max(0.0, min(1.0, dot / (norm1 * norm2)))
        scored.append((emb, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# Keep for backward compatibility (used in chat/service.py)
def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    Kept for backward compatibility. Prefer pgvector native search.
    """
    import math

    if len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))
