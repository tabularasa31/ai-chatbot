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
