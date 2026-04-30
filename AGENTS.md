# AGENTS.md — context for Cursor and agents

This file defines the stack, repository layout, and conventions. Keep it updated when architecture changes materially.

**Cursor:** extra rules live in `.cursor/rules/*.mdc` — `project-basics` (always on), file-scoped: `backend-data-layer`, `backend-http`, `backend-schemas`, `frontend-app`, `frontend-typescript`. Avoid duplicating long sections between AGENTS and `.mdc`; if they diverge, update both or pick a single source of truth.

---

## Stack

| Layer | Technologies |
|-------|----------------|
| Backend | Python 3.11, FastAPI 0.111, Pydantic v2 (2.5), SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL 15 with **pgvector** (production, see `docker-compose.yml`); tests may use SQLite with simplified types and Python cosine candidate acquisition, then the shared BM25/RRF/reranking retrieval flow (see `backend/models.py`, `backend/search/service.py`) |
| Auth | JWT, bcrypt, email verification (Brevo HTTP API); successful `/auth/verify-email` provisions the user's single tenant/workspace; dashboard / tenant JWT APIs require a verified user via `require_verified_user` |
| LLM | OpenAI API (per-tenant key; see `backend/core/openai_client.py`) |
| Frontend | Next.js 14 (App Router), React 18, TypeScript, TailwindCSS, Radix Slot, framer-motion, fumadocs-ui for content |
| Widget | Dedicated Next.js routes (`/widget`), API calls; public endpoints in `backend/routes/widget.py` and `backend/widget/` |
| Observability | Langfuse (LLM tracing), PostHog (product analytics), Sentry (errors) — see `backend/observability/` |

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
| `bm25_cache.py` | Thread-safe BM25 LRU/TTL cache + `_load_or_cache_bm25_corpus` |
| `job_queue_helpers.py` | Constants and pure helpers for job-queue logic |
| `job_retry.py` | Retry policy: effective max attempts, delay with jitter |

**Import-graph conventions** (design intent, not test-enforced):
- `pipelines/` must not import `orchestrator`
- `_repo/` must not import `pipelines/` or `orchestrator`

---

## Guards (`backend/guards/`)

Every chat turn passes through two synchronous guards **before** LLM generation. Either guard can short-circuit the request with a reject response.

| Guard | File | What it does |
|---|---|---|
| Injection detector | `injection_detector.py` | 2-level: (1) structural regex/unicode patterns (~0 ms), (2) semantic embedding similarity vs. seed corpus (~50–100 ms, timeout-gated). Any level match → immediate reject. |
| Relevance checker | `relevance_checker.py` | LLM-based relevance classification against the tenant's domain (gpt-4o-mini, 3 s timeout, LRU+TTL cache). Low-relevance messages → guard reject. |

Key config knobs (all in `backend/core/config.py`):
- `INJECTION_SEMANTIC_THRESHOLD` (default 0.82), `INJECTION_SEMANTIC_TIMEOUT_SEC` (0.5), `INJECTION_SEMANTIC_ENABLED`
- `RELEVANCE_RETRIEVAL_THRESHOLD` (0.35), `RERANKER_BYPASS_THRESHOLD` (0.5)

---

## Language behavior

The service is **language-agnostic**: the bot must reply in whatever language the user writes in.

- Before the first real user question, greetings and fallback-only turns use `user_context.locale → user_context.browser_locale → English`.
- After the first real question, bot replies **follow the language of that question** — do not force English.
- Soft rejections, clarification prompts, and escalation fallbacks are localized via the shared helper in `backend/chat/language.py`.
- New hardcoded strings (error messages, fallback copy, etc.) must go through this helper — never hardcode English-only text in the chat pipeline.

---

Chat responses now support structured clarification outcomes in addition to plain answers. The canonical public chat/message types are:

- `answer`
- `clarification`
- `partial_with_clarification`

For `/chat` and `/widget/chat`, the response body uses the canonical `text` field only. Legacy aliases (`answer` on `/chat`, `response` on `/widget/chat`) have been removed; consumers must read `text`. Typed behavior lives in `backend/chat/service.py`, `backend/chat/schemas.py`, and the widget/frontend transport types.

Deployment: typically Railway (API + Postgres), frontend on Vercel. All env vars defined in `backend/core/config.py`. Required: `DATABASE_URL`, `JWT_SECRET`. Optional by group:

| Group | Key env vars |
|---|---|
| Observability | `LANGFUSE_*`, `POSTHOG_*`, `SENTRY_DSN`, `GIT_SHA` |
| Trace sampling | `FULL_CAPTURE_MODE`, `TRACE_SAMPLE_RATE`, `TRACE_HIGH_VOLUME_*`, `TRACE_NEW_TENANT_THRESHOLD`, `TRACE_RATE_WINDOW_SECONDS`, `OBSERVABILITY_CAPTURE_FULL_PROMPTS` |
| Guards | `HUMAN_REQUEST_MODEL`, `RELEVANCE_GUARD_MODEL`, `INJECTION_SEMANTIC_THRESHOLD`, `INJECTION_SEMANTIC_TIMEOUT_SEC`, `INJECTION_SEMANTIC_ENABLED`, `RELEVANCE_RETRIEVAL_THRESHOLD`, `RERANKER_BYPASS_THRESHOLD` |
| Chat behavior | `VALIDATION_MODEL`, `CLARIFICATION_TURN_LIMIT`, `LANGUAGE_DETECTION_RELIABILITY_THRESHOLD`, `LOCALIZATION_MODEL`, `WIDGET_MESSAGE_MAX_CHARS`, `WIDGET_CHAT_PER_CLIENT_RATE` |
| Contradiction adjudication | `CONTRADICTION_ADJUDICATION_ENABLED`, `CONTRADICTION_ADJUDICATION_MODEL`, `CONTRADICTION_ADJUDICATION_*` |
| RAG / BM25 | `BM25_EXPANSION_MODE` |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_REQUEST_TIMEOUT_SECONDS`, `OPENAI_USER_RETRY_*` |
| Gap Analyzer jobs | `GAP_TRANSIENT_MAX_ATTEMPTS`, `GAP_BASE_DELAY_SECONDS`, `GAP_MAX_DELAY_SECONDS` |
| Log analysis | `LOG_ANALYSIS_BATCH_SIZE`, `LOG_CLUSTER_*`, `MAX_FAQ_PER_RUN`, `FAQ_CONFIDENCE_AUTO_ACCEPT`, `LOG_ANALYSIS_CRON_HOURS`, `LOG_ANALYSIS_THRESHOLD_MESSAGES`, `LOG_EMBEDDINGS_RETENTION_DAYS`, `EMBEDDING_BATCH_*`, `MAX_JOB_DURATION_SEC`, `ALIAS_MIN_*` |
| Email | `BREVO_API_KEY`, `EMAIL_FROM`, `FRONTEND_URL` |

See `docs/07-observability-rollout.md` for Langfuse/trace rollout details.

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
│   ├── auth/, admin/, bots/, chat/, contact_sessions/, documents/, embeddings/,
│   │   escalation/, faq/, gap_analyzer/, guards/, jobs/, knowledge/,
│   │   observability/, search/, tenant_knowledge/, tenants/, widget/, email/
│   │   ├── routes.py         # HTTP routes (often APIRouter)
│   │   ├── service.py        # business logic, DB access
│   │   └── schemas.py        # Pydantic request/response schemas
│   ├── routes/               # cross-cutting public routes: embed.js loader, widget
│   └── migrations/           # Alembic: versions/, env.py (43+ migrations)
├── frontend/                 # Next.js app
│   └── app/                  # App Router: (marketing), (auth), (app), widget/, layout.tsx
│       └── (app)/            # dashboard routes: admin/, dashboard/, debug/, embed/,
│                             #   escalations/, gap-analyzer/, knowledge/, logs/,
│                             #   review/, settings/, widget-settings/
├── docs/                     # product and technical docs (no need to copy the full stack here)
└── …                         # other assets (widget scripts, etc.)
```

Notable non-obvious modules:
- `backend/knowledge/` — extracts and serves the tenant's knowledge profile (topics) from indexed documents; dashboard `/knowledge` page.
- `backend/tenant_knowledge/` — low-level FAQ and `TenantProfile` service helpers used by the chat pipeline.
- `backend/contact_sessions/` — tracks contact-level session state across escalation flows.

Run the API from the repo root with `PYTHONPATH` pointing at the root so `backend.*` imports resolve.

---

## Naming conventions

### Python (backend)

- Modules and functions: `snake_case`.
- SQLAlchemy model classes: `PascalCase` (`User`, `Tenant`, `EscalationTicket`).
- DB tables: **plural, snake_case** (`users`, `tenants`, `escalation_tickets`, `contact_sessions`).
- Pydantic API schemas: suffixes like `Request` / `Response` or descriptive names (`ChatMessageLogItem`), in the domain’s `schemas.py`.
- Routers: `*_router`; path prefixes wired in `main.py` (e.g. `/auth`, `/chat`, `/tenants`).
- Public string IDs for tenants/bots, etc.: follow existing patterns (`generate_public_id`, prefixes like `ch_` — do not invent new ones without a reason).
- Terminology: the legacy product term "client" has been renamed **"tenant"** at the schema/API level; keep new code and docs on `tenant` / `tenant_id`. "Client" may still appear in marketing copy meaning "customer".

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
