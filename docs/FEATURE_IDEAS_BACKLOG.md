# AI Chatbot — RAG Quality Backlog

Краткий план развития качества ответов и поведения бота.

## 📌 P1 — Quality & Safety (ближайшие спринты)

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

## 📌 P3 — Scaling & Plans

### [FI-003] Per-user rate limiting
### [FI-004] Redis-backed sliding window rate limiting
### [FI-011] FAQ layer above RAG

(подробности в FEATURE_IDEAS.md)
