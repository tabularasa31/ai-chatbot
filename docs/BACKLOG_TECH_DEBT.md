# Technical Debt Backlog

Technical debt — not user-facing features, but codebase and infrastructure improvements.

---

## Grok Code Review Notes (2026-03-19)

**Grok Overall Rating:** 8/10 Technical Quality, 7.5/10 Production Readiness

Key recommendations added to this backlog:
- Rate limiting — **baseline shipped** (validate, search, chat, widget); **tier 2** (quotas, per-client global caps) still in `BACKLOG_EMBED-PHASE2.md`
- Background tasks for embeddings (FI-021, already tracked)
- Structured logging (new — structlog/loguru)
- Chunking strategy — expose in UI/settings (new)
- Latency/retrieval quality metrics (new)
- Soft-delete for documents (new)
- Multiple file upload (new — in BACKLOG_PRODUCT)

---

## 🔴 P1 — Critical (before scale)

### ~~[FI-019] pgvector `<=>` instead of Python cosine similarity~~ ✅ Done
- Production uses native cosine distance + HNSW (see migration `dd643d1a544a`, `PROGRESS.md`).

### ~~[FI-019 ext] BM25 hybrid search + HNSW index~~ ✅ Done (2026-03-21)
- HNSW: индекс в миграции (раньше).
- BM25: `rank-bm25` в процессе запроса по чанкам клиента + RRF с векторным ранжированием (`backend/search/service.py`). Postgres full-text (`tsvector`) не используется — при росте корпуса см. кэширование / FTS в бэклоге.

### [FI-021] Embeddings generation → BackgroundTasks
- `POST /embeddings/documents/{id}` is synchronous → timeout with 20+ chunks.
- Move to FastAPI BackgroundTasks: `202 Accepted` immediately, status `pending → embedded`.

### [FI-026] GitHub Actions CI (pytest + ruff + eslint)
- 108+ tests without auto-run on PR.
- `.github/workflows/ci.yml`: pytest + coverage + ruff + eslint.
- Add tests for `build_rag_prompt()` and `validate/{api_key}`.

### ~~[Deps] Remove PyPDF2, update openai~~ ✅ Done (2026-03-20)
- ~~Remove `PyPDF2==3.0.1` (duplicate of pypdf).~~
- ~~Update `openai==1.6.0` → 1.60+.~~
- Мигрировано на `pypdf>=4.0.0` + `openai>=1.70.0` (ветка `chore/deps-pypdf2-openai`).

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
- Log to DB per request: `latency_ms`, `retrieved_chunks_count`, `best_score`, `mode` (vector / keyword / hybrid / none).
- Helps detect RAG quality degradation over time.
- Dashboard chart: avg latency per day, retrieval mode mix (в т.ч. `hybrid` на Postgres).
- **Effort:** 1 day

### [TD-033] Chunking Strategy Configuration
- Currently: fixed sentence-aware `chunk_text()` in `backend/embeddings/service.py` (`chunk_size`, `overlap_sentences`).
- Expose to client: `chunk_size`, `overlap_sentences` / token targets, optional `splitter_type` in settings.
- Options: current sentence splitter, future markdown-aware / structural / semantic.
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
