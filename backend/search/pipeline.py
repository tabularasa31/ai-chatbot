"""Async hybrid retrieval pipeline stages and the public orchestrator.

Public entry points carry an ``async``/``_async`` affix (a naming relic of
the staged sync→async migration); the pipeline stages are private
(``_async_*`` prefix). The former sync twins were removed once the last
runtime callers (chat handlers, search routes) moved to this path.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.db import is_sqlite as _session_is_sqlite
from backend.knowledge.entity_extractor import extract_entities_from_query
from backend.models import Embedding, RerankerStrategy, Tenant
from backend.observability import TraceHandle
from backend.observability.formatters import (
    format_embedding_results,
    format_query_embedding_preview,
)
from backend.observability.metrics import capture_event
from backend.search.bm25 import (
    _bm25_queries_for_script,
    _format_bm25_trace_results,
    _is_en_query,
    _resolve_bm25_expansion_mode,
    _run_bm25_search,
)
from backend.search.embedding import async_embed_queries_with_stats
from backend.search.fusion import (
    MMR_LAMBDA,
    _collect_score_map,
    apply_script_boost,
    detect_source_overlaps,
    mmr_select,
    reciprocal_rank_fusion,
)
from backend.search.query_variants import (
    _async_rewrite_query_for_retrieval,
    _normalize_query_variants,
    detect_query_script_bucket,
    expand_query,
)
from backend.search.reliability import (
    _build_contradiction_adjudication_evidence,
    build_reliability_assessment,
    build_reliability_projection,
    detect_metadata_contradictions,
)
from backend.search.reranking import RerankSignals, rerank_with_fallback
from backend.search.retrieval_db import (
    _async_build_vector_candidate_set,
    _async_tenant_has_embeddings,
    async_detect_tenant_kb_script,
    async_entity_overlap_search,
)
from backend.search.types import (
    BM25ExpansionMode,
    BM25SearchBundle,
    SearchResultBundle,
    VariantMode,
    _CandidateStageResult,
    _QualityStageResult,
    _QueryStageResult,
    _RankingStageResult,
)
from backend.tenants.cache import get_cached_tenant

BM25_CANDIDATE_POOL = 200
RRF_CANDIDATE_POOL_MULTIPLIER = 4

logger = logging.getLogger(__name__)


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


async def _async_resolve_reranker_strategy(
    tenant_id: uuid.UUID, db: AsyncSession
) -> str:
    """Tenant's reranker choice; the heuristic when the row cannot be read."""
    cached = get_cached_tenant(tenant_id)
    if cached is not None:
        return _reranker_strategy_value(cached.reranker_strategy)
    try:
        result = await db.execute(
            select(Tenant.reranker_strategy).filter(Tenant.id == tenant_id)
        )
        return _reranker_strategy_value(result.scalar_one_or_none())
    except Exception:
        logger.warning("reranker_strategy_lookup_failed", exc_info=True)
        return RerankerStrategy.heuristic.value


def _reranker_strategy_value(raw: object) -> str:
    value = raw.value if isinstance(raw, RerankerStrategy) else raw
    if isinstance(value, str) and value in {s.value for s in RerankerStrategy}:
        return value
    return RerankerStrategy.heuristic.value


async def _async_run_ranking_stage(
    *,
    query: str,
    query_stage: _QueryStageResult,
    candidate_stage: _CandidateStageResult,
    top_k: int,
    trace: TraceHandle | None,
    reranker_strategy: str,
    tenant_id: uuid.UUID,
    api_key: str | None,
) -> _RankingStageResult:
    q = query_stage
    c = candidate_stage

    rerank_outcome = await rerank_with_fallback(
        query,
        c.fused_results,
        strategy=reranker_strategy,
        signals=RerankSignals(
            vector_scores=_collect_score_map(c.vector_candidates),
            bm25_scores=_collect_score_map(c.bm25_bundle.results),
            lexical_query=c.rerank_lexical_query,
        ),
        top_k=top_k,
        api_key=api_key,
        tenant_id=tenant_id,
    )
    reranked_results = rerank_outcome.results
    if trace is not None:
        trace.span(
            name="reranking",
            input={
                "query": query,
                "candidate_count": len(c.fused_results),
                "strategy": rerank_outcome.strategy_requested,
                "model": rerank_outcome.model,
            },
        ).end(
            output={
                "ranked": format_embedding_results(
                    reranked_results,
                    score_name="reranker_score",
                ),
                "top_score": reranked_results[0][1] if reranked_results else None,
                "strategy_applied": rerank_outcome.strategy_applied,
                "fallback_reason": rerank_outcome.fallback_reason,
                "duration_ms": rerank_outcome.duration_ms,
            }
        )

    script_started_at = perf_counter()
    script_boosted_results = apply_script_boost(
        q.query_script_bucket,
        reranked_results,
        top_k=top_k * 2,
    )
    if trace is not None:
        trace.span(
            name="script-boost",
            input={
                "query_script_bucket": q.query_script_bucket,
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
    mmr_selection = mmr_select(script_boosted_results, top_k=top_k)
    final_results = mmr_selection.results
    vector_similarity_by_id = {emb.id: sim for emb, sim in c.vector_candidates}
    vector_similarities: list[float | None] = [
        float(vector_similarity_by_id[emb.id]) if emb.id in vector_similarity_by_id else None
        for emb, _ in final_results
    ]
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

    return _RankingStageResult(
        final_results=final_results,
        vector_similarities=vector_similarities,
        mmr_selection=mmr_selection,
    )


def _build_empty_result_bundle(
    q: _QueryStageResult,
    c: _CandidateStageResult,
    retrieval_duration_ms: float,
) -> SearchResultBundle:
    return SearchResultBundle(
        results=[],
        query_variants=q.query_variants,
        query_script_bucket=q.query_script_bucket,
        reliability=build_reliability_assessment(top_score=None, result_count=0),
        query_variant_count=q.query_variant_count,
        variant_mode=q.variant_mode,
        extra_variant_count=q.extra_variant_count,
        embedded_query_count=q.embedded_query_count,
        extra_embedded_queries=q.extra_embedded_queries,
        embedding_api_request_count=q.embedding_api_request_count,
        extra_embedding_api_requests=q.extra_embedding_api_requests,
        vector_search_call_count=c.vector_search_call_count,
        extra_vector_search_calls=max(c.vector_search_call_count - 1, 0),
        bm25_expansion_mode=c.bm25_expansion_mode,
        bm25_query_variant_count=len(c.bm25_variant_queries),
        bm25_variant_eval_count=0,
        extra_bm25_variant_evals=0,
        retrieval_duration_ms=retrieval_duration_ms,
        query_embedding_duration_ms=q.query_embedding_duration_ms,
        vector_search_duration_ms=c.vector_duration_ms,
    )


def _trace_vector_search(
    trace: TraceHandle | None,
    *,
    query_stage: _QueryStageResult,
    tenant_id: uuid.UUID,
    vector_engine: str,
    vector_candidates: list[tuple[Embedding, float]],
    vector_duration_ms: float,
    vector_search_call_count: int,
    top_k: int | None = None,
) -> None:
    """Write the single "vector-search" trace span shared by the empty and normal paths."""
    if trace is None:
        return
    chunks = (
        format_embedding_results(vector_candidates[: top_k * 2], score_name="similarity_score")
        if top_k is not None
        else []
    )
    trace.span(
        name="vector-search",
        input={
            "query_embedding": format_query_embedding_preview(query_stage.trace_query_vector),
            "query_variants": query_stage.query_variants,
            "tenant_id": str(tenant_id),
            "top_k": BM25_CANDIDATE_POOL,
            "engine": vector_engine,
        },
    ).end(
        output={
            "chunks": chunks,
            "duration_ms": vector_duration_ms,
            "total_candidates_scanned": len(vector_candidates),
            "vector_search_call_count": vector_search_call_count,
            "extra_vector_search_calls": max(vector_search_call_count - 1, 0),
        }
    )


# ── Async pipeline stages ────────────────────────────────────────────────────


async def _async_run_query_stage(
    *,
    query: str,
    api_key: str,
    trace: TraceHandle | None,
    precomputed_query_variants: list[str] | None,
    precomputed_variant_vectors: list[list[float]] | None,
    precomputed_embedding_api_request_count: int | None,
    precomputed_rewritten_variant: str | None,
    embedding_timeout: float | None,
) -> _QueryStageResult:
    """Query stage: expand, rewrite, and embed the query variants.

    Key optimization: when not using precomputed variants, the query-rewrite
    LLM call and the embedding of base variants are launched concurrently via
    ``asyncio.gather``, saving the latency of whichever finishes first.
    If the rewrite produces a new variant it is embedded in a second (fast)
    call afterward — total API calls stay the same as the sync path.
    """
    use_precomputed = (
        precomputed_query_variants is not None
        and precomputed_variant_vectors is not None
        and precomputed_query_variants
        and len(precomputed_query_variants) == len(precomputed_variant_vectors)
    )

    query_variants = precomputed_query_variants if use_precomputed else expand_query(query)

    rewritten_variant: str | None = None
    variant_vectors: list[list[float]] = []
    embedding_api_request_count = 0
    query_embedding_duration_ms = 0.0
    embedded_query_count = 0
    extra_embedded_queries = 0
    extra_embedding_api_requests = 0

    if not use_precomputed:
        embedding_started_at = perf_counter()
        # Parallel: LLM rewrite + base variant embedding
        rewritten_variant, (base_vectors, api_count) = await asyncio.gather(
            _async_rewrite_query_for_retrieval(query, api_key=api_key),
            async_embed_queries_with_stats(query_variants, api_key=api_key, timeout=embedding_timeout),
        )

        if rewritten_variant:
            normalized = _normalize_query_variants([*query_variants, rewritten_variant])
            new_variants = [v for v in normalized if v not in set(query_variants)]
            if new_variants:
                extra_vectors, extra_count = await async_embed_queries_with_stats(
                    new_variants, api_key=api_key, timeout=embedding_timeout
                )
                variant_vectors = base_vectors + extra_vectors
                embedding_api_request_count = api_count + extra_count
                query_variants = normalized
            else:
                variant_vectors = base_vectors
                embedding_api_request_count = api_count
        else:
            variant_vectors = base_vectors
            embedding_api_request_count = api_count

        query_embedding_duration_ms = round((perf_counter() - embedding_started_at) * 1000, 2)
        embedded_query_count = len(query_variants)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []

        if trace is not None:
            trace.span(
                name="query-expansion",
                input={"query": query},
            ).end(
                output={
                    "variants": query_variants,
                    "rewritten_variant": rewritten_variant,
                    "query_variant_count": len(query_variants),
                    "variant_mode": _variant_mode_for_count(len(query_variants)),
                    "extra_variant_count": max(len(query_variants) - 1, 0),
                }
            )
            trace.span(
                name="query-embedding",
                input={
                    "query_variants": query_variants,
                    "query_variant_count": len(query_variants),
                    "variant_mode": _variant_mode_for_count(len(query_variants)),
                    "model": settings.embedding_model,
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
    else:
        variant_vectors = precomputed_variant_vectors or []
        embedding_api_request_count = int(precomputed_embedding_api_request_count or 1)
        embedded_query_count = len(variant_vectors)
        extra_embedded_queries = max(embedded_query_count - 1, 0)
        extra_embedding_api_requests = max(embedding_api_request_count - 1, 0)
        trace_query_vector = variant_vectors[0] if variant_vectors else []
        if trace is not None:
            trace.span(
                name="query-expansion",
                input={"query": query},
            ).end(
                output={
                    "variants": query_variants,
                    "rewritten_variant": precomputed_rewritten_variant,
                    "query_variant_count": len(query_variants),
                    "variant_mode": _variant_mode_for_count(len(query_variants)),
                    "extra_variant_count": max(len(query_variants) - 1, 0),
                }
            )

    query_variant_count = len(query_variants)
    variant_mode = _variant_mode_for_count(query_variant_count)

    return _QueryStageResult(
        query_variants=query_variants,
        variant_vectors=variant_vectors,
        query_variant_count=query_variant_count,
        variant_mode=variant_mode,
        extra_variant_count=max(query_variant_count - 1, 0),
        embedded_query_count=embedded_query_count,
        extra_embedded_queries=extra_embedded_queries,
        embedding_api_request_count=embedding_api_request_count,
        extra_embedding_api_requests=extra_embedding_api_requests,
        query_embedding_duration_ms=query_embedding_duration_ms,
        query_script_bucket=detect_query_script_bucket(query),
        rewritten_variant=rewritten_variant,
        trace_query_vector=trace_query_vector,
    )


async def _async_run_candidate_stage(
    *,
    tenant_id: uuid.UUID,
    query: str,
    query_stage: _QueryStageResult,
    top_k: int,
    db: AsyncSession,
    trace: TraceHandle | None,
    api_key: str | None = None,
) -> _CandidateStageResult:
    """Candidate stage: vector + BM25 + entity-overlap retrieval and RRF fusion.

    NER is run via ``run_in_executor`` so it remains concurrent with vector
    and BM25 retrieval without blocking the event loop.
    """
    q = query_stage
    is_sqlite = _session_is_sqlite(db)
    vector_engine = "python-cosine" if is_sqlite else "pgvector"
    bm25_expansion_mode: BM25ExpansionMode = _resolve_bm25_expansion_mode()

    kb_script = await async_detect_tenant_kb_script(tenant_id, db)
    bm25_variant_queries = _bm25_queries_for_script(
        query, q.query_variants, q.query_script_bucket, kb_script=kb_script
    )
    rerank_lexical_query: str | None = (
        None
        if _is_en_query(query, q.query_script_bucket)
        else (bm25_variant_queries[0] if bm25_variant_queries else None)
    )

    # NER runs concurrently in the default executor (thread pool).
    ner_task: asyncio.Task[list[str]] | None = None
    loop = asyncio.get_running_loop()
    if api_key and await _async_tenant_has_embeddings(tenant_id, db):
        ner_task = loop.run_in_executor(
            None,
            lambda: extract_entities_from_query(query, api_key, tenant_id=str(tenant_id)),
        )

    vector_candidate_set = await _async_build_vector_candidate_set(
        tenant_id,
        q.variant_vectors,
        db,
        is_sqlite=is_sqlite,
    )
    vector_candidates = vector_candidate_set.candidates
    vector_search_call_count = vector_candidate_set.call_count
    vector_duration_ms = vector_candidate_set.duration_ms

    if not vector_candidates:
        if ner_task is not None:
            ner_task.cancel()
        _trace_vector_search(
            trace,
            query_stage=q,
            tenant_id=tenant_id,
            vector_engine=vector_engine,
            vector_candidates=[],
            vector_duration_ms=vector_duration_ms,
            vector_search_call_count=vector_search_call_count,
        )
        return _CandidateStageResult(
            vector_candidates=[],
            vector_search_call_count=vector_search_call_count,
            vector_duration_ms=vector_duration_ms,
            vector_engine=vector_engine,
            bm25_variant_queries=bm25_variant_queries,
            bm25_bundle=BM25SearchBundle(
                results=[],
                has_lexical_signal=False,
                variant_queries=bm25_variant_queries or [query],
                variant_eval_count=0,
                merged_hit_count_before_cap=0,
                merged_hit_count_after_cap=0,
                winner_by_id={},
            ),
            bm25_duration_ms=0.0,
            bm25_expansion_mode=bm25_expansion_mode,
            fused_results=[],
            rrf_duration_ms=0.0,
            best_vector_similarity=None,
            best_keyword_score=None,
            rerank_lexical_query=rerank_lexical_query,
        )

    vector_embs = [emb for emb, _ in vector_candidates]
    _trace_vector_search(
        trace,
        query_stage=q,
        tenant_id=tenant_id,
        vector_engine=vector_engine,
        vector_candidates=vector_candidates,
        vector_duration_ms=vector_duration_ms,
        vector_search_call_count=vector_search_call_count,
        top_k=top_k,
    )

    rrf_candidate_pool = top_k * RRF_CANDIDATE_POOL_MULTIPLIER
    bm25_started_at = perf_counter()
    bm25_bundle = _run_bm25_search(
        vector_embs,
        query=query,
        variant_queries=bm25_variant_queries,
        top_k=rrf_candidate_pool,
        expansion_mode=bm25_expansion_mode,
    )
    bm25_duration_ms = round((perf_counter() - bm25_started_at) * 1000, 2)
    if trace is not None:
        trace.span(
            name="bm25-search",
            input={
                "query": query,
                "query_variants": bm25_bundle.variant_queries,
                "tenant_id": str(tenant_id),
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
                    bm25_bundle.results,
                    winner_by_id=bm25_bundle.winner_by_id,
                ),
                "duration_ms": bm25_duration_ms,
                "bm25_query_variant_count": len(bm25_bundle.variant_queries),
                "bm25_variant_eval_count": bm25_bundle.variant_eval_count,
                "extra_bm25_variant_evals": max(bm25_bundle.variant_eval_count - 1, 0),
                "bm25_merged_hit_count_before_cap": bm25_bundle.merged_hit_count_before_cap,
                "bm25_merged_hit_count_after_cap": bm25_bundle.merged_hit_count_after_cap,
            }
        )

    vector_for_rrf = vector_candidates[:rrf_candidate_pool]

    entity_results: list[tuple[Embedding, float]] = []
    query_entities: list[str] = []
    entity_duration_ms = 0.0
    if ner_task is not None:
        wait_started_at = perf_counter()
        try:
            query_entities = await asyncio.wait_for(
                asyncio.ensure_future(ner_task),
                timeout=settings.ner_query_timeout_seconds + 0.5,
            )
        except Exception:
            logger.warning("async_ner_task_failed", exc_info=True)
            query_entities = []
        if query_entities:
            entity_results = await async_entity_overlap_search(
                tenant_id=tenant_id,
                query_entities=query_entities,
                top_k=rrf_candidate_pool,
                db=db,
                is_sqlite=is_sqlite,
            )
        entity_duration_ms = round((perf_counter() - wait_started_at) * 1000, 2)
        if trace is not None:
            trace.span(
                name="entity-overlap-search",
                input={
                    "query": query,
                    "tenant_id": str(tenant_id),
                    "top_k": rrf_candidate_pool,
                    "query_entities": query_entities,
                },
            ).end(
                output={
                    "chunks": format_embedding_results(
                        entity_results,
                        score_name="entity_overlap_score",
                    ),
                    "duration_ms": entity_duration_ms,
                    "query_entity_count": len(query_entities),
                    "candidate_count": len(entity_results),
                }
            )
        try:
            capture_event(
                "entity_overlap.channel_used",
                distinct_id=str(tenant_id) if tenant_id else "system",
                tenant_id=str(tenant_id) if tenant_id else None,
                properties={
                    "channel": "entity_overlap",
                    "query_entity_count": len(query_entities),
                    "had_query_entities": bool(query_entities),
                    "candidate_count": len(entity_results),
                    "duration_ms": entity_duration_ms,
                },
                groups={"tenant": str(tenant_id)} if tenant_id else None,
            )
        except Exception:
            logger.warning("Failed to emit entity_overlap.channel_used", exc_info=True)

    rrf_started_at = perf_counter()
    fused_results = reciprocal_rank_fusion(
        vector_for_rrf,
        bm25_bundle.results,
        top_k=rrf_candidate_pool,
        entity_results=entity_results or None,
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
                    bm25_bundle.results,
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

    return _CandidateStageResult(
        vector_candidates=vector_candidates,
        vector_search_call_count=vector_search_call_count,
        vector_duration_ms=vector_duration_ms,
        vector_engine=vector_engine,
        bm25_variant_queries=bm25_variant_queries,
        bm25_bundle=bm25_bundle,
        bm25_duration_ms=bm25_duration_ms,
        bm25_expansion_mode=bm25_expansion_mode,
        fused_results=fused_results,
        rrf_duration_ms=rrf_duration_ms,
        best_vector_similarity=vector_candidates[0][1] if vector_candidates else None,
        best_keyword_score=bm25_bundle.results[0][1] if bm25_bundle.results else None,
        rerank_lexical_query=rerank_lexical_query,
    )


async def _async_run_quality_stage(
    *,
    final_results: list[tuple[Embedding, float]],
    tenant_id: uuid.UUID,
    db: AsyncSession,
    api_key: str,
    trace: TraceHandle | None,
) -> _QualityStageResult:
    """Quality stage: reliability assessment over the ranked results.

    ``adjudicate_contradictions`` is a sync LLM call; it runs in the default
    thread-pool executor so the event loop is not blocked.
    """
    overlap_started_at = perf_counter()
    source_overlap_detected, source_overlap_pairs = detect_source_overlaps(final_results)
    contradiction_pairs = detect_metadata_contradictions(final_results, source_overlap_pairs)

    # adjudicate_contradictions is sync (LLM call) — run in executor to avoid
    # blocking the event loop. The helper itself is CPU-light; the OpenAI call
    # inside uses the sync client, which is fine in a thread context.
    loop = asyncio.get_running_loop()
    contradiction_adjudication, contradiction_adjudication_observability = await loop.run_in_executor(
        None,
        lambda: _build_contradiction_adjudication_evidence(
            contradiction_pairs=contradiction_pairs,
            final_results=final_results,
            api_key=api_key,
        ),
    )

    reliability = build_reliability_assessment(
        top_score=final_results[0][1] if final_results else None,
        result_count=len(final_results),
        source_overlap_detected=source_overlap_detected,
        source_overlap_pairs=source_overlap_pairs,
        source_overlap_similarity_threshold=0.75,
        contradiction_pairs=contradiction_pairs,
        contradiction_adjudication=contradiction_adjudication,
        contradiction_adjudication_observability=contradiction_adjudication_observability,
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
                **build_reliability_projection(reliability),
                "duration_ms": round((perf_counter() - overlap_started_at) * 1000, 2),
            }
        )
    return _QualityStageResult(reliability=reliability)


# ── Async public orchestrators ───────────────────────────────────────────────


async def search_similar_chunks_detailed_async(
    tenant_id: uuid.UUID,
    query: str,
    top_k: int,
    db: AsyncSession,
    *,
    api_key: str,
    trace: TraceHandle | None = None,
    precomputed_query_variants: list[str] | None = None,
    precomputed_variant_vectors: list[list[float]] | None = None,
    precomputed_embedding_api_request_count: int | None = None,
    precomputed_rewritten_variant: str | None = None,
    embedding_timeout: float | None = None,
) -> SearchResultBundle:
    """Run the full hybrid retrieval pipeline and return a detailed bundle.

    The query-rewrite LLM call and embedding of base variants execute in
    parallel (``asyncio.gather``) for measurable latency savings on every turn.
    """
    retrieval_started_at = perf_counter()

    if embedding_timeout is None:
        embedding_timeout = settings.embedding_http_timeout_seconds

    q = await _async_run_query_stage(
        query=query,
        api_key=api_key,
        trace=trace,
        precomputed_query_variants=precomputed_query_variants,
        precomputed_variant_vectors=precomputed_variant_vectors,
        precomputed_embedding_api_request_count=precomputed_embedding_api_request_count,
        precomputed_rewritten_variant=precomputed_rewritten_variant,
        embedding_timeout=embedding_timeout,
    )
    c = await _async_run_candidate_stage(
        tenant_id=tenant_id,
        query=query,
        query_stage=q,
        top_k=top_k,
        db=db,
        trace=trace,
        api_key=api_key,
    )
    if not c.vector_candidates:
        return _build_empty_result_bundle(
            q, c, round((perf_counter() - retrieval_started_at) * 1000, 2)
        )

    reranker_strategy = await _async_resolve_reranker_strategy(tenant_id, db)
    r = await _async_run_ranking_stage(
        query=query,
        query_stage=q,
        candidate_stage=c,
        top_k=top_k,
        trace=trace,
        reranker_strategy=reranker_strategy,
        tenant_id=tenant_id,
        api_key=api_key,
    )
    quality = await _async_run_quality_stage(
        final_results=r.final_results,
        tenant_id=tenant_id,
        db=db,
        api_key=api_key,
        trace=trace,
    )
    return SearchResultBundle(
        results=r.final_results,
        best_vector_similarity=c.best_vector_similarity,
        vector_similarities=r.vector_similarities,
        best_keyword_score=c.best_keyword_score,
        has_lexical_signal=c.bm25_bundle.has_lexical_signal,
        query_variants=q.query_variants,
        query_script_bucket=q.query_script_bucket,
        reliability=quality.reliability,
        query_variant_count=q.query_variant_count,
        variant_mode=q.variant_mode,
        extra_variant_count=q.extra_variant_count,
        embedded_query_count=q.embedded_query_count,
        extra_embedded_queries=q.extra_embedded_queries,
        embedding_api_request_count=q.embedding_api_request_count,
        extra_embedding_api_requests=q.extra_embedding_api_requests,
        vector_search_call_count=c.vector_search_call_count,
        extra_vector_search_calls=max(c.vector_search_call_count - 1, 0),
        bm25_expansion_mode=c.bm25_expansion_mode,
        bm25_query_variant_count=len(c.bm25_bundle.variant_queries),
        bm25_variant_eval_count=c.bm25_bundle.variant_eval_count,
        extra_bm25_variant_evals=max(c.bm25_bundle.variant_eval_count - 1, 0),
        bm25_merged_hit_count_before_cap=c.bm25_bundle.merged_hit_count_before_cap,
        bm25_merged_hit_count_after_cap=c.bm25_bundle.merged_hit_count_after_cap,
        retrieval_duration_ms=round((perf_counter() - retrieval_started_at) * 1000, 2),
        query_embedding_duration_ms=q.query_embedding_duration_ms,
        vector_search_duration_ms=c.vector_duration_ms,
    )
