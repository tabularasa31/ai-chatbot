# AGENTS.md — context for Cursor and agents

This file defines the stack, repository layout, and conventions. Keep it updated when architecture changes materially.

**Cursor:** extra rules live in `.cursor/rules/*.mdc` — `project-basics` (always on), file-scoped: `backend-data-layer`, `backend-http`, `backend-schemas`, `frontend-app`, `frontend-typescript`. Avoid duplicating long sections between AGENTS and `.mdc`; if they diverge, update both or pick a single source of truth.

---

## Stack

| Layer | Technologies |
|-------|----------------|
| Backend | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy, Alembic |
| Database | PostgreSQL 14+ with **pgvector** (production); tests may use SQLite with simplified types and Python cosine candidate acquisition, then the shared BM25/RRF/reranking retrieval flow (see `backend/models.py`, `backend/search/service.py`) |
| Auth | JWT, bcrypt, email verification (Brevo HTTP API) |
| LLM | OpenAI API (per-client key / client settings; see `backend/core/openai_client.py`) |
| Frontend | Next.js 14 (App Router), React 18, TypeScript, TailwindCSS, Radix Slot, framer-motion |
| Widget | Dedicated Next.js routes (`/widget`), API calls; some public endpoints in `backend/routes/widget.py` and `backend/widget/` |

Deployment: typically Railway (API + Postgres), frontend on Vercel. Environment variables: `backend/core/config.py` (required: `DATABASE_URL`, `JWT_SECRET`, etc.).

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

---

## Database usage

In short: models live only in `backend/models.py`; HTTP handlers use `Depends(get_db)`; services receive `Session` from the router; background jobs and scripts use `SessionLocal`; schema changes only via Alembic from the repo root.

**Full checklist** — `.cursor/rules/backend-data-layer.mdc` (applies when working under `backend/**/*.py`).

---

## Other editing guidelines

See `.cursor/rules/project-basics.mdc` (always applied). Feature details and regression scenarios — `docs/` (`docs/04-features.md`, `docs/qa/`).
Developer test command groups for local/CI runs are documented in `docs/06-developer-test-runbook.md`.
