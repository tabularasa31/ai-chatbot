"""Rank fusion: RRF, script boost, MMR diversification, source-overlap detection."""

from __future__ import annotations

import logging
import uuid

from backend.core.scripts import detect_script_bucket
from backend.models import Embedding
from backend.search.reliability import MAX_OVERLAP_CHECK_CANDIDATES, SourceOverlapPair
from backend.search.reranking import embedding_tiebreak_key
from backend.search.types import MMRSelectionResult
from backend.utils.text import token_set

SCRIPT_BOOST_FACTOR = 0.1
MMR_LAMBDA = 0.7

logger = logging.getLogger(__name__)


def _sort_scored_embeddings(
    scored: list[tuple[Embedding, float]],
) -> list[tuple[Embedding, float]]:
    """Sort DESC by score with a deterministic tie-breaker."""
    return sorted(
        scored,
        key=lambda item: (-item[1], embedding_tiebreak_key(item[0])),
    )


def reciprocal_rank_fusion(
    vector_results: list[tuple[Embedding, float]],
    bm25_results: list[tuple[Embedding, float]],
    k: int = 60,
    top_k: int = 5,
    *,
    entity_results: list[tuple[Embedding, float]] | None = None,
) -> list[tuple[Embedding, float]]:
    """Combine vector + BM25 (+ optional entity-overlap) results using RRF.

    Each input list contributes 1/(k+rank+1) per position. The score for
    a given chunk is the sum of contributions across whichever channels
    surfaced it. ``entity_results`` is keyword-only because three
    same-typed positional ranked lists are easy to mix up at call sites;
    keeping it named makes the third-channel intent obvious. ``None``
    (the default) means "skip the entity channel entirely" — when the
    tenant has no entity index or no API key the caller passes None
    and we degrade to the two-channel formula with zero added cost.
    """
    scores: dict[uuid.UUID, float] = {}
    id_to_emb: dict[uuid.UUID, Embedding] = {}

    for rank, (emb, _) in enumerate(vector_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    for rank, (emb, _) in enumerate(bm25_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb

    if entity_results:
        for rank, (emb, _) in enumerate(entity_results):
            scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
            id_to_emb[emb.id] = emb

    sorted_ids = sorted(
        scores.keys(),
        key=lambda id_: (-scores[id_], embedding_tiebreak_key(id_to_emb[id_])),
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


def _embedding_script_bucket(embedding: Embedding) -> str:
    """Infer the writing system of a chunk from its own text."""
    return detect_script_bucket(embedding.chunk_text or "")


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


def _candidate_similarity(first: Embedding, second: Embedding) -> float:
    """Approximate chunk similarity using Jaccard overlap."""
    first_tokens = token_set(first.chunk_text or "")
    second_tokens = token_set(second.chunk_text or "")
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
) -> tuple[bool, tuple[SourceOverlapPair, ...]]:
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
    overlap_pairs: list[SourceOverlapPair] = []
    for index, (first, _) in enumerate(bounded_candidates):
        for second, _ in bounded_candidates[index + 1 :]:
            if first.document_id == second.document_id:
                continue
            similarity = _candidate_similarity(first, second)
            if similarity < similarity_threshold:
                continue
            overlap_pairs.append(
                SourceOverlapPair(
                    chunk_a_id=str(first.id),
                    chunk_b_id=str(second.id),
                    similarity=round(similarity, 4),
                )
            )
    return bool(overlap_pairs), tuple(overlap_pairs)
