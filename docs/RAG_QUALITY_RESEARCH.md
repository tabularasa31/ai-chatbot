# RAG Quality Research

Результаты опроса других AI-моделей по улучшению качества RAG.
Источники: Perplexity + ChatGPT, 2026-03-18.

---

## 1. Chunking для техдоков

- Overlap **обязателен**: 256–512 токенов, overlap 10–20% (50–100 токенов для чанка 500).
- Структурный подход для техдоков:
  - Сначала бить по структуре (h2/h3, endpoints, таблицы параметров).
  - Внутри больших секций — токен-базовый chunking 300–500 токенов с overlap.
  - Не разрывать код-блоки и примеры запросов.
- Рецепт для старта:
  - 384–512 токенов, 60–80 токенов overlap.
  - Меньшие чанки (200–300) для FAQ/ошибок.

**FI-009 обновить:** добавить overlap 60–80 токенов + структурный chunking.

---

## 2. Graceful degradation (когда информации нет)

Три класса ситуаций:

1. **Нет в документах, но типичный вопрос саппорта** → хранить в org config:
   - support_email, support_portal_url, sales_contact, trial_length_days.
   - Передавать в system prompt всегда (не через RAG).

2. **Нет нигде** → строгий policy в system prompt:
   - Если cosine similarity < 0.3–0.35 → бот обязан сказать, что данных нет, и предложить контакт.
   - Порог можно делать динамическим + учитывать gap между 1-м и 2-м результатом.

3. **Паттерны эскалации:**
   - RAG → low-confidence → шаблон "К сожалению, в документации нет ответа. Обратитесь на..."
   - FAQ-слой над RAG для "очевидных" вопросов (email, тарифы верхнего уровня).
   - System prompt: "Если информации нет — не выдумывай, направь к [контакты]."

---

## 3. Reranking и hybrid search

- Rerank даёт +20–30% к качеству верхнего результата.
- Схема:
  - Этап 1: pgvector + BM25 → топ-30/50 чанков.
  - Этап 2: cross-encoder (MS MARCO) → переупорядочить → топ-3–5.
- Hybrid search (BM25 + vector):
  - BM25 → точные совпадения (endpoints, error codes, флаги).
  - Vector → перефразировки и семантика.
- Рекомендация: подавать 6–10 чанков в prompt, просить ссылаться только на явно релевантные.

**FI-019 расширить:** добавить BM25 (Postgres full-text) + optional cross-encoder rerank.

---

## 4. Версионирование документов

- Явное версионирование: `version`, `updated_at`, `valid_from`, `valid_until`, `is_current`.
- Метаданные тянутся в чанки.
- Запросы фильтруют по `is_current=true`.
- Recency scoring: `final_score = semantic_score * (0.7 + 0.3 * recency_score)`.
- При обновлении дока — новая версия + старая помечается `is_current=false`.
- Для тестовых ссылок: вынести base URLs в конфиг/таблицу, не хранить в доках.
- В документах писать "Updated: Январь 2026" — помогает LLM интерпретировать.

**Новая фича:** FI-029 Document versioning & recency scoring.

---

## 5. System prompt для техдоков

Критические элементы:

1. **Политика правдивости и эскалации:**
   - "Если информации нет в документах или конфиге — не выдумывай. Направь к: support_email, support_portal_url, account_manager."

2. **Привязка к источнику:**
   - "Отвечай только на основе переданного контекста. Если контекст противоречив — опиши это и предложи уточнить у поддержки."

3. **Язык и стиль:**
   - Кратко, без маркетинга, с примерами API-запросов, код-блоками.
   - "Отвечай на языке вопроса."

4. **Работа с несколькими чанками:**
   - "Объедини информацию из нескольких фрагментов. Если есть противоречия — укажи явно."

5. **Ограничение фантазии:**
   - "Не придумывай endpoints, параметры, тарифы. Если нет — скажи, что нет, и направь к документации или поддержке."

**FI-007 уточнить:** включить все эти элементы в per-client system prompt.

---

## 6. Cross-lingual retrieval

- `text-embedding-3-small` ок, но для продового мультиязычного RAG лучше:
  - **Voyage-multilingual-2** (SaaS) — сильный cross-lingual retrieval.
  - **e5-mistral** (open-source) — современный multilingual вариант.
- Практика:
  - Один multilingual embedding space для всех тенантов.
  - В system prompt явно: "Отвечай на языке пользователя, даже если контекст на другом языке."
  - Для критичных клиентов: хранить 2 набора чанков (оригинал + машинный перевод на EN).

**FI-028 обновить:** добавить Voyage-multilingual-2 как основного кандидата.

---

## 7. Метрики качества RAG

**Retrieval:**
- Contextual relevancy@k: доля запросов где среди топ-k есть правильный фрагмент.
- Precision@k: доля релевантных чанков в топ-k.

**Generation:**
- Answer faithfulness (RAGAS) — насколько ответ опирается на контекст, не галлюцинирует.
- Answer relevancy — насколько ответ закрывает вопрос.
- Baseline: ручная аннотация 100–200 реальных тикетов.

**Продуктовые:**
- CSAT по ответам бота.
- Escalation rate: доля диалогов переданных человеку.
- Single-turn resolution rate: сколько вопросов решается без уточнений.

**Инструменты:** RAGAS, Giskard.

---

## 8. Архитектурные рекомендации для B2B

1. **Разделить слои знаний:**
   - Org config (support email, тарифы, домены, лимиты) → всегда в system prompt.
   - Product docs → RAG.
   - Operational data (статусы инцидентов, лимиты аккаунта) → прямые запросы к API/БД.

2. **Retrieval стек:**
   - pgvector + BM25 hybrid.
   - Optional cross-encoder rerank для сложных запросов.
   - Жёсткая изоляция по tenant_id.

3. **Модели:**
   - Multilingual embeddings (Voyage-multilingual-2 / e5-mistral).
   - Смешанный режим генерации: gpt-4o-mini по умолчанию, более сильная модель при low-confidence.

4. **Per-tenant настройки:**
   - Язык документов и ответов.
   - Каналы эскалации.
   - Жёсткость политики "не выдумывать".

---

---

## Источник 2: ChatGPT

### Что совпадает с Perplexity
- Overlap 50–100 токенов — оба согласны.
- Hybrid search BM25 + vector — оба согласны.
- Reranking через cross-encoder или Cohere — оба согласны.
- Graceful degradation с 3 уровнями — оба согласны.
- Метрики: Faithfulness, Recall@k, product thumbs up/down — оба согласны.

### Что добавил ChatGPT (новое)

#### 1. LLM-based answer validation
После генерации ответа — спросить модель:
> "Есть ли в контексте явный ответ на вопрос пользователя?"

Если нет → fallback. Это дополнительный слой поверх similarity threshold.

#### 2. Query rewriting слой
Перед retrieval — нормализовать и улучшить вопрос:
- исправление формулировки,
- нормализация терминов,
- добавление контекста.

Эффект: одна и та же суть вопроса с разными формулировками → одинаково хорошие ответы.

**Это решает нашу проблему №4** (качество зависит от формулировки).

#### 3. Multi-step RAG pipeline

Не: `retrieve → answer`

А:
```
rewrite → retrieve → rerank → validate → answer
```

#### 4. Tool layer для structured knowledge

Вопросы типа "email поддержки", "есть ли trial" — это не RAG, это структурированные данные.
Нужен отдельный слой: structured lookup перед RAG.

**Это подтверждает FI-031 (org config layer)** — только теперь это называется "tool layer".

#### 5. Caching
- Популярные вопросы кэшировать (ответы).
- Embeddings для частых запросов — тоже.
- Снижает latency и стоимость.

#### 6. Observability
Логировать каждый запрос:
- query,
- retrieved chunks (с similarity scores),
- final answer,
- confidence score.

**Это уже частично есть** (наш `/chat/debug`), но нужно расширить.

### TOP-5 по ChatGPT (приоритеты)
1. Overlap + нормальный chunking
2. Reranking (must-have)
3. Hybrid search
4. Confidence + fallback UX
5. Query rewriting

---

---

## Источник 3: DeepSeek

### Что совпадает с Perplexity + ChatGPT
- Overlap 50–75 токенов (чуть консервативнее, но в том же диапазоне).
- Hybrid search BM25 + vector с RRF или взвешенной суммой.
- Reranking cross-encoder (ms-marco-MiniLM-L-6-v2 как локальная альтернатива Cohere).
- Graceful degradation: 3 уровня + fallback фразы.
- Метрики: Precision@k, Answer Relevance, Fallback Rate, User Feedback.

### Что добавил DeepSeek (новое или уточнённое)

#### 1. Prompt injection защита
Явно упомянул санитизацию ввода и защиту от prompt injection — проверять входящие сообщения на попытки сменить роль бота.

**Новая задача:** FI-035 — Security: prompt injection protection.

#### 2. Cost per query метрика
Мониторинг расходов на OpenAI API на запрос. Важно для ценообразования тарифов.

#### 3. A/B тестирование компонентов
Возможность быстро переключать модели chunking, retrieval, промпты для сравнения качества. Нужна инфраструктура.

#### 4. CI/CD для базы знаний
Автоматический пайплайн при обновлении документации: загрузка → chunking → embeddings → деплой.

#### 5. HNSW индекс в pgvector
Явно указал использовать HNSW индекс для ускорения поиска при масштабировании.

#### 6. Читаемые fallback-фразы
Конкретные шаблоны для system prompt:
> "К сожалению, в нашей документации нет информации по этому вопросу. Рекомендую обратиться в службу поддержки по адресу support@company.com — наши специалисты помогут вам."

#### 7. Метаданные в контексте промпта
Передавать версию и дату документа прямо в промпт, чтобы LLM оценивала актуальность.

#### 8. Локальные эмбеддинги для снижения затрат
`intfloat/multilingual-e5-small` как дешёвая альтернатива OpenAI для эмбеддингов при масштабировании.

---

## Сравнение источников

| Тема | Perplexity | ChatGPT | DeepSeek | Вывод |
|------|-----------|---------|----------|-------|
| Overlap | 60–80 | 50–100 | 50–75 | ~60–80 ✅ делать |
| Hybrid search | BM25 + pgvector | BM25 + embeddings | BM25 + vector + RRF | ✅ консенсус |
| Reranking | Cross-encoder + Cohere | Cross-encoder | ms-marco локально | ✅ консенсус |
| Graceful degradation | 3 уровня + org config | 3 уровня + tool layer | 3 уровня + fallback фразы | ✅ FI-031 |
| Cross-lingual | Voyage-multilingual-2 | text-embedding-3-large | multilingual-e5-large | Исследовать, P3 |
| Query rewriting | Не упомянул | 🔥 Рекомендует | Не упомянул | FI-033, P2 |
| LLM validation | Не упомянул | 🔥 Рекомендует | Косвенно | FI-034, P2 |
| Prompt injection | Не упомянул | Не упомянул | 🔥 Упомянул | FI-035, P2 |
| Cost per query | Не упомянул | Не упомянул | 🔥 Упомянул | В метрики |
| HNSW index | Не упомянул | Не упомянул | 🔥 Упомянул | Добавить при FI-019 |
| Caching | Не упомянул | Рекомендует | Redis + in-memory | P3 |
| Observability | Важно | Очень важно | Prometheus + Grafana | Расширить /chat/debug |

---

## Новые FI из этого исследования

| FI | Что | Источник | Приоритет |
|----|-----|----------|-----------|
| FI-009 update | Chunking: overlap 60–80 токенов + структурный | Все три | P1 |
| FI-007 update | System prompt: 5 элементов + fallback-фразы | Все три | P1 |
| FI-019 update | Hybrid search: BM25 + vector + rerank + HNSW | Все три | P1 |
| FI-031 | Org config / tool layer (support_email вне RAG) | Все три | P1 |
| FI-033 | Query rewriting перед retrieval | ChatGPT | P2 |
| FI-034 | LLM-based answer validation (post-generation) | ChatGPT | P2 |
| FI-029 | Document versioning & recency scoring | Perplexity + DeepSeek | P2 |
| FI-035 | Security: prompt injection protection | DeepSeek | P2 |
| FI-028 update | Cross-lingual: Voyage-multilingual-2 / e5-large | Все три | P3 |
| FI-030 | RAG metrics dashboard (RAGAS + cost per query) | Все три | P3 |
