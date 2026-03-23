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

### ~~[FI-021] Embeddings generation → BackgroundTasks~~ ✅ Done (2026-03-22)
- `POST /embeddings/documents/{id}` returns `202 Accepted` immediately.
- Added `DocumentStatus.embedding`; background task sets `ready` on success, `error` on failure.
- Frontend polls `GET /documents/{id}` every 2 s until status leaves `embedding`.

### ~~[FI-026] GitHub Actions CI (pytest + ruff + eslint)~~ ✅ Done (2026-03-22)
- `.github/workflows/ci.yml` on `main` + `deploy`: backend Ruff + `pytest tests/` из корня репо; frontend eslint + `next build`.
- Подробности: `PROGRESS.md` (блок FI-026), `cursor_prompts/ci-cd-github-actions.md`.
- Опционально позже: тесты для `build_rag_prompt()` и `validate/{api_key}`.

### ~~[Deps] Remove PyPDF2, update openai~~ ✅ Done (2026-03-20)
- ~~Remove `PyPDF2==3.0.1` (duplicate of pypdf).~~
- ~~Update `openai==1.6.0` → 1.60+.~~
- Мигрировано на `pypdf>=4.0.0` + `openai>=1.70.0` (ветка `chore/deps-pypdf2-openai`).

### ~~[Quick] GPT-3.5-turbo → gpt-4o-mini~~ ✅ Done (FI-033)
- ~~One line: `model = "gpt-4o-mini"`.~~
- ~~Cheaper or similar cost, better prompt adherence.~~

---

## 🟠 P2 — DevEx & infrastructure

### ~~[FI-025] Docker Compose for local dev~~ ✅ Done (2026-03-23)
- `docker-compose.yml` с `pgvector/pgvector:pg15`, healthcheck, именованным volume `pgdata`.
- `docker compose up -d` вместо `docker run ...`.

### ~~[Refactor] Dead code in backend/auth/middleware.py~~ ✅ Already gone
- `JWTMiddleware` не найден в кодовой базе — был удалён ранее.

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

### ~~[TD-033] Chunking Strategy Configuration~~ ✅ Done (2026-03-22)
- Клиентские настройки не нужны — выбраны оптимальные значения по типу документа.
- `CHUNKING_CONFIG` в `backend/embeddings/service.py`: `swagger` 500/0, `markdown` 700/1, `pdf` 1000/1; fallback 700/1.
- Для будущих типов (`logs` 300/0, `code` 600/1) конфиг уже добавлен.

### [TD-034] Soft-Delete for Documents
- Currently: hard delete removes document and embeddings immediately.
- Risk: user accidentally deletes important doc.
- Add `deleted_at` column + restore option in dashboard.
- **Effort:** 1 day

### ~~[TD-035] background_tasks for Embeddings (already tracked as FI-021)~~ ✅ Done
- See FI-021 above.

### [TD-036] JSDoc for Widget (vanilla JS)
- embed.js is ~100 lines, no type hints.
- Add JSDoc comments for maintainability.
- Consider extracting to separate npm package later.
- **Effort:** 2 hours
