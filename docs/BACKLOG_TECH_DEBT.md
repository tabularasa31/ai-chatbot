# Technical Debt Backlog

Technical debt — not user-facing features, but codebase and infrastructure improvements.

---

## 🔴 P1 — Critical (before scale)

### [FI-019] pgvector `<=>` instead of Python cosine similarity
- We load ALL client embeddings into Python and compute cosine — O(n) per request.
- Switch to native SQL: `ORDER BY vector <=> '[...]'::vector LIMIT 5`.
- Add `pgvector` Python package, HNSW index.

### [FI-019 ext] BM25 hybrid search + HNSW index
- Add BM25 (Postgres full-text) to pgvector retrieval.
- RRF or weighted sum `0.7 vector + 0.3 BM25`.
- HNSW index for scaling.

### [FI-021] Embeddings generation → BackgroundTasks
- `POST /embeddings/documents/{id}` is synchronous → timeout with 20+ chunks.
- Move to FastAPI BackgroundTasks: `202 Accepted` immediately, status `pending → embedded`.

### [FI-026] GitHub Actions CI (pytest + ruff + eslint)
- 108+ tests without auto-run on PR.
- `.github/workflows/ci.yml`: pytest + coverage + ruff + eslint.
- Add tests for `build_rag_prompt()` and `validate/{api_key}`.

### [Deps] Remove PyPDF2, update openai
- Remove `PyPDF2==3.0.1` (duplicate of pypdf).
- Update `openai==1.6.0` → 1.60+.

### ~~[Quick] GPT-3.5-turbo → gpt-4o-mini~~ ✅ Done (FI-033)
- ~~One line: `model = "gpt-4o-mini"`.~~
- ~~Cheaper or similar cost, better prompt adherence.~~

---

## 🟠 P2 — DevEx & infrastructure

### [FI-025] Docker Compose for local dev
- `docker-compose.yml` with pgvector/pgvector:pg15 + healthcheck.
- `docker compose up` instead of `docker run ...`.

### [Refactor] Dead code in backend/auth/middleware.py
- `JWTMiddleware` is no longer used.
- Remove unused class.

### [FI-020] asyncpg + SQLAlchemy async
- Synchronous code blocks Uvicorn worker for 1–3 sec on OpenAI calls.
- Minimal fix: `asyncio.run_in_executor` for OpenAI.
- Full fix: asyncpg + SQLAlchemy 2.0 async.

---

## 🟡 P3 — Tests and coverage

### [FI-024] pytest-postgresql for vector search tests
- SQLite doesn't support pgvector → vector search not tested.
- Add pytest-postgresql or separate profile with real PostgreSQL.

### [FI-030] RAG metrics & observability
- Integrate Langfuse / Phoenix for per-request tracing.
- RAGAS / DeepEval metrics: faithfulness, context precision, answer relevance.
- Cost per query per tenant.
