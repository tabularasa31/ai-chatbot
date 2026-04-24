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

## Task tracking — Plane (NOT Linear, NOT Jira)

This project uses **self-hosted Plane** at `http://localhost`. Do NOT use Linear or any other tracker.

**At the start of every session:** ask "Нужно ли создать задачу в Plane для этой сессии?"

**API (works with API key for issues):**
```
Base URL:  http://localhost/api/v1/workspaces/chat9/projects/fafa6d90-860d-431b-a6a3-8d345c19c48d
API key header: x-api-key: plane_api_7054523303304d158c861c2de2bc720e
```

**State IDs:**
- Backlog: `ce2523ae-ecb6-43da-9617-7ea64a6735a5`
- Todo: `84727391-2611-4b56-911b-e2e0f017793c`
- In Progress: `d65eafc9-95ff-4a30-8bb5-ef67f889c589`
- In Review: `fd53cd08-0693-411e-86d3-227cd895c3ed`
- Done: `beed4266-8c0c-4c47-80c2-410ccad4d8e4`

**Issue lifecycle:**
- Create issue with full business description (WHY, WHAT, acceptance criteria) — never just a title
- Include link to current session in description
- After PR opened → move to In Review + add PR URL to description (PATCH the issue)
- After deploy → move to Done

**Create issue:**
```bash
curl -s -H "x-api-key: plane_api_7054523303304d158c861c2de2bc720e" \
  -H "Content-Type: application/json" \
  -X POST "http://localhost/api/v1/workspaces/chat9/projects/fafa6d90-860d-431b-a6a3-8d345c19c48d/issues/" \
  -d '{"name":"Title","description_html":"<p>Full description</p>","state":"84727391-2611-4b56-911b-e2e0f017793c","priority":"high"}'
```

**Update issue state:**
```bash
curl -s -H "x-api-key: plane_api_7054523303304d158c861c2de2bc720e" \
  -H "Content-Type: application/json" \
  -X PATCH "http://localhost/api/v1/workspaces/chat9/projects/fafa6d90-860d-431b-a6a3-8d345c19c48d/issues/{issue_id}/" \
  -d '{"state":"fd53cd08-0693-411e-86d3-227cd895c3ed"}'
```
