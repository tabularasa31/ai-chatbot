# FI-115: Query Variant Retrieval Latency and Cost

## Goal

Measure whether deterministic query variant expansion is still operationally cheap once it adds extra embedding work and extra pgvector searches, and whether symmetric BM25 variant evaluation is worth promoting beyond the current asymmetric default.

This document is the runbook and evidence template for the production review. It does not change retrieval behavior by itself.

## What to Measure

Primary comparison:

- `variant_mode=single`
- `variant_mode=multi`
- `bm25_expansion_mode=asymmetric`
- `bm25_expansion_mode=symmetric_variants`

Primary signals:

- p50 / p95 total trace latency
- p50 / p95 `retrieval_duration_ms`
- p50 / p95 `query_variant_count`
- p50 / p95 `extra_embedded_queries`
- p50 / p95 `extra_embedding_api_requests`
- p50 / p95 `extra_vector_search_calls`
- p50 / p95 `bm25_query_variant_count`
- p50 / p95 `bm25_variant_eval_count`
- p50 / p95 `extra_bm25_variant_evals`
- p50 / p95 `bm25_merged_hit_count_before_cap`
- p50 / p95 `bm25_merged_hit_count_after_cap`

Supporting signals:

- `embedding_api_request_count`
- `query-embedding.duration_ms`
- `vector-search.duration_ms`
- `bm25-search.duration_ms`
- fused ranking deltas at final `top_k`
- sampled `query-expansion.variants` payloads for noisy-tail inspection
- sampled `bm25-search.query_variants` plus winner provenance for merge-debug inspection

## Where to Look

In Langfuse:

- chat flow: trace name `rag-query`
- direct search flow: trace name `search-request`

Trace metadata:

- `variant_mode`
- `query_variant_count`
- `extra_embedded_queries`
- `extra_embedding_api_requests`
- `extra_vector_search_calls`
- `bm25_expansion_mode`
- `bm25_query_variant_count`
- `bm25_variant_eval_count`
- `extra_bm25_variant_evals`
- `bm25_merged_hit_count_before_cap`
- `bm25_merged_hit_count_after_cap`
- `retrieval_duration_ms`

Tags:

- `variants:single`
- `variants:multi`

Span outputs:

- `query-expansion`
- `query-embedding`
- `vector-search`
- `bm25-search`
- `rrf-fusion`

## Review Procedure

1. Pick a stable production window with representative traffic volume.
2. Review `rag-query` traces first, comparing `single` vs `multi`.
3. Review `search-request` traces separately so retrieval-only requests do not distort chat latency.
4. Compare end-to-end p50/p95 first, then compare p50/p95 of `retrieval_duration_ms`.
5. Compare `asymmetric` vs `symmetric_variants` using the same candidate-pool construction policy, so only lexical expansion behavior changes.
6. Inspect a sample of slow `multi` and `symmetric_variants` traces and read the generated variants.
7. Classify extra variants as useful recall expansion or mostly normalization noise.
8. Check whether extra lexical mass changes the fused ranking at user-visible cutoffs such as final `top_k`, not just whether it exists before cap.

## Evidence Table

Fill this with real production numbers.

| Flow | Segment | Requests | Total p50 | Total p95 | Retrieval p50 | Retrieval p95 | Avg variants | P95 extra embedded queries | P95 extra vector calls | Notes |
|------|---------|----------|-----------|-----------|---------------|---------------|--------------|----------------------------|------------------------|-------|
| `rag-query` | `single` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `rag-query` | `multi` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `single` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `multi` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |

### Symmetric BM25 Comparison

| Flow | BM25 mode | Requests | Retrieval p50 | Retrieval p95 | Avg BM25 variants | P95 extra BM25 evals | Avg merged hits before cap | Avg merged hits after cap | Win/loss queries | Top-k notes |
|------|-----------|----------|---------------|---------------|-------------------|----------------------|----------------------------|---------------------------|------------------|-------------|
| `rag-query` | `asymmetric` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `rag-query` | `symmetric_variants` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `asymmetric` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `symmetric_variants` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |

## Interpretation Checklist

- `multi` should justify its extra work with a small enough latency delta to remain operationally cheap.
- `retrieval_duration_ms` matters more than total latency when generation variance is high.
- `embedding_api_request_count` should usually stay flat because variants are batched.
- `extra_embedding_api_requests` should usually stay at `0`; if it rises, batching or retries changed and transport overhead is no longer flat.
- `extra_vector_search_calls` is the clearest pgvector workload amplifier.
- `bm25_variant_eval_count` is a count of repeated lexical scoring passes over one shared in-memory candidate corpus, not a second corpus-acquisition search.
- `bm25_merged_hit_count_before_cap` tells you whether symmetric lexical expansion found more lexical mass at all.
- `bm25_merged_hit_count_after_cap` tells you how much of that lexical mass actually survived into RRF.
- Slow `multi` traces with nearly duplicate variants are stronger evidence for guardrails than slow traces with clearly distinct variants.
- Extra lexical hits matter only if they improve final fused ranking at useful cutoffs; “more before cap” by itself is not enough to justify the mode switch.

## Guardrails to Consider If Needed

- `max_variants` cap:
  - first choice if p95 inflation is clearly driven by fan-out
  - simplest safety control with the least product ambiguity

- stronger normalization / deduping:
  - use if many extra variants are punctuation, whitespace, or token-order noise
  - best when cost comes from low-value expansions rather than genuinely different phrasings

- cached query embeddings:
  - use only if the same normalized variant sets recur often enough in production
  - adds complexity, so prefer it after confirming repeat-hit patterns

## Current Recommendation

Current behavior should not yet be treated as proven cheap.

Until this evidence table is filled with production p50/p95 data, the safest position is:

- keep the current behavior enabled for measurement
- keep `bm25_expansion_mode=asymmetric` as the default
- do not add guardrails preemptively
- if the first production review shows material `multi` p95 inflation, implement a `max_variants` cap first
- do not promote `symmetric_variants` unless it wins on representative fixtures, improves fused top-k outcomes often enough to matter, and does so with acceptable latency overhead plus no control-case regressions

That makes `max_variants` the default next guardrail, with normalization heuristics second and cached embeddings third.
