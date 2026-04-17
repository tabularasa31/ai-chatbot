# Gap Analyzer Phase 0/1 Boundary Notes

This module is intentionally introduced in two thin layers:

- Phase 0 locks boundaries and contracts.
- Phase 1 adds schema and command-side scaffolding only.

## Dependency rules

- Public exports from `backend.gap_analyzer` are restricted to:
  - `GapAnalyzerOrchestrator`
  - `GapSignal`
- External modules must not import internal module files directly.
- Routes and jobs will call only the orchestrator.
- Phase 1 foundation code must not import cross-domain orchestration from:
  - `backend.chat`
  - `backend.search`
  - `backend.documents`
- `domain.py` and `events.py` stay free of repo-service imports.
- If Gap Analyzer later needs corpus access, it may use:
  - a narrow retrieval adapter, or
  - its own read-only repository access to corpus tables
- It must not import broader search orchestration, trace, reliability, or tenant-search policy logic.

## Recalculate contract

- `POST /gap-analyzer/recalculate` is defined as an orchestration command.
- It must return `202 Accepted` with command status metadata.
- It is never a synchronous compute promise, even if an early implementation finishes quickly.

## Linked draft precedence

- Linked active pair uses Mode B label as the primary display and draft label.
- Mode A `example_questions` are appended to draft context when present.

## Document analysis scope

- Mode A document analysis explicitly excludes documents with `file_type = "swagger"`.
- Swagger/OpenAPI content will have a separate analyzer later and must not be folded into
  Gap Analyzer's document-side analysis by default.

## Phase 2 Follow-ups

- Revisit the current scaffold-level IVFFlat index strategy once real data exists;
  tuning or rebuild parameters will likely be needed on Postgres.
- Keep the current AST boundary tests for foundation, but refine them later if
  `TYPE_CHECKING` or conditional imports make them too noisy.

## Phase 3 Mode A Notes

- Mode A samples the tenant corpus deterministically:
  - group by `section_title`, then `page_title`, then filename/source fallback
  - take the longest chunk per group first
  - backfill by longest remaining chunks up to 40 total
- The extraction hash is computed from the sorted sampled chunk ids.
- If the extraction hash is unchanged, Mode A must:
  - skip the LLM call
  - skip rewriting `gap_doc_topics`
  - preserve the previous `extracted_at` freshness state
- Coverage and dismissal policy remain owned by Gap Analyzer itself.
- Successful triggers for Mode A currently fire best-effort after:
  - manual document embedding completion
  - successful URL source indexing completion
- Trigger execution is coalesced by tenant queue state:
  - completion paths call a queue-empty gate first
  - Mode A runs only when the tenant has no `Document` left in `processing` or `embedding`
  - and no `UrlSource` left in `queued` or `indexing`

## Phase 4 Mode B Notes

- Mode B is intentionally MVP-scoped in this phase:
  - ingest unclustered `gap_questions`
  - incrementally create or join clusters
  - update centroid, `question_count`, and `aggregate_signal_weight`
  - compute coverage against the non-swagger tenant corpus
  - transition only between basic `active` and `closed` states based on coverage
- Mode B runs best-effort after successful signal ingestion.
- In Phase 4 MVP the chat-side ingestion path only spawns an in-process background thread.
  That removes the direct latency hit from the request path, but it is still not a durable queue
  and can still compete for process resources until it moves behind a proper worker model.
- Phase 4 also uses an in-process tenant guard so one worker process does not start multiple
  concurrent Mode B follow-ups for the same tenant at once.
  This reduces duplicate cluster creation inside a single process, but it is not a cross-process
  lock and does not replace a durable queue or a database-level coordination primitive.
- Mode B cluster loading is currently narrowed to `active` and `closed` clusters only.
  That trims obvious non-active history from the in-memory matching set, but it is still not a
  paginated or batched loading strategy.
- Each Phase 4 trigger currently processes all tenant questions with `cluster_id IS NULL`.
  That is acceptable for the MVP, but large backlogs will need batching and/or queued workers.
- Phase 4 explicitly does not add:
  - Mode A ↔ Mode B linking
  - weekly/full reclustering
  - archive or inactive automation
  - trending logic
  - cross-language grouping or label regeneration policies beyond the minimal cluster label
  - durable background execution, retries, or cross-process locking for Mode B follow-ups

## Phase 5 API and UI Notes

- Phase 5 adds authenticated dashboard endpoints at:
  - `GET /gap-analyzer`
  - `POST /gap-analyzer/recalculate`
  - `POST /gap-analyzer/{source}/{gap_id}/dismiss`
  - `POST /gap-analyzer/{source}/{gap_id}/reactivate`
  - `POST /gap-analyzer/{source}/{gap_id}/draft`
- `GET /gap-analyzer` is backend-owned and returns:
  - `summary`
  - `mode_a_items`
  - `mode_b_items`
- Phase 5 now requires verified users for all dashboard reads and actions.
  This keeps operational gap-analysis data behind the same verification boundary as dismiss,
  reactivate, and recalculate flows.
- Phase 5 keeps the response split into two visible sections.
  The frontend does not merge Mode A and Mode B cards itself.
- Manual recalculation remains an orchestration command surface:
  - returns `202 Accepted`
  - starts best-effort background work
  - does not promise synchronous completion to the UI
- The dashboard sidebar badge reads from `summary.new_badge_count`.
  It now uses a lightweight `GET /gap-analyzer/summary` contract instead of paying for the full
  dashboard payload shape on every navigation mount.

## Phase 6 Linking Notes

- Mode A and Mode B links are now synchronized from embedding similarity inside Gap Analyzer itself.
- Link reset now clears all tenant topic/cluster pointers before rebuilding eligible links.
  Matching still only considers labeled Mode A topics and Mode B clusters in `active`, `closed`,
  or `dismissed`, but stale pointers on unlabeled topics and `inactive` clusters are wiped first.
- Active-list presentation is deduped with Mode B as the primary card when:
  - Mode A topic is active
  - linked Mode B cluster is active
  - and the current response is showing active Mode B items
- Archive/source-specific behavior remains separate:
  - dismissed or closed Mode B does not hide an active Mode A topic
  - dismissed Mode A still appears in dismissed/archive views even when its linked Mode B stays active
- Linked Mode B drafts append Mode A `example_questions` when present, keeping Mode B as the title/source of truth while preserving the docs-gap context.

## Phase 6C Hardening Notes

- Gap Analyzer background execution is now persisted in `gap_analyzer_jobs` with:
  - durable queued / retry / in-progress state
  - lease-based claiming
  - bounded retries for failed jobs
- Chat-signal Mode B follow-ups and manual recalculation both enqueue durable jobs rather than
  relying only on process-local threads.
- Coverage scoring now goes through a narrow repository-owned retrieval seam:
  - `vector_top_k_for_tenant(...)`
  - `bm25_match_for_tenant(...)`
- Mode A ↔ Mode B relinking now prefers a `pgvector` nearest-neighbor path on Postgres and keeps
  the Python cosine pass only as the SQLite/test fallback.
- Archive UX now exposes older archived Mode B clusters as `inactive`, and linked Mode B cards can
  render related docs-gap context inline in the dashboard.
- Postgres ANN indexes for `gap_clusters.centroid`, `gap_doc_topics.topic_embedding`, and
  `gap_questions.embedding` now use IVFFlat as the safe baseline.
  On fresh or very small datasets these indexes may stay under-trained until enough rows exist, so
  operators should plan a `REINDEX INDEX CONCURRENTLY ...` pass after the corresponding table grows
  past roughly 10k rows.

## Phase 6B Reclustering Notes

- Weekly Mode B reclustering now rebuilds recent active/closed cluster history on a best-effort
  background path.
- The rebuild scope includes:
  - recent unclustered questions inside the current reclustering window
  - all questions from active/closed clusters that were touched by recent questions
- Dismissed Mode B clusters are intentionally excluded from rebuild scope so dismissal semantics stay
  stable.
- Rebuilt Mode B clusters are created with `is_new = false` to avoid re-badging historical churn as
  newly discovered gaps.
- Archive UX now exposes an explicit `archived` filter:
  - Mode A `archived` maps to dismissed topics
  - Mode B `archived` maps to closed plus dismissed clusters
- Archive presentation remains source-specific:
  - linked archived Mode B items do not suppress active Mode A topics
  - active-list suppression still applies only when linked Mode B is active
  - the dashboard archive view now renders Mode B `closed` and `dismissed` buckets separately so
    operators can review resolved vs manually hidden clusters without mixing them together

## Residual Trade-Offs

- Mode B now filters blank question texts before batch embedding so vector writes stay aligned.
  Fully blank questions remain unembedded and unclustered until later sanitation or admin cleanup.
- Cluster loading for Mode B is not paginated yet.
  Tenants with very large numbers of active/closed clusters will still need batching or a narrower
  candidate-selection strategy in a later phase.
- The in-app runner is now durable at the job-state level, but fully automatic recovery after a
  whole-app outage still depends on the next enqueue/manual trigger unless a dedicated external
  scheduler or worker deployment is added.
- The queue-empty gate is best-effort across short-lived sessions.
  A new indexing job could start between the queue check and the follow-up Mode A run, so the
  coalescing behavior is intentionally helpful rather than strictly serialized.
- `UrlSource` states such as `stale`, `paused`, and `error` do not block Mode A execution.
  This is intentional so a problematic source does not prevent gap analysis from running against
  the rest of the tenant corpus.
- `update_mode_b_question_embedding(...)` now tolerates missing questions with a warning rather than
  aborting the whole follow-up job.
  If this path starts triggering in real traffic, it should grow tenant-aware logging and/or a
  dedicated metric so silent data-shape bugs are easier to spot.

## Future Cleanup

- `backend/gap_analyzer/prompts.py` still parses raw JSON responses from the LLM manually.
  If the candidate schema grows, this should move to Pydantic-backed parsing or OpenAI
  Structured Outputs for stricter validation and less handwritten shape checking.
- `_vector_from_unknown(...)` in `backend/gap_analyzer/orchestrator.py` is still a permissive
  normalization helper. Longer term, the repository boundary should preferably return typed
  vectors directly so Mode A does not need to coerce unknown vector payloads at runtime.
- Phase 6 UI copy such as `also missing in docs` and the linked-draft markdown section currently
  stays in English.
  If the dashboard UI gets localized more broadly, these strings should move behind the same
  localization strategy instead of remaining hard-coded.

## Post-Plan Hardening and Polish

The rollout plan itself is complete. The remaining work is now hardening and polish:

- move durable job draining from the in-app runner to a dedicated external worker or scheduler so
  queued work resumes automatically after a full outage
- optimize larger-tenant Mode B processing with more batching and/or candidate narrowing
- localize remaining Gap Analyzer dashboard copy and draft headings
- optionally deepen the linked-card presentation if operators need stronger linked-pair semantics
- optionally make `inactive` a more explicit first-class UX state rather than mainly an archive
  bucket
