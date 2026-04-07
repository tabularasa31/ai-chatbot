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

Chat responses now support structured clarification outcomes in addition to plain answers. The canonical public chat/message types are:

- `answer`
- `clarification`
- `partial_with_clarification`

For `/chat` and `/widget/chat`, legacy text aliases (`answer` / `response`) still exist for compatibility, but the typed behavior lives in `backend/chat/service.py`, `backend/chat/schemas.py`, and the widget/frontend transport types.

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
