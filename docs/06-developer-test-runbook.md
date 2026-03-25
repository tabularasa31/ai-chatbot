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

## Recommended CI Order

1) P0 Smoke  
2) Auth Reset Flow + Escalation group  
3) RAG Edge Cases  
4) pgvector Integration (separate job with PostgreSQL service)  
5) Coverage Snapshot
