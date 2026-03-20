# Infrastructure: CI/CD GitHub Actions — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b infra/ci-cd-github-actions
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `.github/workflows/ci.yml` — create new file
- `backend/ruff.toml` — create new file
- `backend/requirements.txt` — add `ruff>=0.3.0`

**Do NOT touch:**
- Any route, model, or service files
- migrations
- Frontend source files (only runs `npm run lint` and `npm run build`)

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** No automated checks on PRs. Broken code can be merged without anyone noticing until production.

**Goal:** On every push to `main` and every PR:
- Backend: run `ruff` (linter) + `pytest`
- Frontend: run `eslint` + `next build`

**Why ruff:** Replaces flake8 + isort + pyupgrade in one tool, 10-100x faster.

---

## WHAT TO DO

### 1. Create `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main, deploy]
  pull_request:
    branches: [main]

jobs:
  backend:
    name: Backend (pytest + ruff)
    runs-on: ubuntu-latest

    services:
      postgres:
        image: pgvector/pgvector:pg15
        env:
          POSTGRES_PASSWORD: password
          POSTGRES_DB: test_chatbot
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    env:
      DATABASE_URL: postgresql://postgres:password@localhost:5432/test_chatbot
      JWT_SECRET: test-secret-key-min-32-chars-long!!
      ENVIRONMENT: test
      ENCRYPTION_KEY: ${{ secrets.ENCRYPTION_KEY_TEST }}
      FRONTEND_URL: http://localhost:3000
      BREVO_API_KEY: test-key
      EMAIL_FROM: test@example.com

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          cd backend
          pip install -r requirements.txt
          pip install ruff

      - name: Lint with ruff
        run: |
          cd backend
          ruff check .

      - name: Run tests
        run: |
          cd backend
          pytest --cov=backend --cov-report=term-missing -q

  frontend:
    name: Frontend (eslint + build)
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Node.js 18
        uses: actions/setup-node@v4
        with:
          node-version: "18"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        run: |
          cd frontend
          npm ci

      - name: Lint
        run: |
          cd frontend
          npm run lint

      - name: Build
        run: |
          cd frontend
          npm run build
        env:
          NEXT_PUBLIC_API_URL: https://ai-chatbot-production-6531.up.railway.app
```

### 2. Create `backend/ruff.toml`

```toml
line-length = 100
target-version = "py311"

[lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]
```

### 3. Add `ruff` to `backend/requirements.txt`

```
ruff>=0.3.0
```

### 4. Add GitHub Secret

After merging, go to GitHub repo → Settings → Secrets → Actions → New secret:
- Name: `ENCRYPTION_KEY_TEST`
- Value: generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

(Note this in PR description so the reviewer knows to add it.)

---

## TESTING

Before pushing:
- [ ] `.github/workflows/ci.yml` is valid YAML (check with `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`)
- [ ] `ruff check backend/` runs locally without errors
- [ ] `pytest -q` still passes locally

---

## GIT PUSH

```bash
git add .github/workflows/ci.yml backend/ruff.toml backend/requirements.txt
git commit -m "infra: add GitHub Actions CI — pytest + ruff + eslint + next build"
git push origin infra/ci-cd-github-actions
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- First run will fail if `ENCRYPTION_KEY_TEST` secret is not set — add it before merging PR
- `pgvector/pgvector:pg15` image in CI matches production PostgreSQL version
- `ENVIRONMENT=test` makes rate limiter use random keys (no rate limiting in tests)
- Frontend build uses production API URL — build will succeed even without a running backend

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Added GitHub Actions CI pipeline: backend (ruff + pytest) and frontend (eslint + next build) run on every PR and push to main.

## Changes
- `.github/workflows/ci.yml` — CI pipeline with backend and frontend jobs
- `backend/ruff.toml` — ruff linter config
- `backend/requirements.txt` — added ruff>=0.3.0

## Testing
- [ ] CI runs on this PR
- [ ] Both backend and frontend jobs pass

## Notes
⚠️ Add ENCRYPTION_KEY_TEST secret in GitHub repo settings before merging.
Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
