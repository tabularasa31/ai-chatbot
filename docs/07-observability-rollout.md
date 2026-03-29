# Observability Rollout

This document tracks the current Langfuse-oriented observability implementation against the RAG observability spec and captures the deployment checklist for staging/production rollout.

## Railway Checklist

Add a dedicated `langfuse` service:

- Docker image: `langfuse/langfuse:latest`
- private network access from `chat9-api`
- public URL only for authenticated team access

Add a dedicated `langfuse-postgres` database:

- do not reuse the primary app Postgres
- configure retention separately from app data

Configure `chat9-api` env:

- `LANGFUSE_HOST`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `OBSERVABILITY_CAPTURE_FULL_PROMPTS`
- `FULL_CAPTURE_MODE` — when `true` (default), every eligible request is traced and adaptive sampling is skipped; set `false` in production at scale to use tenant heuristics below
- `TRACE_SAMPLE_RATE`
- `TRACE_HIGH_VOLUME_THRESHOLD`
- `TRACE_HIGH_VOLUME_SAMPLE_RATE`
- `TRACE_NEW_TENANT_THRESHOLD`
- `TRACE_RATE_WINDOW_SECONDS`

Configure `langfuse` env:

- `DATABASE_URL`
- `NEXTAUTH_SECRET`
- `NEXTAUTH_URL`
- `SALT`
- `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`
- `LANGFUSE_INIT_PROJECT_SECRET_KEY`
- `LANGFUSE_INIT_ORG_NAME`
- `LANGFUSE_INIT_PROJECT_NAME`

## Current Coverage

Implemented in code:

- optional Langfuse initialization with no-op fallback
- root `rag-query` traces
- root `search-request` traces for direct `/search` calls
- `quick-answers-check` placeholder stage
- `query-expansion`
- `query-embedding`
- `vector-search`
- `bm25-search`
- `rrf-fusion`
- `reranking`
- `script-boost`
- `mmr-pass`
- `source-overlap-check`
- `llm-generation`
- reliability score propagation
- contradiction observability projection (`contradiction_detected`, `contradiction_count`, `contradiction_pair_count`, `contradiction_basis_types`)
- optional contradiction **adjudication** projection fields (`contradiction_adjudication_*`) sourced from the observability run, not from canonical `evidence` alone — see `docs/04-features.md` (Retrieval contradiction observability + shadow adjudication)
- deferred sampling with promotion for low-reliability and escalated requests
- promotion metadata for both deferred and already-sampled traces

Implemented as heuristic/interim behavior:

- query expansion
- reranking
- script bucket detection/boost
- MMR similarity scoring
- cross-document overlap detection
- cost estimation
- in-process tenant sampling counters
- bounded in-memory deferred trace buffering

## Trace sampling

- **`FULL_CAPTURE_MODE`:** `true` records 100% of traces that pass the Langfuse client gate (early-stage / low-traffic). `false` restores adaptive sampling: new-tenant boost, high-volume downsample, default `TRACE_SAMPLE_RATE`, and `force_trace` overrides — unchanged from prior behavior.
- **Analysis fields:** each materialized root trace includes metadata `sampling_mode` (`full_capture` or `adaptive`) alongside existing `sampling_reason` (`full_capture`, `forced`, `new-tenant`, `high-volume`, `default`). A tag `sampling_mode:full_capture` or `sampling_mode:adaptive` is merged into the trace tag list for Langfuse filters.

## Query Variant Cost Metrics

Query variant expansion is now measured explicitly on every traced retrieval request.

Parent trace metadata and tags:

- `variant_mode`: `single` or `multi`
- `query_variant_count`
- `extra_embedded_queries`
- `extra_embedding_api_requests`
- `extra_vector_search_calls`
- `retrieval_duration_ms`
- tag: `variants:single` or `variants:multi`

`query-expansion` span output:

- `variants`
- `query_variant_count`
- `variant_mode`
- `extra_variant_count`

`query-embedding` span output:

- `embedded_query_count`
- `extra_embedded_queries`
- `embedding_api_request_count`
- `extra_embedding_api_requests`
- `duration_ms`

`vector-search` span output:

- `vector_search_call_count`
- `extra_vector_search_calls`
- `duration_ms`
- existing chunk preview payloads

Interpretation:

- primary cost signal: `extra_embedded_queries`
- secondary transport signal: `embedding_api_request_count`
- transport delta signal: `extra_embedding_api_requests`
- pgvector fan-out signal: `extra_vector_search_calls`
- latency should be compared at two levels:
  - end-to-end request trace latency
  - retrieval-only `retrieval_duration_ms`

This split matters because multi-variant retrieval can be meaningfully slower even when generation latency hides it at the whole-request level.

## Query Variant Comparison Workflow

Use Langfuse filters/grouping with the trace name kept separate by flow:

- `rag-query` for chat requests
- `search-request` for direct `/search`

Primary segmentation:

- compare `variant_mode=single` vs `variant_mode=multi`

Recommended review steps:

1. Filter a stable time window with representative traffic.
2. For each trace family (`rag-query`, `search-request`), compare p50/p95 total latency for `single` vs `multi`.
3. Compare p50/p95 of `retrieval_duration_ms` for the same split.
4. Check work amplification:
   - avg/p95 `query_variant_count`
   - avg/p95 `extra_embedded_queries`
   - avg/p95 `extra_embedding_api_requests`
   - avg/p95 `extra_vector_search_calls`
5. Sample a few high-tail `multi` traces and inspect `query-expansion` outputs to see whether extra variants are meaningfully different or just punctuation/token-order noise.

Decision rule:

- acceptable: `multi` traces show modest tail inflation and the added retrieval work is proportional to likely recall benefit
- needs guardrails: `multi` traces show material p95 inflation or repeated noisy expansions with low retrieval value

Current MMR note:
the `mmr-pass` stage uses token-set Jaccard similarity over the post-rerank pool and recomputes pairwise comparisons as chunks are selected. Normal current usage is small (`/search` defaults to `top_k=3`, chat retrieval uses `top_k=5`, so MMR usually sees about 6-10 candidates after script boost). That remains acceptable for roughly up to 50 MMR candidates; around 100 candidates it becomes a noticeable hot-path cost, and the schema-allowed worst case (`top_k=100` => up to 200 MMR candidates before selection) is outside the intended operating range until we add a cap or replace the heuristic.

## Acceptance Criteria Status

`AC-1` Partial.
Trace structure exists in code, but there is no integration test against the Langfuse API yet.

`AC-2` Mostly covered.
Vector-search logs chunks with previews and similarity scores.

`AC-3` Partial.
Reranking exists, but it is heuristic rather than cross-encoder based.

`AC-4` Covered in current implementation.
MMR pass records replacements and reasons.

`AC-5` Mostly covered.
Generation tracing now includes structured system/user messages, with full prompt/context capture gated by configuration.

`AC-6` Partial.
Token usage is captured; `cost_usd` is still a coarse estimate, but retrieval now records extra embedded inputs and vector-search fan-out caused by query variants.

`AC-7` Covered in code.
Tenant tags and metadata are attached to traces.

`AC-8` Mostly covered.
Tracing uses redacted user content from the existing redaction layer.

`AC-9` Partial.
Latency segmentation for single-vs-multi variant retrieval is now in trace metadata and spans, but production data still needs to be collected and reviewed.

`AC-10` Partial.
Quick Answers stage exists as a placeholder, but there is no real quick-answer store/matcher yet.

`AC-11` Not yet verified.
No restart/recovery test against a real Langfuse deployment has been added yet.

`AC-12` Partial.
Sampling logic and forced-promotion rules exist; an explicit `FULL_CAPTURE_MODE` switch can disable adaptive sampling for full capture environments. Shared/distributed rate tracking is not implemented.

## Remaining Gaps

- real Langfuse deployment validation in staging
- documented retention/TTL configuration in Langfuse itself
- end-to-end test that a submitted query appears in Langfuse
- worker/crawl job instrumentation
- production-grade cost model
- Redis/shared-store tenant counters for multi-instance deployments
- true quick-answer implementation
- model-backed reranking and true contradiction detection if we decide the heuristics are insufficient
- actual production review of FI-115 evidence and a follow-up guardrail decision if multi-variant tails are too expensive
