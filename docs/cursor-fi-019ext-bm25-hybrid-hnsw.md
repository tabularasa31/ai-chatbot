# Feature: [FI-019 ext] BM25 hybrid search + HNSW index

## Контекст

Текущий поиск в `backend/search/service.py`:
- Vector search: pgvector `<=>` cosine distance ✅
- Keyword fallback: простой Python подсчёт вхождений слов ❌ (медленно, неточно)
- Индекс: после FI-019 будет `ivfflat` → в этой задаче заменяем на `hnsw`

Цель: заменить keyword fallback на BM25, объединить с vector search в true hybrid (RRF).

---

## Что такое BM25 и RRF

**BM25** — отраслевой стандарт full-text search (используется в Elasticsearch, Lucene).
Лучше простого keyword match: учитывает частоту термина и длину документа.

**RRF (Reciprocal Rank Fusion)** — алгоритм объединения результатов из разных источников:
```
score(d) = Σ 1 / (k + rank_in_source)   # k=60 стандарт
```
Не требует нормализации скоров — просто берёт ранги.

---

## Что нужно сделать

### 1. Заменить `ivfflat` на `hnsw` — новая Alembic миграция

```python
def upgrade():
    op.drop_index('ix_embeddings_vector_ivfflat', table_name='embeddings')
    op.execute("""
        CREATE INDEX ix_embeddings_vector_hnsw
        ON embeddings
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

def downgrade():
    op.drop_index('ix_embeddings_vector_hnsw', table_name='embeddings')
    op.execute("""
        CREATE INDEX ix_embeddings_vector_ivfflat
        ON embeddings
        USING ivfflat (vector vector_cosine_ops)
        WITH (lists = 100)
    """)
```

> HNSW параметры для MVP: `m=16` (связность графа), `ef_construction=64` (качество построения).
> Быстрее ivfflat при поиске, не требует предварительного знания размера датасета.

### 2. Добавить BM25 через `rank_bm25` библиотеку

Установить: `pip install rank-bm25` → добавить в `requirements.txt`

```python
from rank_bm25 import BM25Okapi

def bm25_search_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    BM25 full-text search over chunk_text.
    Replaces keyword_search_chunks().
    
    Returns list of (embedding, normalized_score) sorted DESC.
    """
    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .filter(Embedding.chunk_text.isnot(None))
        .all()
    )
    
    if not embeddings:
        return []
    
    # Tokenize corpus
    corpus = [(emb.chunk_text or "").lower().split() for emb in embeddings]
    bm25 = BM25Okapi(corpus)
    
    # Score query
    query_tokens = query.lower().split()
    scores = bm25.get_scores(query_tokens)
    
    # Normalize to [0, 1]
    max_score = max(scores) if max(scores) > 0 else 1.0
    normalized = scores / max_score
    
    # Pair with embeddings, filter zeros, sort
    scored = [
        (emb, float(score))
        for emb, score in zip(embeddings, normalized)
        if score > 0
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
```

### 3. Добавить RRF fusion

```python
def reciprocal_rank_fusion(
    vector_results: list[tuple[Embedding, float]],
    bm25_results: list[tuple[Embedding, float]],
    k: int = 60,
    top_k: int = 5,
) -> list[tuple[Embedding, float]]:
    """
    Combine vector and BM25 results using Reciprocal Rank Fusion.
    
    Args:
        vector_results: (embedding, similarity) sorted by similarity DESC
        bm25_results: (embedding, bm25_score) sorted by score DESC
        k: RRF constant (60 is standard)
        top_k: Number of results to return
    
    Returns:
        Merged and re-ranked list of (embedding, rrf_score)
    """
    scores: dict[uuid.UUID, float] = {}
    id_to_emb: dict[uuid.UUID, Embedding] = {}
    
    for rank, (emb, _) in enumerate(vector_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb
    
    for rank, (emb, _) in enumerate(bm25_results):
        scores[emb.id] = scores.get(emb.id, 0) + 1 / (k + rank + 1)
        id_to_emb[emb.id] = emb
    
    sorted_ids = sorted(scores.keys(), key=lambda id_: scores[id_], reverse=True)
    return [(id_to_emb[id_], scores[id_]) for id_ in sorted_ids[:top_k]]
```

### 4. Обновить `search_similar_chunks`

```python
def search_similar_chunks(...) -> list[tuple[Embedding, float]]:
    query_vector = embed_query(query, api_key=api_key)
    
    # SQLite fallback (tests)
    if "sqlite" in str(db.bind.url if db.bind else ""):
        return _python_cosine_search(client_id, query_vector, top_k, db)
    
    # 1. Vector search (pgvector hnsw)
    vector_results = _pgvector_search(client_id, query_vector, top_k * 2, db)
    
    # 2. BM25 search
    bm25_results = bm25_search_chunks(client_id, query, top_k * 2, db)
    
    # 3. If both empty → return empty
    if not vector_results and not bm25_results:
        return []
    
    # 4. If only one source has results → return it
    if not bm25_results:
        return vector_results[:top_k]
    if not vector_results:
        return bm25_results[:top_k]
    
    # 5. Hybrid: RRF fusion
    return reciprocal_rank_fusion(vector_results, bm25_results, top_k=top_k)
```

Вынести pgvector запрос в отдельную `_pgvector_search()` функцию (рефакторинг текущего кода).

### 5. Удалить `keyword_search_chunks`

Заменена на `bm25_search_chunks`. Убедиться что нигде не импортируется.

---

## Файлы для изменения

1. **Новая миграция** — `backend/migrations/versions/XXX_replace_ivfflat_with_hnsw.py`
2. **`requirements.txt`** — добавить `rank-bm25`
3. **`backend/search/service.py`**:
   - добавить `bm25_search_chunks()`
   - добавить `reciprocal_rank_fusion()`
   - добавить `_pgvector_search()` (рефакторинг)
   - обновить `search_similar_chunks()` — hybrid pipeline
   - удалить `keyword_search_chunks()`
   - удалить `VECTOR_CONFIDENCE_THRESHOLD` и `DISTANCE_THRESHOLD` — больше не нужны

---

## Текущий код keyword_search_chunks (заменяем):

```python
def keyword_search_chunks(client_id, query, top_k, db):
    keywords = _extract_keywords(query)
    embeddings = db.query(Embedding).join(Document).filter(Document.client_id == client_id).all()
    scored = []
    for emb in embeddings:
        chunk_lower = (emb.chunk_text or "").lower()
        count = sum(1 for kw in keywords if kw in chunk_lower)
        if count > 0:
            scored.append((emb, float(count)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
```

---

## Важно

- `rank-bm25` — pure Python, нет системных зависимостей
- BM25 строится in-memory на каждый запрос — окей для MVP (сотни чанков), при тысячах+ рассмотреть кэш
- HNSW индекс строится дольше чем ivfflat, но поиск быстрее и не требует знать размер датасета заранее
- RRF не требует нормализации скоров из разных источников — это его главное преимущество
