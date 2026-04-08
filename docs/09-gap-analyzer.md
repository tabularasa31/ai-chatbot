# Gap Analyzer

## Purpose

Gap Analyzer is the bounded backlog module under `backend/gap_analyzer/` that converts weak documentation coverage and repeated user pain into an operator-facing dashboard at `/gap-analyzer`.

It is not part of the live answer-generation loop. The chat pipeline emits signals into it, and operators use its output to prioritize documentation work.

## Scope

Gap Analyzer currently ships two coordinated pipelines:

- `Mode A`: docs-side discovery over the indexed tenant corpus
- `Mode B`: user-question clustering from low-confidence and failure-adjacent chat outcomes

The module owns its own domain types, repository logic, orchestrator, routes, and prompt logic. External callers should treat `GapAnalyzerOrchestrator` as the public seam.

## Boundaries

Public module exports are intentionally narrow:

- `GapAnalyzerOrchestrator`
- `GapSignal`

Boundary rules:

- routes and jobs call only the orchestrator
- `domain.py` and `events.py` stay free of service-layer imports
- Gap Analyzer does not import the full search orchestration stack
- corpus access is owned through Gap Analyzer's repository and a narrow retrieval-style seam

This keeps gap-analysis logic from turning into a second copy of the broader search module.

## Tenant identity

Gap Analyzer uses the tenant's internal `client.id` UUID as its canonical storage identity.

Routes first resolve the authenticated user's client, then pass that tenant UUID into the orchestrator. The frontend never sends raw SQL-shaped identifiers from the dashboard.

## Data model

The module persists and reads from these main storage surfaces:

- `gap_questions`: stored Mode B source questions and correlation metadata
- `gap_clusters`: Mode B clustered gaps
- `gap_doc_topics`: Mode A docs-side gap topics
- `gap_dismissals`: dismissal store used to preserve suppression state
- `gap_question_message_links`: exact user/assistant/message correlation for feedback rewiring
- `gap_unified`: read-side view that normalizes Mode A and Mode B inventory

At the API layer the frontend receives backend-owned DTOs from `backend/gap_analyzer/schemas.py`, not raw database rows.

## Mode A

Mode A looks for under-covered documentation topics inside the tenant corpus.

Current behavior:

- runs against the indexed tenant corpus
- excludes Swagger/OpenAPI documents from the docs-side analysis path
- samples corpus chunks deterministically
- computes an extraction hash from the sampled chunk set
- skips write churn when the extraction hash is unchanged
- validates candidates against Gap Analyzer's own coverage score before persisting
- keeps dismissals in a separate store so re-indexing does not automatically resurrect hidden topics

Operational intent:

- avoid expensive or noisy reruns when nothing materially changed
- keep trust-sensitive docs-side candidates stable across re-indexes

## Mode B

Mode B converts chat-side pain into reusable backlog items.

Signals are created after the final assistant outcome is known. Typical sources include:

- low-confidence answers
- fallback outcomes
- rejection paths
- escalations
- thumbs-down feedback

Important implementation details:

- one stored question signal is created per relevant user-question event
- exact message correlation is persisted in `gap_question_message_links`
- thumbs-down rewires the exact stored signal instead of guessing from chronology
- unclustered questions are either joined into an existing cluster or create a new cluster
- coverage is re-evaluated against the current tenant corpus
- weekly reclustering rebuilds recent active and closed history to reduce cluster drift

## Linking and read-side dedupe

Mode A and Mode B are linked from embedding similarity inside Gap Analyzer itself.

Read-side behavior:

- active linked pairs dedupe to one primary visible item
- the primary visible item is Mode B
- Mode A context can still influence draft generation and UI hints
- archive behavior remains source-specific

Current dashboard/UI behavior does not render a radically different linked-card component. Instead, linked active Mode B items surface as the primary card and can show the `also missing in docs` hint.

## Lifecycle semantics

Current user-visible statuses are:

- `active`
- `closed`
- `dismissed`

Source-specific meaning matters:

- Mode A archive inventory is effectively dismissed docs-side topics
- Mode B archive inventory includes closed and dismissed clusters
- an archived Mode B item does not suppress an active Mode A topic
- active-list suppression only applies when the linked Mode B item is active

This is why the dashboard has both `Active view` and `Archive view`, and why the archive UI splits Mode B closed and dismissed buckets.

## API surface

Authenticated verified-user routes live in `backend/gap_analyzer/routes.py`.

Current endpoints:

- `GET /gap-analyzer`
- `POST /gap-analyzer/recalculate`
- `POST /gap-analyzer/{source}/{gap_id}/dismiss`
- `POST /gap-analyzer/{source}/{gap_id}/reactivate`
- `POST /gap-analyzer/{source}/{gap_id}/draft`

Key contract notes:

- `GET /gap-analyzer` returns backend-owned `summary`, `mode_a_items`, and `mode_b_items`
- `POST /gap-analyzer/recalculate` is an orchestration-style command and returns `202 Accepted`
- the current sidebar badge still reads from the full `GET /gap-analyzer` payload
- there is no separate lightweight `GET /gap-analyzer/summary` endpoint yet in the current code

## Background execution

Gap Analyzer background work currently runs through best-effort in-process entrypoints in `backend/gap_analyzer/jobs.py`.

That means:

- manual recalculation schedules best-effort background tasks
- chat-side follow-up work can also trigger best-effort background execution
- failures are logged and rolled back at the DB session level
- this is not yet a durable queue with cross-process coordination or retries

This trade-off is important for operators and future maintainers: the API contract is orchestration-style, but the current worker implementation is still lightweight.

## Retrieval and coverage seams

Gap Analyzer intentionally avoids importing the full search orchestration flow.

Instead, the module owns narrower repository-level helpers for:

- embeddings access
- corpus reads
- coverage scoring inputs
- linking similarity calculations

This keeps Gap Analyzer decoupled from broader search-policy and trace logic while still letting it reason about corpus coverage.

## Frontend contract

The dashboard page lives at `frontend/app/(app)/gap-analyzer/page.tsx`.

Current UI capabilities:

- active and archive views
- separate Mode A and Mode B sections
- filter dropdowns per section
- recalculate action
- dismiss / reactivate actions
- transient draft preview
- archive grouping for Mode B closed vs dismissed

The frontend does not merge raw rows on its own. It renders the backend-owned response shape.

## Testing focus

Gap Analyzer coverage currently has dedicated tests by phase, including:

- schema and boundary coverage
- exact message-link feedback rewiring
- Mode A gating and hash no-op behavior
- Mode B clustering and coverage transitions
- dashboard API contract
- linking and reclustering regression checks

See:

- `tests/test_gap_analyzer_foundation.py`
- `tests/test_gap_analyzer_phase2.py`
- `tests/test_gap_analyzer_phase3.py`
- `tests/test_gap_analyzer_phase4.py`
- `tests/test_gap_analyzer_phase5.py`
- `tests/test_gap_analyzer_phase6b.py`

## Known follow-ups

The current implementation still leaves room for future hardening:

- durable queueing and retry semantics for background work
- a lighter summary-only endpoint for sidebar badge reads
- richer linked-card presentation if the UI needs stronger pair semantics
- additional lifecycle exposure if `inactive` becomes a first-class visible state
