# FI-115: задержка и стоимость query variants в retrieval

## Цель

Проверить, остаётся ли детерминированное расширение запроса через варианты операционно дешёвым после добавления лишних embedding-операций и дополнительных pgvector-поисков, а также стоит ли продвигать симметричный режим BM25-вариантов сверх текущего асимметричного дефолта.

Этот документ — runbook и шаблон фиксации результатов для прод-ревью. Сам по себе он не меняет поведение retrieval.

## Что измеряем

Основные сравнения:

- `variant_mode=single`
- `variant_mode=multi`
- `bm25_expansion_mode=asymmetric`
- `bm25_expansion_mode=symmetric_variants`

Ключевые метрики:

- p50 / p95 общей задержки trace
- p50 / p95 `retrieval_duration_ms`
- p50 / p95 `query_variant_count`
- p50 / p95 `extra_embedded_queries`
- p50 / p95 `extra_embedding_api_requests`
- p50 / p95 `extra_vector_search_calls`
- p50 / p95 `bm25_query_variant_count`
- p50 / p95 `bm25_variant_eval_count`
- p50 / p95 `extra_bm25_variant_evals`
- p50 / p95 `bm25_merged_hit_count_before_cap`
- p50 / p95 `bm25_merged_hit_count_after_cap`

Поддерживающие сигналы:

- `embedding_api_request_count`
- `query-embedding.duration_ms`
- `vector-search.duration_ms`
- `bm25-search.duration_ms`
- изменения fused ranking на финальном `top_k`
- сэмплы payload `query-expansion.variants` для анализа шумного хвоста
- сэмплы `bm25-search.query_variants` + provenance winner-ов для merge-debug

## Где смотреть

В Langfuse:

- чат-флоу: trace name `rag-query`
- прямой search-флоу: trace name `search-request`

Trace metadata:

- `variant_mode`
- `query_variant_count`
- `extra_embedded_queries`
- `extra_embedding_api_requests`
- `extra_vector_search_calls`
- `bm25_expansion_mode`
- `bm25_query_variant_count`
- `bm25_variant_eval_count`
- `extra_bm25_variant_evals`
- `bm25_merged_hit_count_before_cap`
- `bm25_merged_hit_count_after_cap`
- `retrieval_duration_ms`

Теги:

- `variants:single`
- `variants:multi`

Span outputs:

- `query-expansion`
- `query-embedding`
- `vector-search`
- `bm25-search`
- `rrf-fusion`

## Процедура ревью

1. Выберите стабильное прод-окно с репрезентативным объёмом трафика.
2. Сначала проанализируйте trace `rag-query`, сравнив `single` и `multi`.
3. Отдельно разберите `search-request`, чтобы retrieval-only запросы не искажали чат-латентность.
4. Сначала сравните end-to-end p50/p95, затем p50/p95 по `retrieval_duration_ms`.
5. Сравните `asymmetric` и `symmetric_variants` при одинаковой политике формирования candidate pool, чтобы менялось только лексическое расширение.
6. Просмотрите выборку медленных trace для `multi` и `symmetric_variants`, прочитайте сгенерированные варианты.
7. Классифицируйте дополнительные варианты как полезное расширение recall или как шум нормализации.
8. Проверьте, меняет ли дополнительная лексическая масса fused ranking на пользовательских отсечках (например, финальный `top_k`), а не только «до cap».

## Таблица с доказательствами

Заполните реальными числами из продакшна.

| Flow | Segment | Requests | Total p50 | Total p95 | Retrieval p50 | Retrieval p95 | Avg variants | P95 extra embedded queries | P95 extra vector calls | Notes |
|------|---------|----------|-----------|-----------|---------------|---------------|--------------|----------------------------|------------------------|-------|
| `rag-query` | `single` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `rag-query` | `multi` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `single` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `multi` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |

### Сравнение симметричного BM25

| Flow | BM25 mode | Requests | Retrieval p50 | Retrieval p95 | Avg BM25 variants | P95 extra BM25 evals | Avg merged hits before cap | Avg merged hits after cap | Win/loss queries | Top-k notes |
|------|-----------|----------|---------------|---------------|-------------------|----------------------|----------------------------|---------------------------|------------------|-------------|
| `rag-query` | `asymmetric` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `rag-query` | `symmetric_variants` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `asymmetric` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |
| `search-request` | `symmetric_variants` | TBA | TBA | TBA | TBA | TBA | TBA | TBA | TBA | |

## Чеклист интерпретации

- `multi` должен оправдывать дополнительную работу небольшим приростом латентности, чтобы оставаться операционно дешёвым.
- При высокой вариативности генерации `retrieval_duration_ms` важнее total latency.
- `embedding_api_request_count` обычно должен оставаться стабильным, так как варианты батчатся.
- `extra_embedding_api_requests` обычно должен быть `0`; если растёт, изменилась batching/retry логика, и транспортный overhead уже не плоский.
- `extra_vector_search_calls` — самый прямой усилитель нагрузки на pgvector.
- `bm25_variant_eval_count` — это число повторных lexical scoring проходов по одному shared in-memory candidate corpus, а не второй поиск с повторным сбором корпуса.
- `bm25_merged_hit_count_before_cap` показывает, нашло ли симметричное лексическое расширение больше лексической массы вообще.
- `bm25_merged_hit_count_after_cap` показывает, сколько этой массы реально дошло до RRF.
- Медленные trace `multi` с почти дубликатными вариантами — более сильный аргумент в пользу guardrails, чем медленные trace с явно различающимися формулировками.
- Дополнительные лексические хиты важны только если они улучшают финальный fused ranking на полезных отсечках; само по себе «больше до cap» не оправдывает переключение режима.

## Какие guardrails рассмотреть при необходимости

- cap `max_variants`:
  - первый выбор, если рост p95 явно вызван fan-out
  - самый простой safety-контроль с минимальной продуктовой неоднозначностью

- более агрессивная нормализация / дедупликация:
  - использовать, если много дополнительных вариантов — это шум пунктуации, пробелов или порядка токенов
  - лучше подходит, когда стоимость вызвана низкоценными расширениями, а не реально разными формулировками

- кеширование query embeddings:
  - использовать только если одинаковые нормализованные наборы вариантов часто повторяются в проде
  - усложняет систему, поэтому включать после подтверждения repeat-hit паттернов

## Текущая рекомендация

Текущее поведение пока нельзя считать доказанно дешёвым.

Пока таблица выше не заполнена прод-данными p50/p95, безопасная позиция такая:

- оставить текущую логику включённой для измерений
- оставить `bm25_expansion_mode=asymmetric` режимом по умолчанию
- не добавлять guardrails превентивно
- если первое прод-ревью покажет заметный рост p95 для `multi`, сначала внедрить cap `max_variants`
- не продвигать `symmetric_variants`, пока режим не выигрывает на репрезентативных фикстурах, не улучшает fused top-k достаточно часто и не делает это с приемлемой задержкой без регрессий контрольных кейсов

Итого: guardrail первого приоритета — `max_variants`, затем эвристики нормализации, затем кеш embedding-ов.
