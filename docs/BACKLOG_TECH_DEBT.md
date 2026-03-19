# Technical Debt Backlog

Technical debt — not user-facing features, but codebase and infrastructure improvements.

---

## Grok Code Review Notes (2026-03-19)

**Grok Overall Rating:** 8/10 Technical Quality, 7.5/10 Production Readiness

Key recommendations added to this backlog:
- Rate limiting (already in progress, FI-EMBED Phase 2)
- Background tasks for embeddings (FI-021, already tracked)
- Structured logging (new — structlog/loguru)
- Chunking strategy — expose in UI/settings (new)
- Latency/retrieval quality metrics (new)
- Soft-delete for documents (new)
- Multiple file upload (new — in BACKLOG_PRODUCT)

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

---

## 🟢 P4 — Nice to have (added from Grok review 2026-03-19)

### [TD-031] Structured logging (structlog / loguru)
- Replace basic print/logging with structured JSON logging.
- Use structlog or loguru for consistent log format.
- Useful for production debugging and log aggregation.
- **Effort:** 0.5 days

### [TD-032] Latency & Retrieval Quality Metrics
- Log to DB per request: `latency_ms`, `retrieved_chunks_count`, `best_score`, `mode` (vector/keyword/none).
- Helps detect RAG quality degradation over time.
- Dashboard chart: avg latency per day, % vector hits vs keyword hits.
- **Effort:** 1 day

### [TD-033] Chunking Strategy Configuration
- Currently: RecursiveCharacterTextSplitter with fixed params.
- Expose to client: `chunk_size`, `chunk_overlap`, `splitter_type` in settings.
- Options: recursive, markdown-aware, semantic.
- **Effort:** 1-2 days

### [TD-034] Soft-Delete for Documents
- Currently: hard delete removes document and embeddings immediately.
- Risk: user accidentally deletes important doc.
- Add `deleted_at` column + restore option in dashboard.
- **Effort:** 1 day

### [TD-035] background_tasks for Embeddings (already tracked as FI-021)
- See FI-021 above. Grok confirms: critical for UX with large docs.

### [TD-036] JSDoc for Widget (vanilla JS)
- embed.js is ~100 lines, no type hints.
- Add JSDoc comments for maintainability.
- Consider extracting to separate npm package later.
- **Effort:** 2 hours
