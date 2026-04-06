# Developer Test Runbook (Non-QA)

Use these command groups for fast local checks and CI job partitioning.

## P0 Smoke (critical regressions)

```bash
make smoke
```

Direct pytest equivalent:

```bash
pytest -q \
  tests/test_chat.py \
  tests/test_escalation.py \
  tests/test_auth.py \
  tests/test_auth_email_verification.py \
  tests/test_verification_enforcement.py \
  -k "escalat or verify or forgot_password or reset_password"
```

## Auth Reset Flow

```bash
make auth-reset
```

Direct pytest equivalent:

```bash
pytest -q tests/test_auth.py -k "forgot_password or reset_password"
```

## Escalation State Machine + Manual Escalation

```bash
make escalation
```

Direct pytest equivalent:

```bash
pytest -q \
  tests/test_chat.py \
  tests/test_escalation.py \
  -k "awaiting_email or followup or already_closed or manual_escalate or perform_manual_escalation"
```

## Clarification Flow (controlled clarification MVP)

Use this after touching chat outcome typing, clarification heuristics, widget
quick replies, or continuation-vs-new-intent logic.

```bash
pytest -q \
  tests/test_chat.py \
  tests/test_widget.py \
  -k "clarification"
```

This targeted group covers:

- direct answer vs clarification branching
- clarification continuation vs new intent
- clarification state cleanup/supersede behavior
- widget quick replies and structured clarification payloads
- schema invariant: non-answer message types must include clarification payload

## RAG Edge Cases (SQLite path)

```bash
make rag-edge
```

Direct pytest equivalent:

```bash
pytest -q \
  tests/test_search.py \
  tests/test_chat.py \
  -k "openai_unavailable or malformed or wrong_dimension or low_vector"
```

## pgvector Integration (PostgreSQL only)

```bash
make test-pgvector
```

Direct pytest equivalent (requires Docker Postgres + correct PG_* creds):

```bash
pytest -q -m pgvector tests/pgvector_tests/
```

Optional targeted pgvector hybrid retrieval checks:

```bash
pytest -q -m pgvector tests/pgvector_tests/test_search_pgvector.py -k "hybrid or isolation"
```

## Internal eval QA (`/eval/*`)

Requires `EVAL_JWT_SECRET` in the environment (see `tests/conftest.py` default for local pytest).

```bash
pytest -q tests/test_eval.py
```

Create an internal tester (uses app DB from `DATABASE_URL` / `.env`):

```bash
PYTHONPATH=. python scripts/create_tester.py --username anna --password 'your-secret'
```

Manual UI: **`/eval/chat?bot_id=<widget bot ID>`** — same value as `embed.js?clientId=ch_…` (public bot ID; `clientId` is kept for widget compatibility), not the private API key.

## Coverage Snapshot (backend, SQLite-only)

```bash
make coverage
```

Direct pytest equivalent:

```bash
pytest --cov=backend --cov-report=term-missing --cov-report=xml
```

## Coverage Snapshot (full suite: SQLite + pgvector)

```bash
make coverage-all
```

## Alembic Migration Guard

Use this before merging any schema change:

```bash
pytest -q tests/test_migrations.py
```

This guard checks repository migration metadata and currently enforces:

- every Alembic `revision` id is 32 characters or fewer
- migration files expose a `revision` constant
- there are no duplicate `revision` ids

## Recommended CI Order

1) P0 Smoke  
2) Auth Reset Flow + Escalation group  
3) RAG Edge Cases  
4) Internal eval (`tests/test_eval.py`, CI env includes `EVAL_JWT_SECRET`)  
5) pgvector Integration (separate job with PostgreSQL service)  
6) Coverage Snapshot
