"""BM25 lexical scoring: corpus prep, per-variant search, and script selection."""

from __future__ import annotations

import uuid

from rank_bm25 import BM25Okapi

from backend.core.config import settings
from backend.core.scripts import NO_SCRIPT_BUCKET
from backend.models import Embedding
from backend.observability.formatters import format_embedding_results, truncate_text
from backend.search.fusion import _sort_scored_embeddings
from backend.search.query_variants import detect_query_script_bucket
from backend.search.reranking import lexical_overlap_score
from backend.search.types import BM25ExpansionMode, BM25SearchBundle, BM25Winner, PreparedBM25Corpus
from backend.utils.text import word_tokens

# Number of vector candidates to pre-fetch before BM25 scoring.
# BM25 runs only on this pool (already in memory) — never queries all tenant chunks.
BM25_CANDIDATE_POOL = 200
BM25_DEBUG_VARIANT_TEXT_MAX_LEN = 80


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


def _prepare_bm25_corpus(candidates: list[Embedding]) -> PreparedBM25Corpus:
    """Build the shared in-memory BM25 scorer once for a candidate pool."""
    if not candidates:
        return PreparedBM25Corpus(candidates=[], scorer=None)
    corpus = [word_tokens(emb.chunk_text or "") for emb in candidates]
    return PreparedBM25Corpus(candidates=candidates, scorer=BM25Okapi(corpus))


def _lexical_overlap_results(
    candidates: list[Embedding],
    query: str,
    top_k: int,
) -> list[tuple[Embedding, float]]:
    """Current lexical branch participation criteria over a ranked output list."""
    lexical_overlap_scored = [
        (embedding, lexical_overlap_score(query, embedding.chunk_text or ""))
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
        # Single unique match: award 1.0 — it is the top result by definition.
        # Multiple docs with identical scores: award 0.0 — the signal is
        # uninformative and must not inflate every doc's fusion contribution.
        flat_score = 1.0 if len(scored) == 1 else 0.0
        return [(emb, flat_score) for emb, _ in scored]
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
    query_tokens = word_tokens(query)
    if not query_tokens or not prepared_corpus.candidates or prepared_corpus.scorer is None:
        return []

    raw_scores = [float(score) for score in prepared_corpus.scorer.get_scores(query_tokens)]
    scored = _sort_scored_embeddings(list(zip(prepared_corpus.candidates, raw_scores, strict=True)))[:top_k]
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


def _is_en_query(query: str, query_script_bucket: str) -> bool:
    """Return True when the query is safe for English BM25 (pure ASCII).

    Includes the letterless bucket (digits, punctuation, symbols) — these
    contain no non-ASCII characters so BM25 against an English corpus is
    always safe.
    """
    if query_script_bucket not in ("latin", NO_SCRIPT_BUCKET):
        return False
    return all(ord(c) < 128 for c in query)


def _bm25_queries_for_script(
    query: str,
    query_variants: list[str],
    query_script_bucket: str,
    *,
    kb_script: str | None = None,
) -> list[str]:
    """Select BM25 query list based on script bucket and KB language.

    English queries use the original text.  For non-EN queries the order depends
    on whether the KB is in the same script as the query:

    - Same script (e.g. Russian query, Russian KB): original first so that the
      asymmetric BM25 mode uses the native-language query for lexical matching.
    - Different script / unknown (e.g. Russian query, English KB): EN rewrite
      first so asymmetric mode uses the rewrite for lexical matching.

    Both variants are always included so symmetric mode evaluates both.
    """
    if _is_en_query(query, query_script_bucket):
        return [query]
    rewritten = next(
        (
            v
            for v in reversed(query_variants)
            if _is_en_query(v, detect_query_script_bucket(v))
        ),
        None,
    )
    if not rewritten:
        return [query]
    # Same-language KB: original first (asymmetric uses [0] = native query).
    if kb_script and kb_script == query_script_bucket:
        return [query, rewritten]
    # Cross-lingual KB or unknown: EN rewrite first (asymmetric uses [0] = EN).
    return [rewritten, query]


def _format_bm25_trace_results(
    results: list[tuple[Embedding, float]],
    *,
    winner_by_id: dict[uuid.UUID, BM25Winner],
) -> list[dict[str, object]]:
    """Add compact winner provenance to BM25 trace payloads."""
    payload = format_embedding_results(results, score_name="bm25_score")
    for (embedding, _), item in zip(results, payload, strict=True):
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
    variant_queries: list[str],
    top_k: int,
    expansion_mode: BM25ExpansionMode,
) -> BM25SearchBundle:
    """Evaluate BM25 over one shared corpus using asymmetric or symmetric policy."""
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
        # Use the first variant query (may be an EN rewrite for non-EN queries).
        effective_query = variant_queries[0] if variant_queries else query
        results = _score_prepared_bm25_corpus(prepared_corpus, effective_query, top_k)
        winner_by_id = {
            embedding.id: BM25Winner(variant_index=0, variant_query=effective_query, score=score)
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
