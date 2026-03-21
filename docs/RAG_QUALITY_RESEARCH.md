# RAG Quality Research

Results of surveying other AI models on improving RAG quality.
Sources: Perplexity + ChatGPT, 2026-03-18.

---

## 1. Chunking for technical docs

- Overlap **required**: 256–512 tokens, overlap 10–20% (50–100 tokens for 500-token chunk).
- Structural approach for technical docs:
  - First split by structure (h2/h3, endpoints, parameter tables).
  - Inside large sections — token-based chunking 300–500 tokens with overlap.
  - Don't break code blocks and request examples.
- Starter recipe:
  - 384–512 tokens, 60–80 tokens overlap.
  - Smaller chunks (200–300) for FAQ/errors.

**Prod baseline (2026-03, FI-009):** sentence-aware chunking + метаданные позиции/файла в `embeddings.metadata`. **Следующий шаг (FI-009+):** overlap в токенах (60–80) + структурный сплит по заголовкам/коду.

---

## 2. Graceful degradation (when info is missing)

Three situation classes:

1. **Not in docs, but typical support question** → store in org config:
   - support_email, support_portal_url, sales_contact, trial_length_days.
   - Always pass to system prompt (not via RAG).

2. **Nowhere** → strict policy in system prompt:
   - If cosine similarity < 0.3–0.35 → bot must say no data and suggest contact.
   - Threshold can be dynamic + consider gap between 1st and 2nd result.

3. **Escalation patterns:**
   - RAG → low-confidence → template "Unfortunately, there's no answer in the documentation. Please contact..."
   - FAQ layer over RAG for "obvious" questions (email, top-level pricing).
   - System prompt: "If info is missing — don't invent, direct to [contacts]."

---

## 3. Reranking and hybrid search

- Rerank gives +20–30% to top result quality.
- Scheme:
  - Stage 1: pgvector + BM25 → top 30/50 chunks.
  - Stage 2: cross-encoder (MS MARCO) → reorder → top 3–5.
- Hybrid search (BM25 + vector):
  - BM25 → exact matches (endpoints, error codes, flags).
  - Vector → paraphrases and semantics.
- Recommendation: feed 6–10 chunks to prompt, ask to cite only clearly relevant ones.

**FI-019 extend:** add BM25 (Postgres full-text) + optional cross-encoder rerank.

---

## 4. Document versioning

- Explicit versioning: `version`, `updated_at`, `valid_from`, `valid_until`, `is_current`.
- Metadata flows into chunks.
- Queries filter by `is_current=true`.
- Recency scoring: `final_score = semantic_score * (0.7 + 0.3 * recency_score)`.
- On doc update — new version + old marked `is_current=false`.
- For test URLs: move base URLs to config/table, don't store in docs.
- In docs write "Updated: January 2026" — helps LLM interpret.

**New feature:** FI-029 Document versioning & recency scoring.

---

## 5. System prompt for technical docs

Critical elements:

1. **Truthfulness and escalation policy:**
   - "If info is not in docs or config — don't invent. Direct to: support_email, support_portal_url, account_manager."

2. **Source grounding:**
   - "Answer only from the provided context. If context is contradictory — describe it and suggest clarifying with support."

3. **Language and style:**
   - Concise, no marketing, with API request examples, code blocks.
   - "Answer in the question's language."

4. **Multi-chunk handling:**
   - "Combine info from multiple fragments. If there are contradictions — state them explicitly."

5. **Fantasy limit:**
   - "Don't invent endpoints, parameters, pricing. If not found — say so and direct to documentation or support."

**FI-007 clarify:** include all these elements in per-client system prompt.

---

## 6. Cross-lingual retrieval

- `text-embedding-3-small` is OK, but for production multilingual RAG better:
  - **Voyage-multilingual-2** (SaaS) — strong cross-lingual retrieval.
  - **e5-mistral** (open-source) — modern multilingual option.
- Practice:
  - One multilingual embedding space for all tenants.
  - In system prompt explicitly: "Answer in user's language, even if context is in another language."
  - For critical clients: store 2 chunk sets (original + machine translation to EN).

**FI-028 update:** add Voyage-multilingual-2 as primary candidate.

---

## 7. RAG quality metrics

**Retrieval:**
- Contextual relevancy@k: share of queries where top-k contains the correct fragment.
- Precision@k: share of relevant chunks in top-k.

**Generation:**
- Answer faithfulness (RAGAS) — how much the answer relies on context, doesn't hallucinate.
- Answer relevancy — how well the answer addresses the question.
- Baseline: manual annotation of 100–200 real tickets.

**Product:**
- CSAT on bot answers.
- Escalation rate: share of conversations handed to human.
- Single-turn resolution rate: how many questions resolved without follow-ups.

**Tools:** RAGAS, Giskard.

---

## 8. Architectural recommendations for B2B

1. **Separate knowledge layers:**
   - Org config (support email, pricing, domains, limits) → always in system prompt.
   - Product docs → RAG.
   - Operational data (incident statuses, account limits) → direct API/DB queries.

2. **Retrieval stack:**
   - pgvector + BM25 hybrid.
   - Optional cross-encoder rerank for complex queries.
   - Strict isolation by tenant_id.

3. **Models:**
   - Multilingual embeddings (Voyage-multilingual-2 / e5-mistral).
   - Mixed generation mode: gpt-4o-mini by default, stronger model on low-confidence.

4. **Per-tenant settings:**
   - Document and answer language.
   - Escalation channels.
   - Strictness of "don't invent" policy.

---

---

## Source 2: ChatGPT

### What matches Perplexity
- Overlap 50–100 токенов — оба согласны.
- Hybrid search BM25 + vector — оба согласны.
- Reranking через cross-encoder или Cohere — оба согласны.
- Graceful degradation с 3 уровнями — оба согласны.
- Метрики: Faithfulness, Recall@k, product thumbs up/down — оба согласны.

### What ChatGPT added (new)

#### 1. LLM-based answer validation ✅ Shipped (FI-034, 2026-03-21)
After generating the answer, a second `gpt-4o-mini` call checks grounding in retrieved chunks; low-confidence invalid answers are replaced with a fixed fallback; failures in that step are non-blocking. See `backend/chat/service.py` (`validate_answer`) and `docs/PROGRESS.md`.

#### 2. Query rewriting layer
Before retrieval — normalize and improve the question:
- fix phrasing,
- normalize terms,
- add context.

Effect: same question essence with different phrasings → equally good answers.

**This solves our problem #4** (quality depends on phrasing).

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

**Это следующий шаг после базового FI-009** (sentence chunks уже в проде) — Small-to-Big / parent context, стоит изучить.

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
| Sentence-window | — | — | — | 🔥 | 🔥 | — | Baseline в FI-009; расширить Small-to-Big |
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
| FI-009+ | Chunking: токеновый overlap 60–80 + структурный сплит | Все три | P1 |
| FI-007 update | System prompt: 5 элементов + fallback-фразы | Все три | P1 |
| FI-019 update | Hybrid search: BM25 + vector + rerank + HNSW | Все три | P1 |
| FI-031 | Org config / tool layer (support_email вне RAG) | Все три | P1 |
| FI-033 | Query rewriting перед retrieval | ChatGPT | P2 |
| FI-034 | LLM-based answer validation (post-generation) ✅ done | ChatGPT | ~~P2~~ |
| FI-029 | Document versioning & recency scoring | Perplexity + DeepSeek | P2 |
| FI-035 | Security: prompt injection protection | DeepSeek | P2 |
| FI-036 | HyDE для коротких/расплывчатых вопросов | Gemini | P2 |
| FI-009+ | Small-to-Big / расширенный sentence-window + citations | Gemini + Sonnet | P1 |
| FI-007 update | CoT + citations + prompt versioning в БД | Gemini + Sonnet | P1 |
| FI-031 update | Knowledge Tiers таблица + question routing | Sonnet | P1 |
| FI-033 update | Query expansion (несколько перефразировок + RRF) | Sonnet | P2 |
| FI-028 update | Cross-lingual: Cohere/Voyage/e5-large | Все пять | P3 |
| FI-030 | RAG metrics (RAGAS + cost per tenant) | Все пять | P3 |
