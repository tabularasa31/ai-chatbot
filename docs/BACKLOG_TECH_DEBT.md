# Technical Debt Backlog

Технические долги — не фичи для пользователей, а улучшения кодовой базы и инфраструктуры.

---

## 🔴 P1 — Критичные (до scale)

### [FI-019] pgvector `<=>` вместо Python cosine similarity
- Загружаем ВСЕ эмбеддинги клиента в Python и считаем cosine — O(n) на каждый запрос.
- Перейти на `ORDER BY vector <=> '[...]'::vector LIMIT 5` в SQL.
- Добавить `pgvector` Python-пакет, HNSW индекс.

### [FI-019 ext] BM25 hybrid search + HNSW index
- Добавить BM25 (Postgres full-text) к pgvector retrieval.
- RRF или weighted sum `0.7 vector + 0.3 BM25`.
- HNSW index для масштабирования.

### [FI-021] Embeddings generation → BackgroundTasks
- `POST /embeddings/documents/{id}` синхронный → таймаут при 20+ чанках.
- Перевести в FastAPI BackgroundTasks: `202 Accepted` сразу, статус `pending → embedded`.

### [FI-026] GitHub Actions CI (pytest + ruff + eslint)
- 108+ тестов без автозапуска на PR.
- `.github/workflows/ci.yml`: pytest + coverage + ruff + eslint.
- Добавить тесты на `build_rag_prompt()` и `validate/{api_key}`.

### [Deps] Удалить PyPDF2, обновить openai
- Удалить `PyPDF2==3.0.1` (дубль pypdf).
- Обновить `openai==1.6.0` → 1.60+.

### [Quick] GPT-3.5-turbo → gpt-4o-mini
- Одна строка: `model = "gpt-4o-mini"`.
- Дешевле или сопоставимо, лучше следует промптам.

---

## 🟠 P2 — DevEx & инфраструктура

### [FI-025] Docker Compose для local dev
- `docker-compose.yml` с pgvector/pgvector:pg15 + healthcheck.
- `docker compose up` вместо `docker run ...`.

### [Refactor] Мёртвый код в backend/auth/middleware.py
- `JWTMiddleware` больше не используется.
- Удалить неиспользуемый класс.

### [FI-020] asyncpg + SQLAlchemy async
- Синхронный код блокирует Uvicorn-воркер на 1–3 сек при OpenAI вызовах.
- Минимальный фикс: `asyncio.run_in_executor` для OpenAI.
- Полный фикс: asyncpg + SQLAlchemy 2.0 async.

---

## 🟡 P3 — Тесты и coverage

### [FI-024] pytest-postgresql для vector search тестов
- SQLite не поддерживает pgvector → vector search не тестируется.
- Добавить pytest-postgresql или отдельный profile с реальным PostgreSQL.

### [FI-030] RAG metrics & observability
- Интегрировать Langfuse / Phoenix для трейсинга каждого запроса.
- RAGAS / DeepEval метрики: faithfulness, context precision, answer relevance.
- Cost per query per tenant.
