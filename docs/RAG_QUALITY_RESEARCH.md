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

---

## Источник 4: Gemini

### Что совпадает со всеми тремя
- Overlap 50–75 токенов.
- Hybrid search (BM25 + vector) с RRF.
- Reranking через Cohere Rerank или BGE-Reranker (+20–30%).
- Graceful degradation: fallback + org config в system prompt.
- Метрики через RAGAS/TruLens.
- Tenant isolation по tenant_id.

### Что добавил Gemini (уникальное)

#### 1. Small-to-Big Retrieval
Новый паттерн chunking:
- Храни маленькие чанки (100–200 токенов) для поиска (точный retrieval).
- При нахождении → отдавай модели расширенный контекст вокруг чанка (parent document).
- Эффект: точность поиска + глубина ответа.

**Это новый подход к FI-009**, стоит изучить.

#### 2. Chain-of-Thought (CoT) в system prompt

```
Сначала проанализируй контекст внутри <thinking>,
затем давай ответ пользователю.
```

Повышает качество сложных технических ответов — модель "думает" перед ответом.

#### 3. HyDE (Hypothetical Document Embeddings)

Для коротких/расплывчатых вопросов:
- Генерируй гипотетический ответ через GPT.
- Ищи чанки не по вопросу, а по этому гипотетическому ответу.
- Эффект: лучший retrieval когда вопрос слишком короткий ("лимиты API?").

**Новая фича:** FI-036 — HyDE для коротких вопросов.

#### 4. Semantic caching (RedisVL)

Не просто кэш по точному запросу, а семантический:
- Два похожих вопроса с разными формулировками → один кэшированный ответ.
- Снижает latency и стоимость для популярных тем.

**Усиливает идею caching**, добавить в P3.

#### 5. Intent classifier (guardrails)

Классификатор намерений перед RAG:
- Вопрос про цены → сразу ссылка на прайс.
- Вопрос про лимиты → сразу контакт менеджера.
- Не пытаться "вытащить" из старого чанка то, что меняется часто.

**Усиливает FI-031** (org config layer).

#### 6. Citations в ответах

В system prompt: "Всегда указывай источник. Пример: [Документация: API Auth]."
- Пользователь видит откуда информация.
- Доверие к ответам растёт.

**Добавить в FI-007** (system prompt).

#### 7. Auto-invalidation в CI/CD

При обновлении документации:
- DELETE старых эмбеддингов по `source_id`.
- Загрузить новые.

**Усиливает FI-029** (document versioning).

---

---

## Источник 5: Claude Sonnet 4.6

### Что совпадает со всеми
- Overlap 50–75 токенов.
- Hybrid search BM25 + vector + RRF.
- Reranking.
- Graceful degradation + Knowledge Tiers.
- Метрики: RAGAS, faithfulness, context recall, fallback rate.
- Tenant isolation.
- Async ingestion.

### Что добавил Sonnet (уникальное)

#### 1. Knowledge Tiers — самый чёткий паттерн
Три явных уровня:
- **Tier 1:** Векторная БД (документы клиента).
- **Tier 2:** Статичные FAQ/контакты — отдельная таблица `tenant_static_knowledge`.
- **Tier 3:** Fallback-шаблоны по категориям.

Это самое чёткое описание того, что мы называем FI-031 (org config layer). Sonnet предлагает реализовать это как **отдельную таблицу в БД**, а не просто поле в клиенте:

```sql
CREATE TABLE tenant_static_knowledge (
  tenant_id TEXT,
  category TEXT, -- 'contacts', 'trial', 'billing', 'escalation'
  question_pattern TEXT,
  answer TEXT,
  escalation_contact TEXT
);
```

#### 2. Question routing/classifier

Keyword-based pre-routing перед RAG:
- `contact`, `trial`, `pricing` → сразу Tier 2/3, без RAG.
- `technical` → RAG pipeline.

```python
QUESTION_CATEGORIES = {
    "contact": ["email", "телефон", "связаться"],
    "trial": ["тест", "trial", "пробный"],
    "pricing": ["цена", "тариф", "стоимость"],
    "technical": ["api", "интеграция", "endpoint"]
}
```

Это **усиливает FI-031** и частично делает FI-036 (HyDE) более управляемым.

#### 3. Sentence-window retrieval

Уточнённый вариант Small-to-Big:
- Храни чанки по ~3 предложения.
- При retrieval возвращай ±2 предложения вокруг найденного.
- "Лучше, чем большой чанк с overlap" — по словам Sonnet.

#### 4. Query expansion (несколько перефразировок)

```python
async def expand_query(question: str) -> list[str]:
    # Генерируй 2-3 альтернативные формулировки
    # Retrieval по всем, объединяй через RRF
```

Это решает проблему #4 (зависимость от формулировки). Похоже на FI-033 (query rewriting), но шире — не одна нормализация, а несколько параллельных запросов.

#### 5. Версионирование промптов в БД

Хранить system prompt в таблице с `version` и `tenant_id`:
- Смена промпта без деплоя.
- Быстрые A/B эксперименты.
- Разные промпты для разных клиентов (FI-007).

**Это архитектурное решение, которое делает FI-007 намного мощнее.**

#### 6. TTL для нестабильного контента

Отдельный тип чанков с коротким TTL + предупреждение:
```python
if any(chunk.metadata["content_type"] == "versioned" for chunk in context):
    prompt += "\n⚠️ Часть информации может быть устаревшей."
```

#### 7. Конкретный system prompt шаблон

Самый подробный из всех четырёх:

```
ROLE: Ты — специалист технической поддержки [Название].

ПРАВИЛА:
1. Точный ответ → давай конкретно
2. Частично релевантно → ответь на часть + укажи пробел
3. Нет ответа → не "не содержится", скажи что уточнишь через [канал]
4. Шаги → нумерованный список
5. Код → всегда code block
6. Не додумывай порты, endpoints, параметры
7. Безопасность/данные → "уточните у менеджера"

ТОНАЛЬНОСТЬ: Профессионально, кратко. 
Не используй "К сожалению" и извинительные обороты.
```

---

## Сравнение источников

| Тема | Perplexity | ChatGPT | DeepSeek | Gemini | Sonnet | Grok | Вывод |
|------|-----------|---------|----------|--------|--------|-------|
| Overlap | 60–80 | 50–100 | 50–75 | 50–75 | 50–75 | **450+120** | **450+120 токенов** |
| Hybrid search | ✅ RRF | ✅ RRF | ✅ RRF | ✅ RRF | ✅ RRF | ✅ 0.7+0.3 | Консенсус всех шести |
| Reranking | Cohere | Cross-encoder | ms-marco | Cohere/BGE | ms-marco | **Cohere Rerank 3.5** | Консенсус всех шести |
| Graceful degradation | ✅ | ✅ | ✅ | ✅ | ✅ Knowledge Tiers | ✅ Таблица порогов | Консенсус |
| Similarity thresholds | — | — | 0.3–0.35 | — | — | **<0.28/0.32/0.45** | Grok — самые конкретные |
| Multilingual embeddings | Voyage-2 | e5-large | e5-large | Cohere v3 | e5-large | **Jina v3/Cohere v3** | Jina v3 или Cohere v3 |
| Knowledge Tiers (таблица) | — | — | — | — | 🔥 | — | FI-031 |
| Prompt versioning в БД | — | — | — | — | 🔥 | — | В FI-007 |
| Question routing | — | — | — | Intent classifier | 🔥 | — | FI-031 |
| Query expansion | — | 🔥 | — | Translation | 🔥 | — | FI-033 |
| Sentence-window | — | — | — | 🔥 | 🔥 | — | В FI-009 |
| HyDE | — | — | — | 🔥 | — | — | FI-036, P2 |
| Observability | ✅ | ✅ | Prometheus | — | ✅ | **Langfuse/Phoenix** | Расширить /chat/debug |
| Namespace per version | — | — | — | — | — | 🔥 | В FI-029 |
| Prompt injection | — | — | 🔥 | — | — | — | FI-035, P2 |

---

---

## Источник 6: Grok

### Что совпадает со всеми пятью
- Overlap обязателен (100–150 токенов, 20–25%).
- Hybrid search BM25 + vector + RRF.
- Reranking.
- Graceful degradation с таблицей порогов.
- Метрики через RAGAS.

### Что добавил Grok (уникальное / более конкретное)

#### 1. Конкретные числа для chunking
- chunk_size = **450 токенов**, chunk_overlap = **120 токенов** (самые конкретные рекомендации из всех источников).
- Separators: `["\n\n", "\n", ". ", " ", ""]` — явный порядок сплиттера.
- Code-aware splitter отдельно для API/код-разделов.

#### 2. Таблица порогов similarity с конкретными числами

| max similarity | Действие |
|---------------|----------|
| < 0.28–0.32 | Полный fallback → human handoff |
| 0.32–0.45 | Слабый ответ + disclaimer + human if critical |
| > 0.45 + неполный | Самоограничение + уточняющий вопрос |

#### 3. Рейтинг моделей для reranking (2025–2026)

| Приоритет | Модель | Комментарий |
|----------|--------|-------------|
| 1 | **Cohere Rerank 3.5 multilingual** | Лучший сейчас |
| 2 | bge-reranker-v2-m3 | Open-source |
| 3 | jina-reranker-v2 | Open-source |
| 4 | flashrank | Очень быстрый/дешёвый |

#### 4. Рейтинг multilingual эмбеддингов (2025–2026)

| Место | Модель | Сила |
|-------|--------|------|
| 1 | **Cohere Embed v3 multilingual** | ★★★★★ |
| 2 | **jina-embeddings-v3/v4** | ★★★★☆ |
| 3 | multilingual-e5-large-instruct | ★★★★☆ |
| 4 | text-embedding-3-large (OpenAI) | ★★★☆☆ |
| 5 | BGE-M3 / Qwen3-Embedding | ★★★★ |

→ Обновляет FI-028: **Jina v3/v4** или **Cohere Embed v3** вместо Voyage-multilingual-2.

#### 5. Weighted sum вместо чистого RRF
- `final_score = 0.7 × vector + 0.3 × BM25` — альтернатива RRF, проще имплементировать.

#### 6. Версионирование через namespace
- Отдельное пространство имён / коллекция на major-версию продукта (api-v1, api-v2).
- Фильтр при поиске: `valid_until > now()` или `version = current_version`.

#### 7. Observability stack конкретный
- **Langfuse / Phoenix / Langsmith** — "без этого в B2B слепой полёт".

#### 8. Рекомендация системного промпта с форматом ответа

```
Структура ответа:
1. Краткий прямой ответ (1–2 предложения)
2. Цитата из документа с [1]
3. Дополнительные шаги / предупреждения
4. Если биллинг/контракт → фраза: "Для точного ответа по вашему аккаунту..."
Markdown для кода, таблиц, списков.
```

---

## Сравнение источников

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
| FI-036 | HyDE для коротких/расплывчатых вопросов | Gemini | P2 |
| FI-009 update | Small-to-Big / Sentence-window + citations | Gemini + Sonnet | P1 |
| FI-007 update | CoT + citations + prompt versioning в БД | Gemini + Sonnet | P1 |
| FI-031 update | Knowledge Tiers таблица + question routing | Sonnet | P1 |
| FI-033 update | Query expansion (несколько перефразировок + RRF) | Sonnet | P2 |
| FI-028 update | Cross-lingual: Cohere/Voyage/e5-large | Все пять | P3 |
| FI-030 | RAG metrics (RAGAS + cost per tenant) | Все пять | P3 |
