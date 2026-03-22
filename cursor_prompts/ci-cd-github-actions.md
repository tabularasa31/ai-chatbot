# Infrastructure: CI/CD GitHub Actions — reference (implemented)

This prompt described **FI-026**, now implemented. Use this file as the **source of truth** for how CI works in this repo; do not copy older snippets that run `pytest` from `backend/` or require a Postgres service for the current test suite.

---

## What runs

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

**Triggers:** `push` and `pull_request` to `main` and `deploy`.

**Backend job**

- Python **3.11**, `pip install -r backend/requirements.txt` (from repository root).
- `ruff check backend` — config: [`backend/ruff.toml`](../backend/ruff.toml) (E/F/W; migrations excluded; intentional late imports in `main.py` / `chat/service.py` ignored for E402).
- `pytest tests/ -q --cov=backend --cov-report=term-missing` — **must run from the repository root**; tests live in [`tests/`](../tests/) and use **SQLite** via [`tests/conftest.py`](../tests/conftest.py). No Postgres service in CI.

**Frontend job**

- Node **20**, `npm ci` in `frontend/`, `npm run lint`, `npm run build`.
- `NEXT_PUBLIC_API_URL=https://ci.invalid` for build only (placeholder; real API not required).

**Secrets:** not required for CI — test `ENCRYPTION_KEY` and related vars are set inline in the workflow (aligned with conftest defaults).

---

## Dependencies

- [`backend/requirements.txt`](../backend/requirements.txt) includes **`pgvector>=0.2.0`** (needed to import `backend.models` in tests) and **`ruff>=0.3.0`**.

---

## Local parity

From repo root (Python 3.11+ recommended):

```bash
pip install -r backend/requirements.txt
ruff check backend
pytest tests/ -q --cov=backend --cov-report=term-missing
```

Frontend:

```bash
cd frontend && npm ci && npm run lint && NEXT_PUBLIC_API_URL=https://ci.invalid npm run build
```

---

## PR description template (English)

```markdown
## Summary
GitHub Actions CI: backend (ruff + pytest + coverage) and frontend (eslint + next build) on PR/push to `main` and `deploy`.

## Changes
- `.github/workflows/ci.yml`
- `backend/ruff.toml`, `backend/requirements.txt` (ruff + pgvector)

## Testing
- [ ] CI green on this PR
```
