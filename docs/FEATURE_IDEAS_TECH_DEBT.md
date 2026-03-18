# Technical Debt & Infrastructure

Замечания из code review (2026-03-18). Отсортированы по приоритету.

---

## 🔴 P1 — Критичные (надо фиксить до scale)

### [FI-019] pgvector `<=>` вместо Python cosine similarity

**Проблема:**
- `search_similar_chunks()` загружает ВСЕ эмбеддинги клиента из БД в память,
- считает cosine similarity в Python — O(n) на каждый запрос.
- При 1000+ чанках → медленно и много памяти.

**Решение:**
- Перейти на нативный SQL через pgvector оператор:
  ```sql
  SELECT chunk_text FROM embeddings
  ORDER BY vector <=> '[...]'::vector
  LIMIT 5;
  ```
- Добавить Python-пакет `pgvector` в requirements.txt.
- Использовать `pgvector.sqlalchemy` для типа колонки в модели.

---

### [FI-021] Embeddings generation — перевести в BackgroundTasks

**Проблема:**
- `POST /embeddings/documents/{id}` вызывает OpenAI для каждого чанка синхронно в HTTP-запросе.
- Для документа с 20+ чанками — 20+ HTTP-запросов → риск timeout на Railway (30с).

**Решение:**
- Использовать FastAPI `BackgroundTasks` для генерации эмбеддингов.
- Эндпоинт отвечает немедленно (`202 Accepted`).
- Статус документа: `pending` → `processing` → `embedded` / `failed`.
- UI опционально может поллить статус.

---

### [FI-026] GitHub Actions CI (pytest + coverage)

**Проблема:**
- 108 тестов без автозапуска на PR — тесты фактически не защищают от регрессий.

**Решение:**
- Добавить `.github/workflows/ci.yml`:
  - Триггер: `push`/`pull_request` на `main`.
  - Шаги: `pip install`, `alembic upgrade head` (SQLite или testdb), `pytest --cov`.
  - Coverage report опционально.

---

### [Deps] Удалить PyPDF2, обновить openai

- В `requirements.txt` есть `pypdf==3.17.1` и `PyPDF2==3.0.1`.
  - `PyPDF2` — deprecated predecessor. Удалить.
- `openai==1.6.0` — декабрь 2023, очень старая.
  - Обновить до свежей стабильной (1.60+), проверить совместимость.

---

## 🟠 P2 — Безопасность & DevEx

### [FI-022] CORS — разделить по роутам

**Проблема:**
- `allow_origins=["*"]` применяется ко всем эндпоинтам, включая dashboard-API.
- Риск при переходе на cookie-based auth.

**Решение:**
- `allow_origins=["*"]` только для `/chat` и `/embed.js`.
- Остальные эндпоинты — ограничить конкретными origin'ами (`FRONTEND_URL`).

---

### [FI-023] Rate limit на `GET /clients/validate/{api_key}`

**Проблема:**
- Публичный эндпоинт без rate limit — возможен brute-force перебор ключей.

**Решение:**
- Добавить `@limiter.limit("20/minute")` на этот эндпоинт (как и на другие).

---

### [FI-025] Docker Compose для local dev

**Проблема:**
- README показывает `docker run ...` руками.
- Нет единой команды для поднятия всего окружения.

**Решение:**
- Добавить `docker-compose.yml`:
  - сервис `db`: `pgvector/pgvector:pg15`, с переменными и healthcheck.
  - сервис `backend` (опционально): build из Dockerfile.
- `docker compose up` поднимает PostgreSQL + pgvector за один шаг.

---

### [Refactor] Удалить мёртвый код из backend/auth/middleware.py

- `JWTMiddleware` там больше не используется (судя по review).
- Оставлять его = технический долг и путаница для новых разработчиков.
- Удалить неиспользуемый класс/код.

---

### [Quick] Обновить модель GPT-3.5-turbo → gpt-4o-mini

**Почему:**
- `gpt-4o-mini` дешевле или сопоставима по цене,
- значительно лучше для RAG и точнее следует системным промптам,
- поддерживает более длинный контекст.

**Что:** одна строка в `backend/core/config.py` или `chat/service.py`:
```python
model = "gpt-4o-mini"  # was "gpt-3.5-turbo"
```

**Effort:** 30 минут + проверка качества ответов.

---

### [FI-022 ext] CORS с белым списком доменов клиента

**Улучшение к FI-022:**
- Клиент в дашборде указывает `allowed_origins` (список доменов, на которых встроен виджет).
- Backend при запросе к `/chat` проверяет `Origin` заголовок против `Client.allowed_origins`.
- Если Origin не в списке → 403.

**Ценность:** Полная защита от использования API-ключа клиента на чужих сайтах.
**Effort:** 2 дня (+ миграция `Client.allowed_origins`, middleware, UI в дашборде).

---

## 🟡 P3 — Масштабирование

### [FI-020] Переход на async (asyncpg + SQLAlchemy async)

**Проблема:**
- FastAPI async, но код синхронный (`SessionLocal`, `get_db()` без asyncpg).
- Каждый вызов OpenAI API блокирует Uvicorn-воркер на 1–3 сек.

**Решение:**
- Минимальный фикс: `asyncio.run_in_executor` для OpenAI-вызовов.
- Полный фикс: asyncpg + SQLAlchemy 2.0 async session (большая работа).

---

### [FI-024] pytest-postgresql для vector search тестов

**Проблема:**
- Тесты используют SQLite, у которого нет pgvector.
- Весь vector search pipeline не покрыт тестами (source_documents = None в SQLite).

**Решение:**
- Добавить `pytest-postgresql` или отдельный pytest-profile с реальным PostgreSQL + pgvector.
- Минимально: явный mock для vector search в тестах.

---

(см. основной список фич в FEATURE_IDEAS.md и FEATURE_IDEAS_BACKLOG.md)
