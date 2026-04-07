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
