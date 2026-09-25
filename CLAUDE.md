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
cp .env.example .env       # fill DATABASE_URL, JWT_SECRET, etc.
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

### Test strategy

- **Through-the-app first.** Default to a test that drives a real request through the FastAPI app (the models are `test_widget.py` and `test_search_api.py`; `test_chat_escalation.py` and `test_escalation.py` are the escalation domain files, still carrying unit-style tests that are being migrated) over one that mocks internals to isolate a unit. A new feature or behaviour change lands with a scenario in the domain's existing test file that proves it works through the app and can be rerun to catch regressions — that scenario is the artifact.
- **Where the mock boundary is.** Stubbing the network edge — OpenAI chat and embeddings, Langfuse — via the autouse fixtures in `tests/conftest.py` is part of the through-the-app style, not "mocking internals". Other externals (PostHog, email) are stubbed per test file where relevant, same rule applies. Patching anything under `backend/` (a handler, a service, a classifier's return value) makes it a unit test and needs the justification below.
- **SQLite vs pgvector.** Pipeline behaviour (guards, escalation, language, handoff, what the bot answers) is tested on SQLite via TestClient. Anything where the retrieval result itself is under test (ranking, hybrid fusion, chunk hits) goes in `tests/pgvector_tests/` and runs under `make test-pgvector`. Don't try to assert search quality on SQLite.
- **Landing in a `make` suite.** `smoke`, `escalation`, `rag-edge` and `auth-reset` select tests by pytest marker (`@pytest.mark.smoke`, `.escalation`, `.rag_edge`, `.auth_reset`, registered in `pytest.ini`), not by file list or test name. A scenario belongs to a suite only if it carries the marker — add it when you write the test; a test may carry several. Every test must be reachable through one of the `make` targets in this file (`make test-sqlite` counts).
- **Isolated/unit tests are for logic the app-level path can't cover efficiently**: branch-heavy pure logic (RRF fusion in `search/service.py`, reranking strategy selection, the injection-guard levels, per-type chunkers). A unit test earns its place only when either (a) it covers a failure mode the app-level path can't reach cheaply (an edge-case branch, malformed input, a race), or (b) reaching that branch through the app would need patching inside `backend/`. Before writing one, list the failure modes it is meant to catch as the test names — the file should read as a spec of what can go wrong, not incidental coverage of whatever changed.
- **Group by domain, not by handler — for through-the-app tests.** New through-the-app scenarios go into the domain's existing file (`test_widget.py`, `test_chat_escalation.py`, `test_search_api.py`, `test_auth.py`, …; ranking and reliability units live in `test_search_ranking.py` / `test_search_reliability.py`). A dedicated `test_chat_handlers_*.py` file is a legitimate home only for a handler's pure logic tested without patching internal collaborators — `test_chat_handlers_rag_gating.py` and `test_chat_handlers_greeting.py` are the model: real objects built directly, at most a boundary stub for an LLM call (the same rule as the mock boundary above), docstring naming the failure modes. A handler file that patches internal control flow to force its branches (`test_chat_handlers_escalation.py`, patching `await_only`, `classify_pre_confirm_reply`, `_escalation_turn_response`, …) is the pattern to stop extending: put that behaviour in the domain file through the app instead.
- **No incident regression tests.** Never add a test whose purpose is to reproduce a specific past bug or incident ("regression for #N", "reproduces the prod bug", env/config quirks, one-off crashes). Fix the bug and verify with the existing journey or suite. A rule an incident revealed may be asserted as a product rule, without the incident framing, only if no test asserts it yet.

- **Deterministic and offline.** No live network or OpenAI calls, no time-dependent assertions without freezing the clock, no reliance on test order. Use the fixtures and stubs already in `conftest.py`; add a new stub there rather than patching ad hoc in a test.

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
| `backend/models/` | SQLAlchemy models split by domain; re-exported via `backend/models/__init__.py` |
| `backend/core/config.py` | Settings and all env vars (guards, trace, RAG knobs, etc.) |
| `backend/core/openai_client.py` | Per-tenant OpenAI client factory |
| `backend/search/service.py` | Hybrid RAG retrieval (pgvector + BM25 + RRF) |
| `backend/search/reranking.py` | `Reranker` protocol + heuristic / LLM / cross-encoder strategies, chosen per tenant (`tenants.reranker_strategy`), hard timeout with heuristic fallback |
| `backend/chunkers/` | Per-content-type chunkers (markdown/html/pdf/plaintext) + registry; see its README to add a new type |
| `backend/guards/` | Injection detection (2-level) + relevance guard — gate on every chat turn |
| `backend/observability/` | Langfuse trace helpers, Sentry, PostHog metrics formatters |
| `backend/gap_analyzer/` | Gap Analyzer orchestration (see AGENTS.md for full layout) |
| `backend/evals/` | Automated answer-quality eval CLI: golden dataset → chat → metrics + Anthropic-as-judge. See `docs/06-developer-test-runbook.md` § Eval pipeline. |
| `backend/operator/` | Live operator handoff — take / answer / release a chat, with the bot muted while a human holds it. One ingestion seam (`ingest_from_operator`) for every channel; `sessions.py` records each operator-served stretch as an `operator_sessions` row and emits `operator_session_ended` when it closes |
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
- All DB models go in `backend/models/{domain}.py` (e.g. `auth.py`, `chat.py`), re-exported via `backend/models/__init__.py`. Never import directly from the sub-modules unless needed — use `from backend.models import X`.
- DB schema changes: Alembic migration only (`alembic revision -m "description"`).
- Services receive `Session` from the router via `Depends(get_db)`; never import `SessionLocal` in HTTP handlers.
- **Async-first for new code.** New domains and new services use `AsyncSession` via `Depends(get_async_db)` from `backend/core/db.py`, and `AsyncOpenAI` via `get_async_openai_client`. Substantively reworking an existing sync service = migrate that domain to async in the same PR. Pure-CRUD domains without hot I/O (auth/tenants/admin) may stay sync — that's a legitimate end state, not tech debt. The `search` domain is fully async (single async retrieval pipeline and async-only low-level helpers in `backend/search/service.py` — no sync duplicates). `guards` and the chat pipeline are async end-to-end (`async_detect_injection`, `async_check_relevance_with_profile`); only the pure-CPU structural level-1 check stays a sync helper. The `escalation` LLM surface is async-only as well: `detect_human_request`, `classify_question_intent`, `classify_pre_confirm_reply`, `render_pre_confirm_text`, `complete_escalation_openai_turn`, and `perform_manual_escalation` are coroutines with no sync twins; the escalation FSM's DB work runs on the `AsyncSession` sync facade via `run_sync` and bridges to those coroutines with `await_only` — never wrap escalation functions in `asyncio.to_thread`. The `chat/language.py` LLM surface is async-only: `generate_greeting_in_language_result`, `translate_text_result`, `localize_text_result`, `async_localize_text_to_language_result`, and `render_direct_faq_answer_result` are coroutines with no sync twins, and `guards/reject_response.build_reject_response_result` is natively async. The `widget` module is fully async: every endpoint in `backend/widget/routes.py` runs on `AsyncSession` (sync DB helpers bridged via `run_sync`), and `widget_chat` releases its session's pooled connection before returning the SSE `StreamingResponse` so it is not pinned for the stream's duration. With that, the critical chat path is fully async; the remaining sync `call_openai_with_retry` consumers (`knowledge/entity_extractor.py`, `search/contradiction_adjudication.py`) are background-bound, not critical-path.
- Every chat turn passes through `backend/guards/` before LLM generation: injection detector (structural → semantic, 2 levels) then relevance guard. Both are awaited in the async chat pipeline and short-circuit on failure.
- The bot is language-agnostic: replies must be in the user's language. New hardcoded strings in the chat pipeline go through `backend/chat/language.py` — never hardcode English-only copy.
- Frontend components: `PascalCase`; utilities: `camelCase`; Tailwind for styles.
- **Prompt caching contract.** When touching the chat generation prompt in `backend/chat/prompts.py`, keep the system message byte-identical across turns (stable cache prefix) and ≥ ~1024 tokens — request-specific content goes in the user message after the `Context:` split. Full rules in **AGENTS.md → "Prompt caching contract"**.

---

## Deployment

- **Backend**: Railway — `alembic upgrade head` runs automatically on each deploy, in the service's start/pre-deploy command (the Procfile `release` line is not honoured by Railway). See **AGENTS.md → "Deployment safety"** for the migration-head rule, the `main` ruleset and the Sentry uptime monitor.
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

**Status flow:** `Backlog` → `To Do` → `In Progress` → `In Review` → `Done`

- **Backlog** — all new tasks land here by default
- **To Do** — owner manually moves here when taking into focus
- Agents never touch `Backlog` → `To Do` transition (that's the owner's call)

**Task lifecycle — mandatory for every agent session:**

1. **Session start / task picked up** → move status to `In Progress` immediately
2. **During work** → add a comment with a link to the current Claude session (so progress is traceable)
3. **PR opened** → move status to `In Review` + add comment with PR URL
4. **After deploy / work done** → move status to `Done`

Never leave a task in `To Do` while actively working on it. Never finish a session without updating the status and dropping a comment with the session/PR link.

**Description is for specification only** — set at creation and updated only if requirements or acceptance criteria change. All progress updates (session links, PR URLs, notes) must go as comments via clickup_create_task_comment, never as edits to the description.
