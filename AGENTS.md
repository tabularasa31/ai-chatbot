# AGENTS.md — context for Cursor and agents

This file defines the stack, repository layout, and conventions. Keep it updated when architecture changes materially.

**Cursor:** extra rules live in `.cursor/rules/*.mdc` — `project-basics` (always on), file-scoped: `backend-data-layer`, `backend-http`, `backend-schemas`, `frontend-app`, `frontend-typescript`. Avoid duplicating long sections between AGENTS and `.mdc`; if they diverge, update both or pick a single source of truth.

---

## Stack

| Layer | Technologies |
|-------|----------------|
| Backend | Python 3.11, FastAPI 0.111, Pydantic v2 (2.5), SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL 15 with **pgvector** (production, see `docker-compose.yml`); tests may use SQLite with simplified types and Python cosine candidate acquisition, then the shared BM25/RRF/reranking retrieval flow (see `backend/models.py`, `backend/search/service.py`) |
| Cache / coordination | Redis 7 (foundational infra: rate-limit storage, caches, distributed locks). Optional locally — see "Redis" section below. |
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

## Redis (`backend/core/redis.py`)

Redis is **foundational infra** — used for things that need cross-worker shared state. It is wired through a single async client (`backend/core/redis.py`) opened in the FastAPI `lifespan`.

| Use case | Where | Notes |
|---|---|---|
| **Rate-limit storage** | `backend/core/limiter.py` (slowapi `storage_uri`) | Required for correct enforcement under N workers (in-memory storage = limit × N). Tests force `memory://`. |
| **Caches** (e.g. guard verdicts, embeddings) | `backend/core/redis.py` → `cache_get` / `cache_set_with_ttl` | Optional infra; helpers swallow connection errors and the caller treats it as a miss. |
| **Distributed locks** (e.g. scheduled jobs, crawl throttle) | `backend/core/redis.py` → `acquire_lock` / `release_lock` | Atomic `SET NX EX` + Lua check-and-delete on release. |
| **ARQ queue broker** | `backend/core/queue.py` (worker process: `backend/worker.py`) | Durable retry-aware background work — see "Job queue" below. |
| **Idempotency keys** (future) | — | Tracked separately; will use the cache helpers. |

**Configuration:**
- `REDIS_URL` (env). Unset locally → in-memory rate-limit storage and no-op cache/lock helpers. Required in production (Railway provisions it via the Redis add-on).
- Local dev: `docker-compose up redis` (port `6379`).
- `/health`: `200 {status: ok, redis: ok|disabled}` when healthy; **`503 {status: degraded, redis: unavailable}`** when `REDIS_URL` is set but Redis is unreachable (so Railway pulls the instance out of rotation — rate-limit storage depends on Redis).

**Key namespaces** (`<domain>:<purpose>:<id>`, lowercase, colon-separated):
- `ratelimit:*` — managed by slowapi (do not touch directly)
- `cache:guard:<sha256>` — guard-verdict caches
- `lock:gap_analyzer:<job>` — distributed locks for periodic jobs

**Graceful degradation rules:**
- Cache misses on Redis errors must not break the request — log at debug, return as if cache miss.
- Locks: when `acquire_lock` returns `None` (Redis down or already held), the caller decides whether to skip or run unguarded — never block forever.
- Rate limit: failures surface as 500s on purpose (rate limiting is a security control; silent fallback to memory storage in production would create a window where a single worker has no shared limit).

---

## Job queue (`backend/core/queue.py`)

Single ARQ-backed queue for everything that should outlive a request, survive a deploy, and retry on failure. Replaces ad-hoc `BackgroundTasks` and `threading.Thread` (migrations of existing background work happen ticket-by-ticket).

**Components:**
- `backend/core/queue.py` — `register_job` decorator, `enqueue` helper, `get_worker_settings()` factory.
- `backend/worker.py` — entrypoint (`python -m backend.worker`); imports every module that registers jobs, then calls `arq.run_worker`.
- `backend/jobs/*` — modules containing `@register_job` coroutines.
- `background_jobs` table (Postgres) — durable mirror of ARQ job state for admin UI and debugging.

**Adding a new job:**

1. Create or pick a module under `backend/jobs/` and decorate the coroutine:
   ```python
   from backend.core.queue import register_job

   @register_job(name="reindex_tenant", max_attempts=5)
   async def reindex_tenant(ctx, tenant_id: str) -> None:
       ...
   ```
   The first arg is always the ARQ `ctx` dict (`ctx['job_id']`, `ctx['job_try']`, etc.). Keep the body **idempotent** — ARQ may retry on failure or worker restart.

2. Register the module in `_JOB_MODULES` inside `backend/worker.py` so the decorator runs when the worker boots.

3. Enqueue from a route or another job:
   ```python
   from backend.core.queue import enqueue

   await enqueue(
       "reindex_tenant",
       str(tenant.id),
       kind="reindex_tenant",
       tenant_id=tenant.id,
       payload={"tenant_id": str(tenant.id)},
   )
   ```
   Pass `job_id="reindex:{tenant_id}"` to deduplicate while a job is pending.

**Retries and failure handling:**
- ARQ default retry policy: up to `max_attempts` tries with exponential backoff. Override per job via `max_attempts=` on `register_job`.
- The status-row wrapper updates `background_jobs.status`: `queued → in_progress → completed | failed | dead_letter`. The terminal `dead_letter` value is set on the **final** failure (attempt == `max_attempts`).
- Failures re-raise so ARQ schedules the retry; the wrapper records `last_error` / `last_error_at` / `attempt_count` on every attempt.

**Graceful degradation:**
- When `REDIS_URL` is unset, `enqueue` logs `queue_enqueue_skipped` and returns `None`. The worker process refuses to start without `REDIS_URL` (it is required infra in production; Railway provisions it via the Redis add-on).

**Local dev:**
```bash
docker compose up -d redis db
export REDIS_URL=redis://localhost:6379/0
alembic upgrade head
python -m backend.worker          # in one terminal
uvicorn backend.main:app --reload # in another
```

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
│   │   escalation/, evals/, faq/, gap_analyzer/, guards/, jobs/, knowledge/,
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
- `backend/evals/` — automated answer-quality eval CLI (`python -m backend.evals run`). Loads YAML golden datasets from `tests/eval/datasets/`, hits the running backend's widget chat endpoint, and scores responses with deterministic checks + Anthropic Claude as LLM-as-judge. Datasets and unit tests in `tests/`; demo bot seeded via `scripts/seed_eval_bot.py`.

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
