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
- `quick-answers-check` placeholder stage
- `query-expansion`
- `vector-search`
- `bm25-search`
- `rrf-fusion`
- `reranking`
- `language-boost`
- `mmr-pass`
- `conflict-detection`
- `llm-generation`
- reliability score propagation
- deferred sampling with promotion for low-reliability and escalated requests

Implemented as heuristic/interim behavior:

- query expansion
- reranking
- language detection/boost
- MMR similarity scoring
- conflict detection
- cost estimation
- in-process tenant sampling counters

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
Generation tracing now includes system prompt, context chunk payload, and user message structure.

`AC-6` Partial.
Token usage is captured; `cost_usd` is currently a coarse estimate.

`AC-7` Covered in code.
Tenant tags and metadata are attached to traces.

`AC-8` Mostly covered.
Tracing uses redacted user content from the existing redaction layer.

`AC-9` Not yet benchmarked.
No explicit latency benchmark has been added yet.

`AC-10` Partial.
Quick Answers stage exists as a placeholder, but there is no real quick-answer store/matcher yet.

`AC-11` Not yet verified.
No restart/recovery test against a real Langfuse deployment has been added yet.

`AC-12` Partial.
Sampling logic and forced-promotion rules exist, but shared/distributed rate tracking is not implemented.

## Remaining Gaps

- real Langfuse deployment validation in staging
- documented retention/TTL configuration in Langfuse itself
- end-to-end test that a submitted query appears in Langfuse
- worker/crawl job instrumentation
- production-grade cost model
- Redis/shared-store tenant counters for multi-instance deployments
- true quick-answer implementation
- model-backed reranking/conflict confirmation if we decide the heuristics are insufficient
