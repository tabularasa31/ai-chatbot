"""Business logic for vector similarity search."""

from __future__ import annotations

import logging
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
SCRIPT_BOOST_FACTOR = 0.1
MMR_LAMBDA = 0.7
CYRILLIC_LANGUAGE_PREFIXES = ("ru", "uk", "bg", "sr", "mk", "be")
LATIN_LANGUAGE_PREFIXES = ("en", "es", "fr", "de", "it", "pt", "tr", "nl")

logger = logging.getLogger(__name__)


@dataclass
class SearchResultBundle:
    """Ranked retrieval results plus raw signals used for confidence decisions."""

    results: list[tuple[Embedding, float]]
    best_vector_similarity: float | None = None
    best_keyword_score: float | None = None
    query_variants: list[str] | None = None
    query_script_bucket: str | None = None
    conflicts_found: bool = False
    conflict_pairs: list[dict[str, object]] | None = None
    reliability_score: str | None = None
    reliability_score_cap: str | None = None


@dataclass
class MMRSelectionResult:
    """MMR selection order plus separate debug metadata for observability."""

    results: list[tuple[Embedding, float]]
    replacements: list[dict[str, object]]
    diagnostics: list[dict[str, object]]


def detect_query_script_bucket(text: str) -> str:
    """Detect a coarse script bucket from the query text."""
    if re.search(r"[а-яё]", text.casefold(), flags=re.UNICODE):
        return "cyrillic"
    if re.search(r"[a-z]", text.casefold(), flags=re.UNICODE):
        return "latin"
    return "other"


def detect_query_language(text: str) -> str:
    """Backward-compatible alias for coarse script bucket detection."""
    return detect_query_script_bucket(text)


def _embedding_script_bucket(embedding: Embedding) -> str:
    """Infer a coarse script bucket from embedding metadata or chunk text."""
    meta = embedding.metadata_json or {}
    language = meta.get("language")
    if isinstance(language, str) and language.strip():
        lowered = language.strip().lower()
        if lowered.startswith(CYRILLIC_LANGUAGE_PREFIXES):
            return "cyrillic"
        if lowered.startswith(LATIN_LANGUAGE_PREFIXES):
            return "latin"
    return detect_query_script_bucket(embedding.chunk_text or "")


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


def embed_queries(queries: list[str], *, api_key: str) -> list[list[float]]:
    """Embed multiple search queries in one OpenAI API round-trip."""
    if not queries:
        return []
    openai_client = get_openai_client(api_key)
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=queries,
    )
    return [item.embedding for item in response.data]


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

    max_rrf = max(score for _, score in candidates)
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


def apply_script_boost(
    query_script_bucket: str,
    candidates: list[tuple[Embedding, float]],
    *,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Soft-boost chunks that match the query script bucket."""
    boosted: list[tuple[Embedding, float]] = []
    for embedding, score in candidates:
        adjusted = score + (
            SCRIPT_BOOST_FACTOR
            if _embedding_script_bucket(embedding) == query_script_bucket
            else 0.0
        )
        boosted.append((embedding, round(adjusted, 6)))
    boosted.sort(key=lambda item: item[1], reverse=True)
    return boosted[:top_k]


def apply_language_boost(
    query_language: str,
    candidates: list[tuple[Embedding, float]],
    *,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Backward-compatible alias for coarse script-bucket boosting."""
    return apply_script_boost(
        query_script_bucket=query_language,
        candidates=candidates,
        top_k=top_k,
    )


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def _candidate_similarity(first: Embedding, second: Embedding) -> float:
    """Approximate chunk similarity using Jaccard overlap."""
    first_tokens = _token_set(first.chunk_text or "")
    second_tokens = _token_set(second.chunk_text or "")
    if not first_tokens or not second_tokens:
        return 0.0
    union = first_tokens | second_tokens
    return len(first_tokens & second_tokens) / len(union)


def mmr_select(
    candidates: list[tuple[Embedding, float]],
    *,
    top_k: int,
    lambda_mult: float = MMR_LAMBDA,
) -> MMRSelectionResult:
    """Select top-k diverse chunks while preserving comparable output scores."""
    if not candidates:
        return MMRSelectionResult(results=[], replacements=[], diagnostics=[])
    if len(candidates) < top_k:
        logger.warning(
            "MMR received fewer candidates than requested top_k",
            extra={"candidate_count": len(candidates), "top_k": top_k},
        )

    selected: list[tuple[Embedding, float]] = []
    selected_ids: set[uuid.UUID] = set()
    replacements: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    baseline_top_ids = {embedding.id for embedding, _ in candidates[:top_k]}
    baseline_top_order = [embedding.id for embedding, _ in candidates[:top_k]]
    baseline_top_map = {embedding.id: embedding for embedding, _ in candidates[:top_k]}
    displaced_baseline_ids: set[uuid.UUID] = set()
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        if not selected:
            chosen = remaining.pop(0)
            selected.append(chosen)
            selected_ids.add(chosen[0].id)
            diagnostics.append(
                {
                    "selected_chunk_id": str(chosen[0].id),
                    "selected_rank": 1,
                    "base_score": round(chosen[1], 6),
                    "mmr_score": round(chosen[1], 6),
                    "redundancy_penalty": 0.0,
                }
            )
            continue

        best_index = 0
        best_score = float("-inf")
        best_similarity = 0.0
        for index, (embedding, relevance) in enumerate(remaining):
            similarity = max(
                _candidate_similarity(embedding, chosen_embedding)
                for chosen_embedding, _ in selected
            )
            mmr_score = (lambda_mult * relevance) - ((1 - lambda_mult) * similarity)
            if mmr_score > best_score:
                best_score = mmr_score
                best_index = index
                best_similarity = similarity

        chosen = remaining.pop(best_index)
        selected_snapshot = list(selected)
        selected.append((chosen[0], round(chosen[1], 6)))
        selected_ids.add(chosen[0].id)
        diagnostics.append(
            {
                "selected_chunk_id": str(chosen[0].id),
                "selected_rank": len(selected),
                "base_score": round(chosen[1], 6),
                "mmr_score": round(best_score, 6),
                "redundancy_penalty": round(best_similarity, 6),
            }
        )

        if chosen[0].id not in baseline_top_ids:
            for baseline_id in baseline_top_order:
                if baseline_id not in selected_ids and baseline_id not in displaced_baseline_ids:
                    removed_embedding = baseline_top_map[baseline_id]
                    removed_similarity = max(
                        _candidate_similarity(removed_embedding, selected_embedding)
                        for selected_embedding, _ in selected_snapshot
                    )
                    displaced_baseline_ids.add(baseline_id)
                    replacements.append(
                        {
                            "removed_chunk_id": str(baseline_id),
                            "replacement_chunk_id": str(chosen[0].id),
                            "reason": f"removed_baseline_redundancy:{removed_similarity:.3f}",
                            "removed_redundancy": round(removed_similarity, 6),
                            "replacement_redundancy": round(best_similarity, 6),
                        }
                    )
                    break

    return MMRSelectionResult(
        results=selected,
        replacements=replacements,
        diagnostics=diagnostics,
    )


def detect_conflicts(
    candidates: list[tuple[Embedding, float]],
    *,
    similarity_threshold: float = 0.75,
) -> tuple[bool, list[dict[str, object]], str | None]:
    """Flag likely-conflicting chunks based on high lexical overlap and different sources."""
    conflict_pairs: list[dict[str, object]] = []
    for index, (first, _) in enumerate(candidates):
        for second, _ in candidates[index + 1 :]:
            similarity = _candidate_similarity(first, second)
            if similarity < similarity_threshold:
                continue
            if first.document_id == second.document_id:
                continue
            conflict_pairs.append(
                {
                    "chunk_a_id": str(first.id),
                    "chunk_b_id": str(second.id),
                    "similarity": round(similarity, 4),
                    "confirmed_by_llm": False,
                }
            )
    return bool(conflict_pairs), conflict_pairs, ("medium" if conflict_pairs else None)


def compute_reliability_score(
    *,
    top_score: float | None,
    conflicts_found: bool,
    result_count: int,
) -> str:
    """Compute a coarse reliability bucket for the final answer trace."""
    if result_count == 0 or top_score is None:
        return "low"
    if conflicts_found:
        return "medium"
    if top_score >= 0.8:
        return "high"
    if top_score >= 0.45:
        return "medium"
    return "low"


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
        logger.exception("pgvector search failed; falling back to Python cosine search")
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

    variant_vectors = embed_queries(query_variants, api_key=api_key)
    query_vector = variant_vectors[0]
    query_script_bucket = detect_query_script_bucket(query)

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
            query_script_bucket=query_script_bucket,
            reliability_score=compute_reliability_score(
                top_score=results[0][1] if results else None,
                conflicts_found=False,
                result_count=len(results),
            ),
        )

    # Fetch a wider candidate pool via the HNSW index so BM25 has enough coverage.
    # BM25 then re-ranks only these candidates — no separate full-table scan.
    vector_started_at = perf_counter()
    vector_candidate_map: dict[uuid.UUID, tuple[Embedding, float]] = {}
    for variant, variant_vector in zip(query_variants, variant_vectors):
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
        return SearchResultBundle(
            results=[],
            query_variants=query_variants,
            query_script_bucket=query_script_bucket,
            reliability_score="low",
        )

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

    rrf_candidate_pool = top_k * RRF_CANDIDATE_POOL_MULTIPLIER
    bm25_started_at = perf_counter()
    bm25_results = _bm25_score_candidates(vector_embs, query, rrf_candidate_pool)
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={"query": query, "tenant_id": str(client_id), "top_k": rrf_candidate_pool},
        ).end(
            output={
                "chunks": format_embedding_results(bm25_results, score_name="bm25_score"),
                "duration_ms": bm25_duration_ms,
            }
        )
    vector_for_rrf = vector_candidates[:rrf_candidate_pool]
    best_vector_similarity = vector_candidates[0][1] if vector_candidates else None
    best_keyword_score = bm25_results[0][1] if bm25_results else None

    if not bm25_results:
        return SearchResultBundle(
            results=vector_for_rrf[:top_k],
            best_vector_similarity=best_vector_similarity,
            best_keyword_score=best_keyword_score,
            query_variants=query_variants,
            query_script_bucket=query_script_bucket,
            reliability_score=compute_reliability_score(
                top_score=vector_for_rrf[0][1] if vector_for_rrf else None,
                conflicts_found=False,
                result_count=len(vector_for_rrf[:top_k]),
            ),
        )

    rrf_started_at = perf_counter()
    fused_results = reciprocal_rank_fusion(
        vector_for_rrf,
        bm25_results,
        top_k=rrf_candidate_pool,
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

    language_started_at = perf_counter()
    script_boosted_results = apply_script_boost(
        query_script_bucket,
        reranked_results,
        top_k=top_k * 2,
    )
    if trace is not None:
        trace.span(
            name="script-boost",
            input={
                "query_script_bucket": query_script_bucket,
                "candidate_count": len(reranked_results),
                "strategy": "coarse-script-bucket-heuristic",
            },
        ).end(
            output={
                "reordered": format_embedding_results(
                    script_boosted_results[:top_k],
                    score_name="script_boost_score",
                ),
                "duration_ms": round((perf_counter() - language_started_at) * 1000, 2),
            }
        )

    mmr_started_at = perf_counter()
    mmr_selection = mmr_select(
        script_boosted_results,
        top_k=top_k,
    )
    final_results = mmr_selection.results
    if trace is not None:
        trace.span(
            name="mmr-pass",
            input={
                "lambda": MMR_LAMBDA,
                "candidate_count": len(script_boosted_results),
                "selection_strategy": "mmr-order-base-score-output",
            },
        ).end(
            output={
                "final_chunks": format_embedding_results(
                    final_results,
                    score_name="final_score",
                ),
                "selection_diagnostics": mmr_selection.diagnostics,
                "replacements": mmr_selection.replacements,
                "duration_ms": round((perf_counter() - mmr_started_at) * 1000, 2),
            }
        )

    conflict_started_at = perf_counter()
    conflicts_found, conflict_pairs, reliability_score_cap = detect_conflicts(final_results)
    if trace is not None:
        trace.span(
            name="conflict-detection",
            input={
                "candidate_count": len(final_results),
            },
        ).end(
            output={
                "conflicts_found": conflicts_found,
                "conflict_pairs": conflict_pairs,
                "reliability_score_cap": reliability_score_cap,
                "duration_ms": round((perf_counter() - conflict_started_at) * 1000, 2),
            }
        )

    reliability_score = compute_reliability_score(
        top_score=final_results[0][1] if final_results else None,
        conflicts_found=conflicts_found,
        result_count=len(final_results),
    )

    return SearchResultBundle(
        results=final_results,
        best_vector_similarity=best_vector_similarity,
        best_keyword_score=best_keyword_score,
        query_variants=query_variants,
        query_script_bucket=query_script_bucket,
        conflicts_found=conflicts_found,
        conflict_pairs=conflict_pairs,
        reliability_score=reliability_score,
        reliability_score_cap=reliability_score_cap,
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
