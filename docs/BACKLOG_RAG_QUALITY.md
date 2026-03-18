# RAG Quality Backlog

Всё что улучшает качество ответов бота.
Подробное исследование (6 моделей) — в `RAG_QUALITY_RESEARCH.md`.

---

## 🔴 P1 — Делаем в первую очередь

### [FI-031] Org config layer
**Проблема:** Бот не знает email поддержки, тестового периода, контактов — их нет в документах.

**Решение:**
- Поле `org_config` (JSON) на модели Client: `support_email`, `account_manager`, `trial_period`, `support_url`.
- UI в дашборде: форма для заполнения.
- В `build_rag_prompt()` — вставлять org_config блоком перед контекстом.

**Делать ДО FI-007** (system prompt без org config будет неполным).

---

### [FI-007] Per-client system prompt
**5 критических элементов (консенсус всех 6 моделей):**
1. Роль и цель: "Ты — техподдержка [Название]. Отвечай только из контекста."
2. Политика правдивости: "Если нет — не выдумывай, направь к: support_email, account_manager."
3. Язык: "Отвечай на языке вопроса."
4. Работа с несколькими чанками: "Объедини, укажи противоречия явно."
5. Ограничение фантазии: "Не придумывай endpoints, тарифы, параметры."

**Дополнительно (Grok):** Структура ответа:
```
1. Краткий прямой ответ (1–2 предложения)
2. Цитата [1]
3. Доп. шаги / предупреждения
4. Если биллинг/контракт → направить к менеджеру
```

**Хранить:** system_prompt в БД с version + tenant_id (prompt versioning, по Sonnet).

---

### [FI-009] Improved chunking + metadata
**Рекомендации всех 6 моделей:**
- `chunk_size = 450 токенов`, `chunk_overlap = 120 токенов` (по Grok — самые конкретные).
- Recursive splitter: `["\n\n", "\n", ". ", " ", ""]`.
- Структурный chunking: бить по заголовкам (H1–H3), не разрывать код-блоки.
- Small-to-Big / Sentence-window: маленький чанк для поиска, ±2 предложения при выдаче.
- Metadata на каждом чанке: `section_title`, `doc_id`, `doc_date`, `content_type`, `tenant_id`.

---

### [FI-033] Query expansion / rewriting
**Проблема:** Качество зависит от формулировки вопроса.

**Варианты:**
- Query rewriting: нормализовать перед retrieval.
- Query expansion: 2–3 перефразировки + RRF по всем результатам.

---

### [FI-034] LLM-based answer validation
**Идея:** После генерации спросить модель: "Есть ли в контексте явный ответ?"
- Если нет → fallback.
- Дополнительный слой поверх cosine threshold.

---

### [FI-032] Document health check
**Идея:** После загрузки документа — автоматический GPT-анализ:
- Неполные разделы (нет ответа на типичные вопросы).
- Нерабочие / устаревшие URL.
- Пробелы в документации.
- Плохая структура для RAG (длинные разделы без подзаголовков).

**UI:** таб/секция "Document health" в дашборде с предупреждениями.
**Effort:** 3 дня.

---

## 🟠 P2 — Следующий спринт

### [FI-036] HyDE (Hypothetical Document Embeddings)
**Когда:** Вопрос типа "лимиты API?" — слишком короткий.
- Генерировать гипотетический ответ через GPT → искать чанки по нему.
- Помогает при коротких/расплывчатых вопросах.

### [FI-037] Approved Answers Layer
**Идея:** 👍-ответы → отдельный слой поиска.

```
Вопрос → Semantic search в approved_answers (>0.85?) → Instant answer (без GPT)
                                                  ↓ нет
                                            Обычный RAG
```

- Уровень 1 MVP: таблица `approved_answers`, при 👍 → запись.
- Уровень 2: ideal_answer как эталон + редактирование в /review.
- Уровень 3: (question + context + ideal_answer) → датасет для fine-tuning.

### [FI-029] Document versioning & recency scoring
- Поля: `is_current`, `version`, `valid_until`, `updated_at`.
- Фильтр при retrieval: `WHERE is_current = true`.
- Recency scoring: `final_score = semantic * 0.7 + recency * 0.3`.
- При обновлении: новая версия + старая `is_current=false`.
- Namespace per major version (api-v1, api-v2).

### [FI-008] Hybrid search (уже частично есть, расширить)
- Текущий fallback keyword < 0.3 — расширить до полноценного BM25.
- RRF или weighted `0.7 vector + 0.3 BM25`.
- После retrieval: cross-encoder rerank (Cohere Rerank 3.5 или flashrank).

---

## 🟡 P3 — Долгосрочно

### [FI-028] Cross-lingual retrieval
- Перейти на multilingual embeddings: **Jina v3/v4** или **Cohere Embed v3 multilingual**.
- System prompt: "Отвечай на языке вопроса, даже если контекст на другом."
- Для критичных клиентов: хранить чанки на оригинале + машинный перевод на EN.

### [FI-011] FAQ layer (автогенерация из тикетов)
- Не ручной ввод Q&A, а автогенерация из загруженных тикетов.
- Клиент одобряет/отклоняет предложенные пары.

---

## Исследование

Полные рекомендации 6 моделей (Perplexity, ChatGPT, DeepSeek, Gemini, Sonnet, Grok) → `RAG_QUALITY_RESEARCH.md`.
