# CLAUDE.md — Claude Code instructions for ai-chatbot (Chat9)

Architecture, stack, naming conventions, and repo layout → **AGENTS.md**.
Global Claude rules (PR format, branching, Alembic safety) → `~/.claude/CLAUDE.md`.
This file covers **how to run, test, and work with this codebase**.

---

## Dev setup

```bash
# Database (PostgreSQL + pgvector)
docker compose up -d db

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
make test-pgvector  # PostgreSQL integration tests
make coverage       # SQLite coverage report
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
| `backend/core/config.py` | Settings and required env vars |
| `backend/core/openai_client.py` | Per-client OpenAI client factory |
| `backend/search/service.py` | Hybrid RAG retrieval (pgvector + BM25 + RRF) |
| `tests/conftest.py` | Pytest fixtures (DB session, test client) |
| `docs/04-features.md` | Feature specs and expected behaviour |
| `docs/06-developer-test-runbook.md` | Test command groups reference |

---

## Conventions (quick reference)

- New backend modules follow: `routes.py` + `service.py` + `schemas.py` under a domain folder.
- All DB models go in `backend/models.py` — never create a new models file.
- DB schema changes: Alembic migration only (`alembic revision -m "description"`).
- Services receive `Session` from the router via `Depends(get_db)`; never import `SessionLocal` in HTTP handlers.
- Frontend components: `PascalCase`; utilities: `camelCase`; Tailwind for styles.

---

## Deployment

- **Backend**: Railway — `alembic upgrade head` runs automatically on each deploy (Procfile `release` step).
- **Frontend**: Vercel — auto-deploys on `main` branch.
- Never push directly to `main` without a PR (see global rules).
