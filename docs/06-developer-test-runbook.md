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

## Contradiction adjudication smoke (`scripts/contradiction_demo.py`)

End-to-end verification that the LLM contradiction-adjudication wiring is
alive: detector → effective_pairs → adjudicator → cap decision. Use after
changes to anything in `backend/search/contradiction_adjudication.py` or
the contradiction-cap policy in `build_reliability_assessment`.

The script does not touch the DB or the chat HTTP layer — it calls the
search-layer functions directly with two synthetic conflicting facts and
prints the resulting reliability object under both flag states.

```bash
# Real OpenAI call to the adjudicator (needs a working OPENAI_API_KEY in .env;
# placeholder keys exercise the failed_open / fail-open branch).
python scripts/contradiction_demo.py

# Skip the OpenAI round-trip and synthesize an all-`rejected` run; the only
# branch where suppression engages. Use when validating the cap-suppression
# branch without spending tokens or depending on a live key.
python scripts/contradiction_demo.py --mock-all-rejected

# Restrict to one flag state if you only want to see one half of the matrix.
python scripts/contradiction_demo.py --on-only --mock-all-rejected
python scripts/contradiction_demo.py --off-only
```

What the four observable cells of the truth table mean:

| Flag | Run outcome | Expected `cap` / `cap_reason` |
|---|---|---|
| `false` | any | `low` / `contradiction` (legacy: arbiter is observer-only) |
| `true` | every fact `verdict == "rejected"` | `None` / `None` (cap suppressed — main feature path) |
| `true` | mixed / `confirmed` / `inconclusive` / `failed_open` | `low` / `contradiction` (fail-open) |

The script prints a one-line `verdict:` summary for each cell so a green
run is obvious without parsing the dataclass dump.

## Eval pipeline (`backend/evals/`)

Automated answer-quality evaluation against a real chat backend, with
deterministic metrics + LLM-as-judge (Anthropic Claude Haiku). Used to
spot regressions in answer quality after prompt / RAG / model changes.

The runner targets a **running** chat backend (local uvicorn or a
deployed instance) — it does not stand up a backend itself.

### One-time setup

```bash
# 1. Spin up the backend locally (separate terminal):
uvicorn backend.main:app --reload

# 2. Seed the demo eval bot from the chat9 fixture docs:
DATABASE_URL=postgresql://… OPENAI_API_KEY=sk-… \
    python scripts/seed_eval_bot.py
# → prints  EVAL_BOT_PUBLIC_ID=ch_xxxxxxxxxxxxxxxx
```

### Run a dataset

```bash
ANTHROPIC_API_KEY=sk-ant-… \
    python -m backend.evals run \
        --dataset chat9_basic \
        --bot-id ch_xxxxxxxxxxxxxxxx \
        --tag local-2026-04-30
```

Datasets live in `tests/eval/datasets/*.yaml`. List them with
`python -m backend.evals list`. Reports land in `eval-results/<tag>/`
as `report.json` + `report.md`.

Useful flags: `--api-base http://staging.…` (target a remote backend),
`--no-judge` (skip Claude — deterministic metrics only, free), and
`--judge-model claude-sonnet-4-6` (more accurate but ~10x more
expensive than the default Haiku).

### Compare two runs (before vs after)

Use after a prompt / RAG / model change to see what flipped:

```bash
python -m backend.evals run --tag baseline --dataset chat9_basic --bot-id ch_…
# … make your code changes …
python -m backend.evals run --tag candidate --dataset chat9_basic --bot-id ch_…

python -m backend.evals compare \
    eval-results/baseline/report.json \
    eval-results/candidate/report.json \
    --fail-on-regression
```

Outputs a Markdown summary: per-case **regressions** (was passing,
now failing) and **fixes** (was failing, now passing), plus the
average judge-score delta. `--fail-on-regression` returns exit code
1 so the same command can be wired into a Makefile / pre-deploy gate.

### Persist runs to Langfuse

When `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
are set, add `--langfuse` to mirror the dataset items + per-case
traces (with scores) into Langfuse so the team can browse history
in the UI:

```bash
LANGFUSE_HOST=https://cloud.langfuse.com \
LANGFUSE_PUBLIC_KEY=pk-… LANGFUSE_SECRET_KEY=sk-… \
ANTHROPIC_API_KEY=sk-ant-… \
python -m backend.evals run --dataset chat9_basic --bot-id ch_… \
    --tag manual-2026-04-30 --langfuse
```

Without those env vars `--langfuse` is a logged no-op.

### Run from GitHub Actions

Two workflows ship in `.github/workflows/`:

- **`eval-manual.yml`** — `workflow_dispatch`. Pick a dataset, a
  run tag, and (optionally) a PR number to comment on. Posts a
  Markdown summary as a PR comment when the input is set; uploads
  `report.json` + `report.md` (and `diff.md` if `compare_with` was
  passed) as workflow artifacts.
- **`eval-nightly.yml`** — `cron: "0 2 * * *"` (02:00 UTC). Runs
  every dataset against the staging bot and opens a labelled
  `[eval] regression` GitHub Issue when the pass rate drops below
  `vars.EVAL_REGRESSION_THRESHOLD` (default 0.85).

Both require these to be set on the repository:

| Kind | Name | Purpose |
|------|------|---------|
| secret | `ANTHROPIC_API_KEY` | LLM-as-judge |
| secret | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Optional persistence |
| variable | `EVAL_API_BASE` | URL of the staging chat backend |
| variable | `EVAL_BOT_PUBLIC_ID` | Long-lived demo bot in that backend |
| variable | `EVAL_REGRESSION_THRESHOLD` | Optional, default `0.85` |

### Unit tests for the runner

```bash
pytest -q tests/test_evals_runner.py tests/test_evals_compare_and_sink.py
```

Run on every PR — covers dataset schema, deterministic metrics, the
judge JSON parser, the SSE client, runner orchestration, the
compare/diff logic, and the Langfuse sink (with a mocked client). No
real Anthropic / OpenAI / Langfuse calls.

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
4) pgvector Integration (separate job with PostgreSQL service)  
5) Coverage Snapshot
