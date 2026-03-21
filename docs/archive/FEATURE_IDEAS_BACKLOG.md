# AI Chatbot — RAG Quality Backlog

Краткий план развития качества ответов и поведения бота.

## 📌 P1 — Quality & Safety (ближайшие спринты)

### [FI-031] Org config layer (support_email, trial, контакты вне RAG)
**Идея:** Статичные данные клиента хранятся отдельно от RAG и всегда вставляются в system prompt.

**Проблема:** Бот не знает email поддержки, тестового периода, контактов — потому что их нет в документах.

**Что сделать:**
- Поле `org_config` (JSON) на модели Client.
- UI в дашборде: форма (support_email, account_manager, trial_period, support_url).
- В `build_rag_prompt()` — вставлять org_config блоком перед контекстом.
- Делать **до FI-007** (system prompt без org config будет неполным).

---

### [FI-032] Document health check (GPT-анализ качества документов)
**Идея:** После загрузки документа — автоматический анализ и отчёт клиенту о проблемах.

**Что анализировать:**
- Неполные разделы (нет ответа на типичные вопросы).
- Устаревшие/нерабочие URL.
- Пробелы в документации (типичные темы без описания).
- Плохая структура для RAG (длинные разделы без подзаголовков).

**Реализация:** BackgroundTask → GPT анализ → таб "Document health" в дашборде.

---

### [FI-033] Query expansion / rewriting перед retrieval
**Идея:** Нормализовать и перефразировать вопрос перед поиском — решает проблему зависимости качества от формулировки.

**Варианты:**
- Одна нормализованная формулировка (query rewriting).
- 2–3 параллельных перефразировки + RRF по всем (query expansion).

---

### ~~[FI-034] LLM-based answer validation (post-generation)~~ ✅ Done (2026-03-21)
См. `docs/PROGRESS.md` и `docs/BACKLOG_RAG_QUALITY.md`.

---

### [FI-007] Per-client system prompt (RAG instructions)
**Идея:** У каждого клиента своё текстовое описание ассистента:
- кто он (поддержка CDNvideo / SaaS / API);
- какой тон/язык использовать;
- что можно / нельзя отвечать.

**Что сделать:**
- Добавить поле `system_prompt` в модель Client
- Показать textarea в Dashboard (Client settings)
- В `build_rag_prompt()` подмешивать system_prompt сверху, если он задан

---

### [FI-008] Hybrid search (vector + keyword fallback)
**Идея:** Если векторный поиск не уверен (низкая similarity), включать резервный keyword-поиск по chunk_text.

**Что сделать:**
- `keyword_search_chunks()` в backend/search/service.py
- Порог на max similarity (например, 0.3)
- Если ниже порога → искать по ключевым словам (CORS, DVR, limits и т.п.)

---

### [FI-009] Improved chunking + metadata
**Идея:** Чанк = логический раздел, а не тупой кусок по размеру.

**Что сделать:**
- Резать текст по заголовкам (`#`, `##`, `###`)
- В Embedding.metadata хранить:
  - путь файла
  - список заголовков (breadcrumb)
  - тип документа (http/live/api/cors/...)
- В ответах использовать эти метаданные ("раздел HTTP → CORS")

---

### [FI-010] Feedback on answers (👍/👎) + bad answers report
**Идея:** Видеть реальные провалы, а не придумывать их.

**Что сделать:**
- Поле `feedback` в Message (none/up/down)
- Эндпоинт `POST /chat/messages/{id}/feedback`
- Кнопки 👍/👎 в виджете и в Dashboard логах
- Фильтр "только плохие ответы" в Logs

---

## 📌 P2 — Channels & UX

### [FI-001] Telegram интеграция
(см. FEATURE_IDEAS.md)

### [FI-005] Приветственное сообщение от бота
(см. FEATURE_IDEAS.md)

### [FI-012] Logs UX polish
**Идея:** Доработать страницу /logs для удобства оператора.

**Что сделать:**
- Пустые состояния:
  - "No sessions yet" при отсутствии сессий
  - "Select a session to view conversation" при незаданной сессии
  - "No messages in this session" если сессия без сообщений
- Возможно добавить поиск/фильтр по тексту последнего вопроса.

---

## 📌 P2 — RAG Pipeline improvements

### [FI-036] HyDE (Hypothetical Document Embeddings)
**Идея:** Для коротких/расплывчатых вопросов генерировать гипотетический ответ через GPT, затем искать чанки по нему.

**Когда:** Вопрос типа "лимиты API?" — слишком короткий для хорошего retrieval.

---

### [FI-037] Approved Answers Layer (👍 → instant answers)
**Идея:** Ответы, помеченные 👍, становятся отдельным слоем поиска. Перед RAG — ищем в проверенных ответах.

**Pipeline:**
```
Вопрос → Semantic search в approved_answers (>0.85?) → Instant answer (без GPT)
                                                  ↓ нет
                                            Обычный RAG
```

**Источники данных:** `Message.feedback = up` + `Message.ideal_answer`.

---

### [FI-029] Document versioning & recency scoring
**Идея:** При обновлении документа — новая версия, старая помечается `is_current=false`.

**Recency scoring:** `final_score = semantic_score * (0.7 + 0.3 * recency_score)`.

---

## 📌 P3 — Scaling & Plans

### [FI-003] Per-user rate limiting
### [FI-004] Redis-backed sliding window rate limiting
### [FI-011] FAQ layer above RAG
### [FI-028] Cross-lingual retrieval (Jina v3 / Cohere Embed v3)
### [FI-035] Prompt injection protection
### [FI-030] RAG metrics dashboard (RAGAS/Giskard/DeepEval + Langfuse)

(подробности в FEATURE_IDEAS.md и RAG_QUALITY_RESEARCH.md)
