"""Shared dataclasses for the hybrid retrieval pipeline: bundles + stage results."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from rank_bm25 import BM25Okapi

from backend.models import Embedding
from backend.search.reliability import RetrievalReliability, default_retrieval_reliability

VariantMode = Literal["single", "multi"]
BM25ExpansionMode = Literal["asymmetric", "symmetric_variants"]


@dataclass
class SearchResultBundle:
    """Ranked retrieval results plus raw signals used for confidence decisions."""

    results: list[tuple[Embedding, float]]
    best_vector_similarity: float | None = None
    # For each returned final chunk: cosine similarity from vector-candidate stage.
    # If a chunk came only from lexical/BM25 path (no vector candidate), value is None.
    vector_similarities: list[float | None] | None = None
    best_keyword_score: float | None = None
    has_lexical_signal: bool = False
    query_variants: list[str] | None = None
    query_script_bucket: str | None = None
    reliability: RetrievalReliability = field(default_factory=default_retrieval_reliability)
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


# ---------------------------------------------------------------------------
# Pipeline stage dataclasses — private to search_similar_chunks_detailed_async
# ---------------------------------------------------------------------------


@dataclass
class _QueryStageResult:
    query_variants: list[str]
    variant_vectors: list[list[float]]
    query_variant_count: int
    variant_mode: VariantMode
    extra_variant_count: int
    embedded_query_count: int
    extra_embedded_queries: int
    embedding_api_request_count: int
    extra_embedding_api_requests: int
    query_embedding_duration_ms: float
    query_script_bucket: str
    rewritten_variant: str | None
    trace_query_vector: list[float]


@dataclass
class _CandidateStageResult:
    vector_candidates: list[tuple[Embedding, float]]
    vector_search_call_count: int
    vector_duration_ms: float
    vector_engine: str
    bm25_variant_queries: list[str]
    bm25_bundle: BM25SearchBundle
    bm25_duration_ms: float
    bm25_expansion_mode: BM25ExpansionMode
    fused_results: list[tuple[Embedding, float]]
    rrf_duration_ms: float
    best_vector_similarity: float | None
    best_keyword_score: float | None
    rerank_lexical_query: str | None


@dataclass
class _RankingStageResult:
    final_results: list[tuple[Embedding, float]]
    vector_similarities: list[float | None]
    mmr_selection: MMRSelectionResult


@dataclass
class _QualityStageResult:
    reliability: RetrievalReliability
