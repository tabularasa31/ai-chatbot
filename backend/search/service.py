"""Business logic for vector similarity search."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from time import perf_counter

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import Document, Embedding
from backend.observability import TraceHandle
from backend.observability.formatters import (
    format_embedding_results,
    format_query_embedding_preview,
)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# Number of vector candidates to pre-fetch before BM25 scoring.
# BM25 runs only on this pool (already in memory) — never queries all client chunks.
BM25_CANDIDATE_POOL = 200
RRF_CANDIDATE_POOL_MULTIPLIER = 4
RERANK_LEXICAL_WEIGHT = 0.35
RERANK_VECTOR_WEIGHT = 0.25
RERANK_BM25_WEIGHT = 0.20
RERANK_RRF_WEIGHT = 0.20


@dataclass
class SearchResultBundle:
    """Ranked retrieval results plus raw signals used for confidence decisions."""

    results: list[tuple[Embedding, float]]
    best_vector_similarity: float | None = None
    best_keyword_score: float | None = None
    query_variants: list[str] | None = None


def expand_query(query: str) -> list[str]:
    """Generate lightweight query variants without changing user intent."""
    variants: list[str] = []
    seen: set[str] = set()

    def _push(value: str) -> None:
        normalized = " ".join(value.split())
        if not normalized:
            return
        key = normalized.casefold()
        if key in seen:
            return
        seen.add(key)
        variants.append(normalized)

    _push(query)

    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    _push(cleaned)

    tokens = re.findall(r"\w+", query.casefold(), flags=re.UNICODE)
    if tokens:
        unique_tokens = list(dict.fromkeys(tokens))
        _push(" ".join(unique_tokens))

    return variants or [query]


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


def _bm25_score_candidates(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """
    BM25 scoring over a pre-loaded list of Embedding objects.
    No DB access — operates on objects already in memory.
    Returns normalized scores in [0, 1].
    """
    query_tokens = query.lower().split()
    if not query_tokens or not candidates:
        return []

    corpus = [(emb.chunk_text or "").lower().split() for emb in candidates]
    bm25 = BM25Okapi(corpus)
    raw_scores = [float(s) for s in bm25.get_scores(query_tokens)]
    scored = list(zip(candidates, raw_scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    scored = scored[:top_k]
    if not scored:
        return []
    max_s = scored[0][1]
    min_s = scored[-1][1]
    if max_s == min_s:
        return [(emb, 1.0) for emb, _ in scored]
    return [(emb, (s - min_s) / (max_s - min_s)) for emb, s in scored]


def bm25_search_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    BM25 full-text search over chunk_text for a client.
    Fetches all client chunks from DB, then delegates scoring to _bm25_score_candidates.
    Public API preserved for direct use and tests.
    """
    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .filter(Embedding.chunk_text.isnot(None))
        .all()
    )
    return _bm25_score_candidates(embeddings, query, top_k)


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


def _collect_score_map(results: list[tuple[Embedding, float]]) -> dict[uuid.UUID, float]:
    """Collect the strongest score per embedding id."""
    score_map: dict[uuid.UUID, float] = {}
    for embedding, score in results:
        existing = score_map.get(embedding.id)
        if existing is None or score > existing:
            score_map[embedding.id] = score
    return score_map


def _lexical_overlap_score(query: str, chunk_text: str) -> float:
    """Cheap lexical signal used as an interim reranker until a cross-encoder is added."""
    query_tokens = set(re.findall(r"\w+", query.casefold(), flags=re.UNICODE))
    if not query_tokens:
        return 0.0
    chunk_tokens = set(re.findall(r"\w+", (chunk_text or "").casefold(), flags=re.UNICODE))
    if not chunk_tokens:
        return 0.0
    overlap = len(query_tokens & chunk_tokens)
    return overlap / len(query_tokens)


def rerank_candidates(
    query: str,
    candidates: list[tuple[Embedding, float]],
    *,
    vector_scores: dict[uuid.UUID, float] | None = None,
    bm25_scores: dict[uuid.UUID, float] | None = None,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Apply an interim heuristic reranking stage over fused candidates."""
    if not candidates:
        return []

    max_rrf = max(score for _, score in candidates) or 1.0
    vector_scores = vector_scores or {}
    bm25_scores = bm25_scores or {}

    rescored: list[tuple[Embedding, float]] = []
    for embedding, rrf_score in candidates:
        lexical_score = _lexical_overlap_score(query, embedding.chunk_text or "")
        vector_score = vector_scores.get(embedding.id, 0.0)
        bm25_score = bm25_scores.get(embedding.id, 0.0)
        normalized_rrf = rrf_score / max_rrf if max_rrf else 0.0
        final_score = (
            (lexical_score * RERANK_LEXICAL_WEIGHT)
            + (vector_score * RERANK_VECTOR_WEIGHT)
            + (bm25_score * RERANK_BM25_WEIGHT)
            + (normalized_rrf * RERANK_RRF_WEIGHT)
        )
        rescored.append((embedding, round(final_score, 6)))

    rescored.sort(
        key=lambda item: (
            item[1],
            vector_scores.get(item[0].id, 0.0),
            bm25_scores.get(item[0].id, 0.0),
        ),
        reverse=True,
    )
    return rescored[:top_k]


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
    """Compatibility wrapper returning ranked results only."""
    return search_similar_chunks_detailed(
        client_id=client_id,
        query=query,
        top_k=top_k,
        db=db,
        api_key=api_key,
    ).results


def search_similar_chunks_detailed(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
    trace: TraceHandle | None = None,
) -> SearchResultBundle:
    """
    Hybrid search: pgvector cosine similarity + BM25, merged with RRF.

    SQLite (tests): Python cosine only; pgvector and BM25 are skipped.
    """
    query_variants = expand_query(query)
    if trace is not None:
        trace.span(
            name="query-expansion",
            input={"query": query},
        ).end(
            output={"variants": query_variants}
        )

    query_vector = embed_query(query, api_key=api_key)

    db_url = str(db.bind.url if db.bind else "")
    if "sqlite" in db_url:
        vector_started_at = perf_counter()
        results = _python_cosine_search(client_id, query_vector, top_k, db)
        if trace is not None:
            trace.span(
                name="vector-search",
                input={
                    "query_embedding": format_query_embedding_preview(query_vector),
                    "query_variants": query_variants,
                    "tenant_id": str(client_id),
                    "top_k": top_k,
                    "engine": "python-cosine",
                },
            ).end(
                output={
                    "chunks": format_embedding_results(results, score_name="similarity_score"),
                    "duration_ms": round((perf_counter() - vector_started_at) * 1000, 2),
                    "total_candidates_scanned": len(results),
                }
            )
        return SearchResultBundle(
            results=results,
            best_vector_similarity=results[0][1] if results else None,
            query_variants=query_variants,
        )

    # Fetch a wider candidate pool via the HNSW index so BM25 has enough coverage.
    # BM25 then re-ranks only these candidates — no separate full-table scan.
    vector_started_at = perf_counter()
    vector_candidate_map: dict[uuid.UUID, tuple[Embedding, float]] = {}
    for variant in query_variants:
        variant_vector = query_vector if variant == query else embed_query(variant, api_key=api_key)
        for embedding, similarity in _pgvector_search(client_id, variant_vector, BM25_CANDIDATE_POOL, db):
            existing = vector_candidate_map.get(embedding.id)
            if existing is None or similarity > existing[1]:
                vector_candidate_map[embedding.id] = (embedding, similarity)
    vector_candidates = sorted(
        vector_candidate_map.values(),
        key=lambda item: item[1],
        reverse=True,
    )[:BM25_CANDIDATE_POOL]
    vector_duration_ms = round((perf_counter() - vector_started_at) * 1000, 2)

    if not vector_candidates:
        if trace is not None:
            trace.span(
                name="vector-search",
                input={
                    "query_embedding": format_query_embedding_preview(query_vector),
                    "query_variants": query_variants,
                    "tenant_id": str(client_id),
                    "top_k": BM25_CANDIDATE_POOL,
                    "engine": "pgvector",
                },
            ).end(
                output={
                    "chunks": [],
                    "duration_ms": vector_duration_ms,
                    "total_candidates_scanned": 0,
                }
            )
        return SearchResultBundle(results=[], query_variants=query_variants)

    vector_embs = [emb for emb, _ in vector_candidates]
    if trace is not None:
        trace.span(
            name="vector-search",
            input={
                "query_embedding": format_query_embedding_preview(query_vector),
                "query_variants": query_variants,
                "tenant_id": str(client_id),
                "top_k": BM25_CANDIDATE_POOL,
                "engine": "pgvector",
            },
        ).end(
            output={
                "chunks": format_embedding_results(
                    vector_candidates[:top_k * 2],
                    score_name="similarity_score",
                ),
                "duration_ms": vector_duration_ms,
                "total_candidates_scanned": len(vector_candidates),
                }
            )

    bm25_started_at = perf_counter()
    bm25_results = _bm25_score_candidates(vector_embs, query, top_k * 2)
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={"query": query, "tenant_id": str(client_id), "top_k": top_k * 2},
        ).end(
            output={
                "chunks": format_embedding_results(bm25_results, score_name="bm25_score"),
                "duration_ms": bm25_duration_ms,
            }
        )
    vector_for_rrf = vector_candidates[: max(top_k * RRF_CANDIDATE_POOL_MULTIPLIER, top_k * 2)]
    best_vector_similarity = vector_candidates[0][1] if vector_candidates else None
    best_keyword_score = bm25_results[0][1] if bm25_results else None

    if not bm25_results:
        return SearchResultBundle(
            results=vector_for_rrf[:top_k],
            best_vector_similarity=best_vector_similarity,
            best_keyword_score=best_keyword_score,
            query_variants=query_variants,
        )

    rrf_started_at = perf_counter()
    fused_results = reciprocal_rank_fusion(
        vector_for_rrf,
        bm25_results,
        top_k=max(top_k * RRF_CANDIDATE_POOL_MULTIPLIER, top_k),
    )
    rrf_duration_ms = round((perf_counter() - rrf_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="rrf-fusion",
            input={
                "vector_results": format_embedding_results(
                    vector_for_rrf,
                    score_name="similarity_score",
                ),
                "bm25_results": format_embedding_results(
                    bm25_results,
                    score_name="bm25_score",
                ),
            },
        ).end(
            output={
                "merged_chunks": format_embedding_results(
                    fused_results,
                    score_name="rrf_score",
                ),
                "duration_ms": rrf_duration_ms,
            }
        )

    rerank_started_at = perf_counter()
    reranked_results = rerank_candidates(
        query,
        fused_results,
        vector_scores=_collect_score_map(vector_candidates),
        bm25_scores=_collect_score_map(bm25_results),
        top_k=top_k,
    )
    if trace is not None:
        trace.span(
            name="reranking",
            input={
                "query": query,
                "candidate_count": len(fused_results),
                "model": "heuristic-rrf-v0",
            },
        ).end(
            output={
                "ranked": format_embedding_results(
                    reranked_results,
                    score_name="reranker_score",
                ),
                "top_score": reranked_results[0][1] if reranked_results else None,
                "duration_ms": round((perf_counter() - rerank_started_at) * 1000, 2),
            }
        )

    return SearchResultBundle(
        results=reranked_results,
        best_vector_similarity=best_vector_similarity,
        best_keyword_score=best_keyword_score,
        query_variants=query_variants,
    )


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
