# AGENTS.md — context for Cursor and agents

This file defines the stack, repository layout, and conventions. Keep it updated when architecture changes materially.

**Cursor:** extra rules live in `.cursor/rules/*.mdc` — `project-basics` (always on), file-scoped: `backend-data-layer`, `backend-http`, `backend-schemas`, `frontend-app`, `frontend-typescript`. Avoid duplicating long sections between AGENTS and `.mdc`; if they diverge, update both or pick a single source of truth.

---

## Stack

| Layer | Technologies |
|-------|----------------|
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy, Alembic |
| Database | PostgreSQL 14+ with **pgvector** (production); tests may use SQLite with simplified types and Python cosine candidate acquisition, then the shared BM25/RRF/reranking retrieval flow (see `backend/models.py`, `backend/search/service.py`) |
| Auth | JWT, bcrypt, email verification (Brevo HTTP API); successful `/auth/verify-email` provisions the user's single client/workspace; dashboard / tenant JWT APIs require a verified user via `require_verified_user`; internal eval QA (`/eval/*`) uses a separate signing secret `EVAL_JWT_SECRET` |
| LLM | OpenAI API (per-client key / client settings; see `backend/core/openai_client.py`) |
| Frontend | Next.js 14 (App Router), React 18, TypeScript, TailwindCSS, Radix Slot, framer-motion |
| Widget | Dedicated Next.js routes (`/widget`), API calls; some public endpoints in `backend/routes/widget.py` and `backend/widget/` |

Gap Analyzer is implemented as a bounded backend module under `backend/gap_analyzer/` with a dashboard page at `/gap-analyzer`. It has two pipelines:

- `Mode A` — documentation-side gap discovery from the indexed corpus, with deterministic sampling, hash-based no-op skips, coverage gating, dismissal persistence, and Swagger/OpenAPI sources excluded from this document-analysis path
- `Mode B` — user-question clustering from low-confidence / fallback / rejected / escalated chat signals, with exact feedback correlation, incremental clustering, periodic full reclustering, inactive archive aging, and Mode A ↔ Mode B linking/dedupe on the read side

Gap Analyzer orchestration is backed by durable `gap_analyzer_jobs` rows with claim/retry state. The dashboard/API surface includes `GET /gap-analyzer`, `GET /gap-analyzer/summary`, `POST /gap-analyzer/recalculate`, dismiss/reactivate, and draft-generation endpoints. Linked active Mode B items are the primary surface; archive views stay source-specific.

**Package layout** (`backend/gap_analyzer/`):

| File / dir | Role |
|---|---|
| `orchestrator.py` | `GapAnalyzerOrchestrator` — thin shell, delegates to pipelines |
| `repository.py` | `SqlAlchemyGapAnalyzerRepository` — thin proxy, delegates to `_repo/` |
| `_repo/` | Persistence subpackage (8 focused ops modules, see below) |
| `pipelines/mode_a.py` | Mode A coverage pipeline helpers |
| `pipelines/mode_b.py` | Mode B clustering state machine |
| `pipelines/link_sync.py` | Mode A ↔ Mode B vector link sync |
| `pipelines/drafts.py` | Markdown draft builders |
| `read_models.py` | Response-shape builders (DB rows → API objects) |
| `_math.py` | Pure vector/token math (no I/O) |
| `_classification.py` | Gap classification and status helpers |
| `routes.py`, `schemas.py`, `enums.py`, `events.py`, `domain.py`, `prompts.py`, `jobs.py` | Standard domain slices |

`_repo/` submodules (all imported only through `repository.py`):

| Submodule | Contents |
|---|---|
| `records.py` | Public dataclass records (no internal deps) |
| `capabilities.py` | Dialect capabilities, enum/value helpers, shared utils |
| `bm25_cache.py` | Thread-safe BM25 LRU/TTL cache + `_Bm25CacheOps` |
| `signals.py` | `_SignalsOps` — signal ingestion/query |
| `mode_a_queries.py` | `_ModeAQueriesOps` — corpus/topic queries |
| `mode_b_queries.py` | `_ModeBQueriesOps` — cluster/question queries + vector/BM25 |
| `job_queue.py` | `_JobQueueOps` — job lifecycle + helpers |
| `summary.py` | `_SummaryOps` — gap summary aggregation |

**Import-graph conventions** (design intent, not test-enforced):
- `pipelines/` must not import `orchestrator`
- `_repo/` must not import `pipelines/` or `orchestrator`

Chat responses now support structured clarification outcomes in addition to plain answers. The canonical public chat/message types are:

- `answer`
- `clarification`
- `partial_with_clarification`

For `/chat` and `/widget/chat`, the response body uses the canonical `text` field only. Legacy aliases (`answer` on `/chat`, `response` on `/widget/chat`) have been removed; consumers must read `text`. Typed behavior lives in `backend/chat/service.py`, `backend/chat/schemas.py`, and the widget/frontend transport types.

Language behavior is now:

- before the first real user question, default greeting and other fallback-only turns use `user_context.locale -> user_context.browser_locale -> English`
- after the first real question, bot replies should follow the language of the question itself
- soft rejections / clarification prompts / escalation fallbacks are localized through the shared helper in `backend/chat/language.py`

Deployment: typically Railway (API + Postgres), frontend on Vercel. Environment variables: `backend/core/config.py` (required: `DATABASE_URL`, `JWT_SECRET`, `EVAL_JWT_SECRET`, etc.). Langfuse / trace sampling knobs include `FULL_CAPTURE_MODE` and `TRACE_*` (see `docs/07-observability-rollout.md`).

---

## Repository layout

```
ai-chatbot/
├── AGENTS.md                 # this file
├── alembic.ini               # Alembic; script_location = backend/migrations
├── backend/                  # Python app package (imports: backend.*)
│   ├── main.py               # FastAPI entry, router wiring
│   ├── models.py             # SQLAlchemy models and Base (single models file)
│   ├── core/                 # db, config, security, limiter, utils, openai_client, …
│   ├── auth/, chat/, clients/, documents/, embeddings/, search/, escalation/, admin/, widget/
│   │   ├── routes.py         # HTTP routes (often APIRouter)
│   │   ├── service.py        # business logic, DB access
│   │   └── schemas.py        # Pydantic request/response schemas
│   ├── routes/               # cross-cutting: public, widget
│   └── migrations/           # Alembic: versions/, env.py
├── frontend/                 # Next.js app
│   └── app/                  # App Router: (marketing), (auth), (app), widget/, layout.tsx
├── docs/                     # product and technical docs (no need to copy the full stack here)
└── …                         # other assets (widget scripts, etc.)
```

Run the API from the repo root with `PYTHONPATH` pointing at the root so `backend.*` imports resolve.

---

## Naming conventions

### Python (backend)

- Modules and functions: `snake_case`.
- SQLAlchemy model classes: `PascalCase` (`User`, `EscalationTicket`).
- DB tables: **plural, snake_case** (`users`, `clients`, `escalation_tickets`, `user_sessions`).
- Pydantic API schemas: suffixes like `Request` / `Response` or descriptive names (`ChatMessageLogItem`), in the domain’s `schemas.py`.
- Routers: `*_router`; path prefixes wired in `main.py` (e.g. `/auth`, `/chat`).
- Public string IDs for clients, etc.: follow existing patterns (`generate_public_id`, prefixes like `ch_` — do not invent new ones without a reason).

### TypeScript / React (frontend)

- Components and types: `PascalCase`.
- Page and layout files: Next.js conventions (`page.tsx`, `layout.tsx`).
- Utilities and functions: `camelCase`.
- Styles: Tailwind classes in JSX; avoid adding new CSS frameworks unless necessary.

### API

- REST-style paths: lowercase, hyphens or path segments consistent with existing routers.
- Bodies and responses via Pydantic; FastAPI errors: `HTTPException`, consistent `detail` messages.
- Chat endpoints may return structured clarification payloads. Keep `message_type` and `clarification` in sync across backend schemas, service-layer literals, and frontend transport/widget types.
- Knowledge profile terminology: prefer **`topics`** for extracted documentation themes shown in the dashboard/API. The underlying DB/storage layer still uses the `modules` field name, but user-facing docs and contracts should call these extracted items `topics`, not product modules.

---

## Database usage

In short: models live only in `backend/models.py`; HTTP handlers use `Depends(get_db)`; services receive `Session` from the router; background jobs and scripts use `SessionLocal`; schema changes only via Alembic from the repo root.

### Alembic safety rule

- Alembic `revision` identifiers **must fit into `alembic_version.version_num`**.
- In this project, treat **32 characters as the hard maximum** for every new `revision` string.
- Prefer short snake_case ids such as `phase4_user_sessions_active_v1`, not long descriptive ids that can exceed 32 chars.
- Every schema PR should run the migration guard test that scans `backend/migrations/versions/*.py` and fails if a `revision` is too long.

**Full checklist** — `.cursor/rules/backend-data-layer.mdc` (applies when working under `backend/**/*.py`).

---

## Other editing guidelines

See `.cursor/rules/project-basics.mdc` (always applied). Feature details and regression scenarios — `docs/` (`docs/04-features.md`, `docs/qa/`).
Developer test command groups for local/CI runs are documented in `docs/06-developer-test-runbook.md`.
