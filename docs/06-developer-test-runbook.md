# Developer Test Runbook (Non-QA)

Use these command groups for fast local checks and CI job partitioning.

For **production regression evals** (latency / answer-quality comparisons after metric-claiming PRs), see [post-merge-eval-runbook.md](./post-merge-eval-runbook.md). This file covers pytest groups; the post-merge runbook covers behavioural evals against the deployed bot.

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

## Clarification policy (decision engine)

Use this after touching `backend/chat/decision.py`, `backend/chat/slots.py`,
the `chats.clarification_count` budget, the block-rules gate in
`backend/chat/handlers/rag.py`, or any of the trace/PostHog fields produced
by `Decision.trace_dict()`.

```bash
pytest -q tests/test_decision.py
```

This is a pure-function suite that exercises the `decide()` block rules in
isolation from the pipeline. It covers:

- each of the seven block rules (guard reject, explicit human request,
  closed session, active escalation, FAQ direct hit, KB confidence routing,
  partial-answer inline clarify)
- budget enforcement: a second would-be blocking clarify in the same
  session falls back to `answer_with_caveat` or escalates with
  `clarify_loop_limit`
- ordering invariants between block rules (guard > human request > closed
  > active escalation > FAQ > KB confidence)
- inline clarify and FAQ hits are never suppressed by the budget rule
- counter semantics: `Decision.trace_dict()` increments
  `clarification_count_after` only for blocking clarify

The end-to-end pipeline integration (TurnContext build, counter persistence,
trace emission) is exercised by the regular chat suites — no separate
group is needed for it.

v1 note: structured clarification payloads (`message_type`,
`partial_with_clarification`, quick-reply options) are **not implemented**.
Tests that previously asserted on those shapes were removed in PR #287.

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

If Gap Analyzer ANN plans still fall back to sequential scans after a fresh bootstrapping import,
rebuild the IVFFlat indexes once the tables contain enough data for useful centroids:

```bash
psql "$DATABASE_URL" -c "REINDEX INDEX CONCURRENTLY ix_gap_clusters_centroid_ivfflat"
psql "$DATABASE_URL" -c "REINDEX INDEX CONCURRENTLY ix_gap_doc_topics_topic_embedding_ivfflat"
psql "$DATABASE_URL" -c "REINDEX INDEX CONCURRENTLY ix_gap_questions_embedding_ivfflat"
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

Manual UI: **`/eval/chat?bot_id=<widget bot ID>`** — same value as the dashboard embed's `data-bot-id` (bot's `public_id`), not the private API key.

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
