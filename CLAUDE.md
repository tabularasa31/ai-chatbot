# CLAUDE.md — Claude Code instructions for ai-chatbot (Chat9)

Architecture, stack, naming conventions, and repo layout → **AGENTS.md**.
Global Claude rules (PR format, branching, Alembic safety) → `~/.claude/CLAUDE.md`.
This file covers **how to run, test, and work with this codebase**.

---

## Dev setup

```bash
# Database (PostgreSQL + pgvector)
make db-up          # shorthand for docker compose up -d db
# or directly: docker compose up -d db

# Backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill DATABASE_URL, JWT_SECRET, EVAL_JWT_SECRET, etc.
alembic upgrade head
uvicorn backend.main:app --reload   # http://localhost:8000

# Frontend
cd frontend
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL
npm run dev                         # http://localhost:3000
```

---

## Testing

```bash
make smoke          # fast P0 regression (auth, chat, escalation)
make test           # full suite: SQLite + pgvector (requires db container)
make test-sqlite    # SQLite only (no docker needed)
make test-pgvector  # PostgreSQL integration tests only (requires db)
make pgvector-only  # same as above but skips db-up (db already running)
make coverage       # SQLite coverage report
make coverage-all   # full coverage: SQLite + pgvector (requires db container)

# Focused suites
make auth-reset     # forgot/reset password flows
make escalation     # escalation edge cases
make rag-edge       # RAG pipeline edge cases (openai_unavailable, low_vector, etc.)
```

Run a single file: `pytest tests/test_chat.py -v`

- Tests use SQLite by default; pgvector tests run against the Docker container.
- Do **not** use `--asyncio-mode=auto` globally; configuration lives in `pytest.ini`.

---

## Linting

```bash
ruff check backend          # Python lint
cd frontend && npm run lint # TypeScript/ESLint
```

CI runs both on every push/PR to `main` and `deploy`.

---

## Key files to know

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI entry point, all router wiring |
| `backend/models.py` | **All** SQLAlchemy models in one file |
| `backend/core/config.py` | Settings and all env vars (guards, trace, RAG knobs, etc.) |
| `backend/core/openai_client.py` | Per-tenant OpenAI client factory |
| `backend/search/service.py` | Hybrid RAG retrieval (pgvector + BM25 + RRF) |
| `backend/guards/` | Injection detection (2-level) + relevance guard — gate on every chat turn |
| `backend/observability/` | Langfuse trace helpers, Sentry, PostHog metrics formatters |
| `backend/gap_analyzer/` | Gap Analyzer orchestration (see AGENTS.md for full layout) |
| `backend/eval/` | QA eval pipeline; uses separate `EVAL_JWT_SECRET` |
| `backend/knowledge/` | Tenant knowledge profile extraction and topics API |
| `backend/tenant_knowledge/` | FAQ/tenant-profile service helpers |
| `tests/conftest.py` | Pytest fixtures (DB session, test client) |
| `docs/04-features.md` | Feature specs and expected behaviour |
| `docs/06-developer-test-runbook.md` | Test command groups reference |
| `docs/docs-ru/` | Internal Russian-language project documentation |
| `frontend/content/docs/` | **Client-facing documentation** (MDX, rendered in the product UI) — update here when asked to update client docs |

---

## Conventions (quick reference)

- New backend modules follow: `routes.py` + `service.py` + `schemas.py` under a domain folder.
- All DB models go in `backend/models.py` — never create a new models file.
- DB schema changes: Alembic migration only (`alembic revision -m "description"`).
- Services receive `Session` from the router via `Depends(get_db)`; never import `SessionLocal` in HTTP handlers.
- Every chat turn passes through `backend/guards/` before LLM generation: injection detector (structural → semantic, 2 levels) then relevance guard. Both are synchronous and short-circuit on failure.
- The bot is language-agnostic: replies must be in the user's language. New hardcoded strings in the chat pipeline go through `backend/chat/language.py` — never hardcode English-only copy.
- Frontend components: `PascalCase`; utilities: `camelCase`; Tailwind for styles.

---

## Deployment

- **Backend**: Railway — `alembic upgrade head` runs automatically on each deploy (Procfile `release` step).
- **Frontend**: Vercel — auto-deploys on `main` branch.
- Never push directly to `main` without a PR (see global rules).

---

## Task tracking — ClickUp

This project uses **ClickUp** (cloud, MCP-accessible). Do NOT use Plane, Linear, or Jira.

**At the start of every session:** ask "Нужно ли создать задачу в ClickUp для этой сессии?"

**Workspace & structure:**
```
Workspace ID:  90182652207
Space:         Team Space (901810779094)
Folder:        Chat9 — AI Chatbot (901813669414)
```

**Lists (by domain):**
- Auth and Users:    `901817658296`
- Chat Core:         `901817658300`
- RAG and Search:    `901817658303`
- Tenant Management: `901817658304`
- Observability:     `901817658306`
- Gap Analyzer:      `901817658307`
- Eval Pipeline:     `901817658308`
- Frontend:          `901817658309`
- DevOps:            `901817658310`

**Documents (knowledge/specs):**
- Backlog:            `2kzmw49f-458`
- Strategy & Research:`2kzmw49f-478`
- Progress & Reviews: `2kzmw49f-498`
- QA & Testing:       `2kzmw49f-518`
- Specs:              `2kzmw49f-538`
- Archive:            `2kzmw49f-558`

**MCP tools** (use the available ClickUp MCP tools — `clickup_*`):
- Create task: `clickup_create_task` — always include full business description (WHY, WHAT, acceptance criteria)
- Update task status: `clickup_update_task` with `status` field
- Add comment: `clickup_create_task_comment`
- Create doc page: `clickup_create_document_page`
- Search: `clickup_search`

**Task lifecycle — mandatory for every agent session:**

1. **Session start / task picked up** → move status to `in progress` immediately
2. **During work** → add a comment with a link to the current Claude session (so progress is traceable)
3. **PR opened** → move status to `in review` + add comment with PR URL
4. **After deploy / work done** → move status to `done`

Never leave a task in `to do` while actively working on it. Never finish a session without updating the status and dropping a comment with the session/PR link.
