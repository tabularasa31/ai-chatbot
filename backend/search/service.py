"""Business logic for vector similarity search."""

from __future__ import annotations

import uuid

from rank_bm25 import BM25Okapi
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


def bm25_search_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    BM25 full-text search over chunk_text.
    Returns normalized scores in [0, 1].
    """
    query_tokens = query.lower().split()
    if not query_tokens:
        return []

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .filter(Embedding.chunk_text.isnot(None))
        .all()
    )
    if not embeddings:
        return []

    corpus = [(emb.chunk_text or "").lower().split() for emb in embeddings]
    bm25 = BM25Okapi(corpus)
    raw_scores = [float(s) for s in bm25.get_scores(query_tokens)]
    scored = list(zip(embeddings, raw_scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = scored[:top_k]
    if not scored:
        return []
    max_s = scored[0][1]
    min_s = scored[-1][1]
    if max_s == min_s:
        return [(emb, 1.0) for emb, _ in scored]
    return [(emb, (s - min_s) / (max_s - min_s)) for emb, s in scored]


def reciprocal_rank_fusion(
    vector_results: list[tuple[Embedding, float]],
    bm25_results: list[tuple[Embedding, float]],
    k: int = 60,
    top_k: int = 5,
) -> list[tuple[Embedding, float]]:
    """Combine vector and BM25 results using Reciprocal Rank Fusion."""
    scores: dict[uuid.UUID, float] = {}
    id_to_emb: dict[uuid.UUID, Embedding] = {}

    for rank, (emb, _) in enumerate(vector_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    for rank, (emb, _) in enumerate(bm25_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    sorted_ids = sorted(scores.keys(), key=lambda id_: scores[id_], reverse=True)
    return [(id_to_emb[id_], scores[id_]) for id_ in sorted_ids[:top_k]]


def _pgvector_search(
    client_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """Native pgvector cosine distance search. PostgreSQL only."""
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
        return [
            (emb, max(0.0, 1.0 - distance))
            for emb, distance in results_with_distance
        ]
    except Exception:
        return _python_cosine_search(client_id, query_vector, top_k, db)


def search_similar_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """
    Hybrid search: pgvector cosine similarity + BM25, merged with RRF.

    SQLite (tests): Python cosine only; pgvector and BM25 are skipped.
    """
    query_vector = embed_query(query, api_key=api_key)

    db_url = str(db.bind.url if db.bind else "")
    if "sqlite" in db_url:
        return _python_cosine_search(client_id, query_vector, top_k, db)

    vector_results = _pgvector_search(client_id, query_vector, top_k * 2, db)
    bm25_results = bm25_search_chunks(client_id, query, top_k * 2, db)

    if not vector_results and not bm25_results:
        return []
    if not bm25_results:
        return vector_results[:top_k]
    if not vector_results:
        return bm25_results[:top_k]

    return reciprocal_rank_fusion(vector_results, bm25_results, top_k=top_k)


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
