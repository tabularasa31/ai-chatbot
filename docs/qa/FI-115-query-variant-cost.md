# FI-115: Query Variant Retrieval Latency and Cost

## Goal

Measure whether deterministic query variant expansion is still operationally cheap once it adds extra embedding work and extra pgvector searches.

This document is the runbook and evidence template for the production review. It does not change retrieval behavior by itself.

## What to Measure

Primary comparison:

- `variant_mode=single`
- `variant_mode=multi`

Primary signals:

- p50 / p95 total trace latency
- p50 / p95 `retrieval_duration_ms`
- p50 / p95 `query_variant_count`
- p50 / p95 `extra_embedded_queries`
- p50 / p95 `extra_vector_search_calls`

Supporting signals:

- `embedding_api_request_count`
- `query-embedding.duration_ms`
- `vector-search.duration_ms`
- sampled `query-expansion.variants` payloads for noisy-tail inspection

## Where to Look

In Langfuse:

- chat flow: trace name `rag-query`
- direct search flow: trace name `search-request`

Trace metadata:

- `variant_mode`
- `query_variant_count`
- `extra_embedded_queries`
- `extra_vector_search_calls`
- `retrieval_duration_ms`

Tags:

- `variants:single`
- `variants:multi`

Span outputs:

- `query-expansion`
- `query-embedding`
- `vector-search`

## Review Procedure

1. Pick a stable production window with representative traffic volume.
2. Review `rag-query` traces first, comparing `single` vs `multi`.
3. Review `search-request` traces separately so retrieval-only requests do not distort chat latency.
4. Compare end-to-end p50/p95 first, then compare p50/p95 of `retrieval_duration_ms`.
5. Inspect a sample of slow `multi` traces and read the generated variants.
6. Classify extra variants as useful recall expansion or mostly normalization noise.

## Evidence Table

Fill this with real production numbers.

| Flow | Segment | Requests | Total p50 | Total p95 | Retrieval p50 | Retrieval p95 | Avg variants | P95 extra embedded queries | P95 extra vector calls | Notes |
|------|---------|----------|-----------|-----------|---------------|---------------|--------------|----------------------------|------------------------|-------|
| `rag-query` | `single` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `rag-query` | `multi` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `single` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `multi` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |

## Interpretation Checklist

- `multi` should justify its extra work with a small enough latency delta to remain operationally cheap.
- `retrieval_duration_ms` matters more than total latency when generation variance is high.
- `embedding_api_request_count` should usually stay flat because variants are batched; if cost rises, it will mostly show up in `extra_embedded_queries`.
- `extra_vector_search_calls` is the clearest pgvector workload amplifier.
- Slow `multi` traces with nearly duplicate variants are stronger evidence for guardrails than slow traces with clearly distinct variants.

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
- do not add guardrails preemptively
- if the first production review shows material `multi` p95 inflation, implement a `max_variants` cap first

That makes `max_variants` the default next guardrail, with normalization heuristics second and cached embeddings third.
