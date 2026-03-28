"""Business logic for vector similarity search."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.models import Document, Embedding
from backend.observability import TraceHandle
from backend.observability.formatters import (
    format_embedding_results,
    format_query_embedding_preview,
    truncate_text,
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
MAX_OVERLAP_CHECK_CANDIDATES = 5
BM25_DEBUG_VARIANT_TEXT_MAX_LEN = 80

ReliabilityScore = Literal["low", "medium", "high"]
ReliabilityCapReason = Literal["source_overlap"]
VariantMode = Literal["single", "multi"]
BM25ExpansionMode = Literal["asymmetric", "symmetric_variants"]

logger = logging.getLogger(__name__)


@dataclass
class SearchResultBundle:
    """Ranked retrieval results plus raw signals used for confidence decisions."""

    results: list[tuple[Embedding, float]]
    best_vector_similarity: float | None = None
    best_keyword_score: float | None = None
    has_lexical_signal: bool = False
    query_variants: list[str] | None = None
    query_script_bucket: str | None = None
    conflicts_found: bool = False
    conflict_pairs: list[dict[str, object]] | None = None
    reliability_score: ReliabilityScore | None = None
    reliability_cap_reason: ReliabilityCapReason | None = None
    query_variant_count: int = 1
    variant_mode: VariantMode = "single"
    extra_variant_count: int = 0
    embedded_query_count: int = 1
    extra_embedded_queries: int = 0
    embedding_api_request_count: int = 1
    extra_embedding_api_requests: int = 0
    vector_search_call_count: int = 0
    extra_vector_search_calls: int = 0
    bm25_expansion_mode: BM25ExpansionMode = "asymmetric"
    bm25_query_variant_count: int = 1
    bm25_variant_eval_count: int = 1
    extra_bm25_variant_evals: int = 0
    bm25_merged_hit_count_before_cap: int = 0
    bm25_merged_hit_count_after_cap: int = 0
    retrieval_duration_ms: float = 0.0
    query_embedding_duration_ms: float = 0.0
    vector_search_duration_ms: float = 0.0


@dataclass
class MMRSelectionResult:
    """MMR selection order plus separate debug metadata for observability."""

    results: list[tuple[Embedding, float]]
    replacements: list[dict[str, object]]
    diagnostics: list[dict[str, object]]


@dataclass
class VectorCandidateSet:
    """Shared vector candidate-set construction output before lexical stages."""

    candidates: list[tuple[Embedding, float]]
    call_count: int
    duration_ms: float


@dataclass
class PreparedBM25Corpus:
    """Reusable BM25 scorer over the shared in-memory candidate corpus."""

    candidates: list[Embedding]
    scorer: BM25Okapi | None


@dataclass
class BM25Winner:
    """Winning lexical-safe variant provenance for one merged BM25 hit."""

    variant_index: int
    variant_query: str
    score: float


@dataclass
class BM25SearchBundle:
    """Merged BM25 branch output plus explicit expansion/debug metadata."""

    results: list[tuple[Embedding, float]]
    has_lexical_signal: bool
    variant_queries: list[str]
    variant_eval_count: int
    merged_hit_count_before_cap: int
    merged_hit_count_after_cap: int
    winner_by_id: dict[uuid.UUID, BM25Winner]


def _embedding_tiebreak_key(embedding: Embedding) -> tuple[str, int, str]:
    """Deterministic secondary key for equal-score ordering."""
    meta = embedding.metadata_json or {}
    chunk_index = meta.get("chunk_index", -1)
    if not isinstance(chunk_index, int):
        chunk_index = -1
    return (str(embedding.document_id), chunk_index, str(embedding.id))


def _sort_scored_embeddings(
    scored: list[tuple[Embedding, float]],
) -> list[tuple[Embedding, float]]:
    """Sort DESC by score with a deterministic tie-breaker."""
    return sorted(
        scored,
        key=lambda item: (-item[1], _embedding_tiebreak_key(item[0])),
    )


def _variant_mode_for_count(count: int) -> VariantMode:
    return "multi" if count > 1 else "single"


def build_variant_trace_metadata(bundle: SearchResultBundle) -> dict[str, object]:
    """Compact trace metadata used on parent request traces."""
    return {
        "variant_mode": bundle.variant_mode,
        "query_variant_count": bundle.query_variant_count,
        "extra_embedded_queries": bundle.extra_embedded_queries,
        "extra_embedding_api_requests": bundle.extra_embedding_api_requests,
        "extra_vector_search_calls": bundle.extra_vector_search_calls,
        "bm25_expansion_mode": bundle.bm25_expansion_mode,
        "bm25_query_variant_count": bundle.bm25_query_variant_count,
        "bm25_variant_eval_count": bundle.bm25_variant_eval_count,
        "extra_bm25_variant_evals": bundle.extra_bm25_variant_evals,
        "bm25_merged_hit_count_before_cap": bundle.bm25_merged_hit_count_before_cap,
        "bm25_merged_hit_count_after_cap": bundle.bm25_merged_hit_count_after_cap,
        "retrieval_duration_ms": bundle.retrieval_duration_ms,
    }


def build_variant_trace_tag(variant_mode: VariantMode) -> str:
    """Simple tag for slicing traces by variant fan-out."""
    return f"variants:{variant_mode}"


def detect_query_script_bucket(text: str) -> str:
    """Detect a coarse script bucket from the query text."""
    if re.search(r"[а-яё]", text.casefold(), flags=re.UNICODE):
        return "cyrillic"
    if re.search(r"[a-z]", text.casefold(), flags=re.UNICODE):
        return "latin"
    return "other"


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

    def _push(value: str) -> None:
        variants[:] = _normalize_query_variants([*variants, value])

    _push(query)

    cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
    _push(cleaned)

    tokens = re.findall(r"\w+", query.casefold(), flags=re.UNICODE)
    if tokens:
        unique_tokens = list(dict.fromkeys(tokens))
        _push(" ".join(unique_tokens))

    return variants or [query]


def _normalize_query_variants(values: list[str]) -> list[str]:
    """Normalize and dedupe query variants while preserving first-seen order."""
    variants: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        variants.append(normalized)
    return variants


def lexical_safe_query_variants(
    query: str,
    *,
    base_variants: list[str] | None = None,
) -> list[str]:
    """
    Return only normalization-safe variants suitable for lexical BM25 scoring.

    Today this mirrors the deterministic normalized variants used for vector
    retrieval. If expand_query() ever grows to include freer rewrites or
    paraphrases, BM25 must continue consuming only the lexical-safe subset
    unless the lexical branch contract is explicitly revisited.
    """
    source_variants = base_variants if base_variants is not None else expand_query(query)
    variants = _normalize_query_variants(source_variants)
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


def embed_queries_with_stats(
    queries: list[str], *, api_key: str
) -> tuple[list[list[float]], int]:
    """Embed multiple queries and return the actual API request count used."""
    if not queries:
        return [], 0
    vectors = embed_queries(queries, api_key=api_key)
    return vectors, 1


def _bm25_score_candidates_with_signal(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> tuple[list[tuple[Embedding, float]], bool]:
    """
    BM25 scoring over a pre-loaded list of Embedding objects.
    No DB access — operates on objects already in memory.
    Returns normalized scores in [0, 1].
    """
    prepared_corpus = _prepare_bm25_corpus(candidates)
    scored = _score_prepared_bm25_corpus(prepared_corpus, query, top_k)
    return scored, _has_lexical_signal(scored, query, top_k)


def _bm25_score_candidates(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    scored, _ = _bm25_score_candidates_with_signal(candidates, query, top_k)
    return scored


def _build_vector_candidate_set(
    client_id: uuid.UUID,
    variant_vectors: list[list[float]],
    db: Session,
    *,
    vector_search_fn,
) -> VectorCandidateSet:
    """Acquire, merge, dedupe, and truncate vector candidates across variants."""
    vector_started_at = perf_counter()
    vector_candidate_map: dict[uuid.UUID, tuple[Embedding, float]] = {}
    vector_search_call_count = 0
    for variant_vector in variant_vectors:
        vector_search_call_count += 1
        for embedding, similarity in vector_search_fn(
            client_id,
            variant_vector,
            BM25_CANDIDATE_POOL,
            db,
        ):
            existing = vector_candidate_map.get(embedding.id)
            if existing is None or similarity > existing[1]:
                vector_candidate_map[embedding.id] = (embedding, similarity)
    return VectorCandidateSet(
        candidates=_sort_scored_embeddings(list(vector_candidate_map.values()))[
            :BM25_CANDIDATE_POOL
        ],
        call_count=vector_search_call_count,
        duration_ms=round((perf_counter() - vector_started_at) * 1000, 2),
    )


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


def _prepare_bm25_corpus(candidates: list[Embedding]) -> PreparedBM25Corpus:
    """Build the shared in-memory BM25 scorer once for a candidate pool."""
    if not candidates:
        return PreparedBM25Corpus(candidates=[], scorer=None)
    corpus = [(emb.chunk_text or "").lower().split() for emb in candidates]
    return PreparedBM25Corpus(candidates=candidates, scorer=BM25Okapi(corpus))


def _lexical_overlap_results(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Current lexical branch participation criteria over a ranked output list."""
    lexical_overlap_scored = [
        (embedding, _lexical_overlap_score(query, embedding.chunk_text or ""))
        for embedding in candidates
    ]
    lexical_overlap_scored = [
        (embedding, score)
        for embedding, score in lexical_overlap_scored
        if score > 0.0
    ]
    return _sort_scored_embeddings(lexical_overlap_scored)[:top_k]


def _normalize_scored_results(
    scored: list[tuple[Embedding, float]],
) -> list[tuple[Embedding, float]]:
    """Normalize descending scores into [0, 1] while preserving ordering."""
    if not scored:
        return []
    max_s = scored[0][1]
    min_s = scored[-1][1]
    if max_s == min_s:
        return [(emb, 1.0) for emb, _ in scored]
    return [(emb, (s - min_s) / (max_s - min_s)) for emb, s in scored]


def _score_prepared_bm25_corpus(
    prepared_corpus: PreparedBM25Corpus,
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """
    BM25 scoring over a shared in-memory corpus.

    One corpus is built per request-stage candidate pool; repeated variant
    evaluation is only repeated lexical scoring over that already-built corpus.
    """
    query_tokens = query.lower().split()
    if not query_tokens or not prepared_corpus.candidates or prepared_corpus.scorer is None:
        return []

    raw_scores = [float(score) for score in prepared_corpus.scorer.get_scores(query_tokens)]
    scored = _sort_scored_embeddings(list(zip(prepared_corpus.candidates, raw_scores)))[:top_k]
    if not scored:
        return []

    distinct_raw_scores = len({round(score, 12) for _, score in scored}) > 1
    if not distinct_raw_scores:
        scored = _lexical_overlap_results(prepared_corpus.candidates, query, top_k)
        if not scored:
            return []

    return _normalize_scored_results(scored)


def _has_lexical_signal(
    results: list[tuple[Embedding, float]],
    query: str,
    top_k: int,
) -> bool:
    """
    Preserve lexical participation semantics over the final lexical branch output.

    Symmetric BM25 expansion changes lexical input generation only. This signal
    must be derived from the final merged lexical list handed downstream, not
    from a raw OR across per-variant scoring attempts.
    """
    return bool(_lexical_overlap_results([embedding for embedding, _ in results], query, top_k))


def _resolve_bm25_expansion_mode() -> BM25ExpansionMode:
    """Return the effective BM25 lexical expansion mode with a safe default."""
    if settings.bm25_expansion_mode == "symmetric_variants":
        return "symmetric_variants"
    return "asymmetric"


def _format_bm25_trace_results(
    results: list[tuple[Embedding, float]],
    *,
    winner_by_id: dict[uuid.UUID, BM25Winner],
) -> list[dict[str, object]]:
    """Add compact winner provenance to BM25 trace payloads."""
    payload = format_embedding_results(results, score_name="bm25_score")
    for (embedding, _), item in zip(results, payload):
        winner = winner_by_id.get(embedding.id)
        if winner is None:
            continue
        item["winner_variant_index"] = winner.variant_index
        if len(winner.variant_query) <= BM25_DEBUG_VARIANT_TEXT_MAX_LEN:
            item["winner_variant_text"] = truncate_text(winner.variant_query)
    return payload


def _run_bm25_search(
    candidates: list[Embedding],
    *,
    query: str,
    query_variants: list[str],
    top_k: int,
    expansion_mode: BM25ExpansionMode,
) -> BM25SearchBundle:
    """Evaluate BM25 over one shared corpus using asymmetric or symmetric policy."""
    variant_queries = (
        [query]
        if expansion_mode == "asymmetric"
        else lexical_safe_query_variants(query, base_variants=query_variants)
    )
    prepared_corpus = _prepare_bm25_corpus(candidates)
    variant_eval_count = len(variant_queries)
    if not candidates or not variant_queries:
        return BM25SearchBundle(
            results=[],
            has_lexical_signal=False,
            variant_queries=variant_queries or [query],
            variant_eval_count=0,
            merged_hit_count_before_cap=0,
            merged_hit_count_after_cap=0,
            winner_by_id={},
        )

    if expansion_mode == "asymmetric":
        results = _score_prepared_bm25_corpus(prepared_corpus, query, top_k)
        winner_by_id = {
            embedding.id: BM25Winner(variant_index=0, variant_query=query, score=score)
            for embedding, score in results
        }
        return BM25SearchBundle(
            results=results,
            has_lexical_signal=_has_lexical_signal(results, query, top_k),
            variant_queries=variant_queries,
            variant_eval_count=variant_eval_count,
            merged_hit_count_before_cap=len(results),
            merged_hit_count_after_cap=len(results),
            winner_by_id=winner_by_id,
        )

    merged_by_id: dict[uuid.UUID, tuple[Embedding, BM25Winner]] = {}
    for variant_index, variant_query in enumerate(variant_queries):
        variant_results = _score_prepared_bm25_corpus(prepared_corpus, variant_query, top_k)
        for embedding, score in variant_results:
            existing = merged_by_id.get(embedding.id)
            if existing is None or score > existing[1].score:
                merged_by_id[embedding.id] = (
                    embedding,
                    BM25Winner(
                        variant_index=variant_index,
                        variant_query=variant_query,
                        score=score,
                    ),
                )

    merged_results = _sort_scored_embeddings(
        [(embedding, winner.score) for embedding, winner in merged_by_id.values()]
    )
    merged_hit_count_before_cap = len(merged_results)
    final_results = merged_results[:top_k]
    winner_by_id = {
        embedding.id: merged_by_id[embedding.id][1]
        for embedding, _ in final_results
        if embedding.id in merged_by_id
    }
    return BM25SearchBundle(
        results=final_results,
        has_lexical_signal=_has_lexical_signal(final_results, query, top_k),
        variant_queries=variant_queries,
        variant_eval_count=variant_eval_count,
        merged_hit_count_before_cap=merged_hit_count_before_cap,
        merged_hit_count_after_cap=len(final_results),
        winner_by_id=winner_by_id,
    )


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

    sorted_ids = sorted(
        scores.keys(),
        key=lambda id_: (-scores[id_], _embedding_tiebreak_key(id_to_emb[id_])),
    )
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

    rescored = sorted(
        rescored,
        key=lambda item: (
            -item[1],
            -vector_scores.get(item[0].id, 0.0),
            -bm25_scores.get(item[0].id, 0.0),
            _embedding_tiebreak_key(item[0]),
        ),
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
    boosted = _sort_scored_embeddings(boosted)
    return boosted[:top_k]


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
    """
    Select top-k diverse chunks while preserving comparable output scores.

    This is an interim heuristic over a small post-rerank pool. Similarity is
    lexical Jaccard overlap on token sets, and each selection step recomputes
    pairwise comparisons against already-selected chunks. That is acceptable for
    the current bounded usage (typically 6-10 candidates, still reasonable up to
    roughly 50), but pools approaching 100 candidates become a hot-path cost and
    should be capped or optimized before we widen them further.
    """
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


def detect_source_overlaps(
    candidates: list[tuple[Embedding, float]],
    *,
    similarity_threshold: float = 0.75,
) -> tuple[bool, list[dict[str, object]], ReliabilityCapReason | None]:
    """Detect cross-document overlap on the final top-k result set only."""
    if len(candidates) > MAX_OVERLAP_CHECK_CANDIDATES:
        logger.warning(
            "Source overlap detection received more candidates than expected; truncating",
            extra={
                "candidate_count": len(candidates),
                "max_candidates": MAX_OVERLAP_CHECK_CANDIDATES,
            },
        )
    bounded_candidates = candidates[:MAX_OVERLAP_CHECK_CANDIDATES]
    overlap_pairs: list[dict[str, object]] = []
    for index, (first, _) in enumerate(bounded_candidates):
        for second, _ in bounded_candidates[index + 1 :]:
            if first.document_id == second.document_id:
                continue
            similarity = _candidate_similarity(first, second)
            if similarity < similarity_threshold:
                continue
            overlap_pairs.append(
                {
                    "chunk_a_id": str(first.id),
                    "chunk_b_id": str(second.id),
                    "similarity": round(similarity, 4),
                    "signal_type": "cross_document_overlap",
                    "confirmed_by_llm": False,
                }
            )
    return bool(overlap_pairs), overlap_pairs, ("source_overlap" if overlap_pairs else None)


def detect_conflicts(
    candidates: list[tuple[Embedding, float]],
    *,
    similarity_threshold: float = 0.75,
) -> tuple[bool, list[dict[str, object]], ReliabilityCapReason | None]:
    """Backward-compatible alias for the interim source-overlap heuristic."""
    return detect_source_overlaps(
        candidates,
        similarity_threshold=similarity_threshold,
    )


def compute_reliability_score(
    *,
    top_score: float | None,
    conflicts_found: bool,
    result_count: int,
) -> ReliabilityScore:
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

    PostgreSQL uses pgvector for candidate acquisition, while SQLite uses
    Python cosine search. Downstream ranking and observability stages are shared.
    """
    retrieval_started_at = perf_counter()
    query_variants = expand_query(query)
    query_variant_count = len(query_variants)
    variant_mode = _variant_mode_for_count(query_variant_count)
    extra_variant_count = max(query_variant_count - 1, 0)
    if trace is not None:
        trace.span(
            name="query-expansion",
            input={"query": query},
        ).end(
            output={
                "variants": query_variants,
                "query_variant_count": query_variant_count,
                "variant_mode": variant_mode,
                "extra_variant_count": extra_variant_count,
            }
        )

    embedding_started_at = perf_counter()
    variant_vectors, embedding_api_request_count = embed_queries_with_stats(
        query_variants,
        api_key=api_key,
    )
    query_embedding_duration_ms = round((perf_counter() - embedding_started_at) * 1000, 2)
    embedded_query_count = len(query_variants)
    extra_embedded_queries = max(embedded_query_count - 1, 0)
    extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
    trace_query_vector = variant_vectors[0] if variant_vectors else []
    query_script_bucket = detect_query_script_bucket(query)
    if trace is not None:
        trace.span(
            name="query-embedding",
            input={
                "query_variants": query_variants,
                "query_variant_count": query_variant_count,
                "variant_mode": variant_mode,
                "model": EMBEDDING_MODEL,
            },
        ).end(
            output={
                "embedded_query_count": embedded_query_count,
                "extra_embedded_queries": extra_embedded_queries,
                "embedding_api_request_count": embedding_api_request_count,
                "extra_embedding_api_requests": extra_embedding_api_requests,
                "duration_ms": query_embedding_duration_ms,
            }
        )

    db_url = str(db.bind.url if db.bind else "")
    vector_engine = "python-cosine" if "sqlite" in db_url else "pgvector"
    vector_search_fn = _python_cosine_search if "sqlite" in db_url else _pgvector_search
    bm25_expansion_mode = _resolve_bm25_expansion_mode()

    # Build one shared candidate set before lexical stages: engine-specific
    # acquisition, then cross-variant merge/dedup/truncation.
    vector_candidate_set = _build_vector_candidate_set(
        client_id,
        variant_vectors,
        db,
        vector_search_fn=vector_search_fn,
    )
    vector_candidates = vector_candidate_set.candidates
    vector_search_call_count = vector_candidate_set.call_count
    vector_duration_ms = vector_candidate_set.duration_ms
    extra_vector_search_calls = max(vector_search_call_count - 1, 0)
    bm25_variant_queries = (
        [query]
        if bm25_expansion_mode == "asymmetric"
        else lexical_safe_query_variants(query, base_variants=query_variants)
    )

    if not vector_candidates:
        retrieval_duration_ms = round((perf_counter() - retrieval_started_at) * 1000, 2)
        if trace is not None:
            trace.span(
                name="vector-search",
                input={
                    "query_embedding": format_query_embedding_preview(trace_query_vector),
                    "query_variants": query_variants,
                    "tenant_id": str(client_id),
                    "top_k": BM25_CANDIDATE_POOL,
                    "engine": vector_engine,
                },
            ).end(
                output={
                    "chunks": [],
                    "duration_ms": vector_duration_ms,
                    "total_candidates_scanned": 0,
                    "vector_search_call_count": vector_search_call_count,
                    "extra_vector_search_calls": extra_vector_search_calls,
                }
            )
        return SearchResultBundle(
            results=[],
            query_variants=query_variants,
            query_script_bucket=query_script_bucket,
            reliability_score="low",
            query_variant_count=query_variant_count,
            variant_mode=variant_mode,
            extra_variant_count=extra_variant_count,
            embedded_query_count=embedded_query_count,
            extra_embedded_queries=extra_embedded_queries,
            embedding_api_request_count=embedding_api_request_count,
            extra_embedding_api_requests=extra_embedding_api_requests,
            vector_search_call_count=vector_search_call_count,
            extra_vector_search_calls=extra_vector_search_calls,
            bm25_expansion_mode=bm25_expansion_mode,
            bm25_query_variant_count=len(bm25_variant_queries),
            bm25_variant_eval_count=0,
            extra_bm25_variant_evals=0,
            retrieval_duration_ms=retrieval_duration_ms,
            query_embedding_duration_ms=query_embedding_duration_ms,
            vector_search_duration_ms=vector_duration_ms,
        )

    vector_embs = [emb for emb, _ in vector_candidates]
    if trace is not None:
        trace.span(
            name="vector-search",
            input={
                "query_embedding": format_query_embedding_preview(trace_query_vector),
                "query_variants": query_variants,
                "tenant_id": str(client_id),
                "top_k": BM25_CANDIDATE_POOL,
                "engine": vector_engine,
            },
        ).end(
            output={
                "chunks": format_embedding_results(
                    vector_candidates[:top_k * 2],
                    score_name="similarity_score",
                ),
                "duration_ms": vector_duration_ms,
                "total_candidates_scanned": len(vector_candidates),
                "vector_search_call_count": vector_search_call_count,
                "extra_vector_search_calls": extra_vector_search_calls,
            }
            )

    rrf_candidate_pool = top_k * RRF_CANDIDATE_POOL_MULTIPLIER
    bm25_started_at = perf_counter()
    bm25_bundle = _run_bm25_search(
        vector_embs,
        query=query,
        query_variants=query_variants,
        top_k=rrf_candidate_pool,
        expansion_mode=bm25_expansion_mode,
    )
    bm25_results = bm25_bundle.results
    has_lexical_signal = bm25_bundle.has_lexical_signal
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={
                "query": query,
                "query_variants": bm25_bundle.variant_queries,
                "tenant_id": str(client_id),
                "top_k": rrf_candidate_pool,
                "bm25_expansion_mode": bm25_expansion_mode,
                "variant_source": (
                    "original-query"
                    if bm25_expansion_mode == "asymmetric"
                    else "lexical-safe-normalized-variants"
                ),
            },
        ).end(
            output={
                "chunks": _format_bm25_trace_results(
                    bm25_results,
                    winner_by_id=bm25_bundle.winner_by_id,
                ),
                "duration_ms": bm25_duration_ms,
                "bm25_query_variant_count": len(bm25_bundle.variant_queries),
                "bm25_variant_eval_count": bm25_bundle.variant_eval_count,
                "extra_bm25_variant_evals": max(bm25_bundle.variant_eval_count - 1, 0),
                "bm25_merged_hit_count_before_cap": (
                    bm25_bundle.merged_hit_count_before_cap
                ),
                "bm25_merged_hit_count_after_cap": (
                    bm25_bundle.merged_hit_count_after_cap
                ),
            }
        )
    vector_for_rrf = vector_candidates[:rrf_candidate_pool]
    best_vector_similarity = vector_candidates[0][1] if vector_candidates else None
    best_keyword_score = bm25_results[0][1] if bm25_results else None

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
                "bm25_expansion_mode": bm25_expansion_mode,
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

    script_started_at = perf_counter()
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
                "duration_ms": round((perf_counter() - script_started_at) * 1000, 2),
            }
        )

    # Keep MMR on the small post-rerank pool only. The current lexical pairwise
    # similarity is an interim heuristic, not a large-pool reranker.
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

    overlap_started_at = perf_counter()
    conflicts_found, conflict_pairs, reliability_cap_reason = detect_source_overlaps(
        final_results
    )
    if trace is not None:
        trace.span(
            name="source-overlap-check",
            input={
                "candidate_count": len(final_results),
                "strategy": "cross-document-jaccard-overlap-heuristic",
            },
        ).end(
            output={
                "semantic_conflict_detection": False,
                "conflicts_found": conflicts_found,
                "conflict_pairs": conflict_pairs,
                "reliability_cap_reason": reliability_cap_reason,
                "duration_ms": round((perf_counter() - overlap_started_at) * 1000, 2),
            }
        )

    reliability_score = compute_reliability_score(
        top_score=final_results[0][1] if final_results else None,
        conflicts_found=conflicts_found,
        result_count=len(final_results),
    )
    retrieval_duration_ms = round((perf_counter() - retrieval_started_at) * 1000, 2)

    return SearchResultBundle(
        results=final_results,
        best_vector_similarity=best_vector_similarity,
        best_keyword_score=best_keyword_score,
        has_lexical_signal=has_lexical_signal,
        query_variants=query_variants,
        query_script_bucket=query_script_bucket,
        conflicts_found=conflicts_found,
        conflict_pairs=conflict_pairs,
        reliability_score=reliability_score,
        reliability_cap_reason=reliability_cap_reason,
        query_variant_count=query_variant_count,
        variant_mode=variant_mode,
        extra_variant_count=extra_variant_count,
        embedded_query_count=embedded_query_count,
        extra_embedded_queries=extra_embedded_queries,
        embedding_api_request_count=embedding_api_request_count,
        extra_embedding_api_requests=extra_embedding_api_requests,
        vector_search_call_count=vector_search_call_count,
        extra_vector_search_calls=extra_vector_search_calls,
        bm25_expansion_mode=bm25_expansion_mode,
        bm25_query_variant_count=len(bm25_bundle.variant_queries),
        bm25_variant_eval_count=bm25_bundle.variant_eval_count,
        extra_bm25_variant_evals=max(bm25_bundle.variant_eval_count - 1, 0),
        bm25_merged_hit_count_before_cap=bm25_bundle.merged_hit_count_before_cap,
        bm25_merged_hit_count_after_cap=bm25_bundle.merged_hit_count_after_cap,
        retrieval_duration_ms=retrieval_duration_ms,
        query_embedding_duration_ms=query_embedding_duration_ms,
        vector_search_duration_ms=vector_duration_ms,
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

    return _sort_scored_embeddings(scored)[:top_k]


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
