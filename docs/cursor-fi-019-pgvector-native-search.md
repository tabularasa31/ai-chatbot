# Feature: [FI-019] pgvector <=> native search — cleanup & index

## Статус

`<=>` (cosine_distance) уже используется в PostgreSQL пути в `backend/search/service.py`.
Но есть незавершённые части:

1. **Индекс на vector — неправильный тип** — создан как обычный B-tree индекс вместо `ivfflat`
2. **`cosine_similarity` Python функция** — висит в коде как "backward compat", но нигде не используется активно
3. **`_python_cosine_search`** — дублирует логику, нужно убедиться что используется только в SQLite/тестах

---

## Что нужно сделать

### 1. Заменить индекс на vector — новая Alembic миграция

Текущий индекс (в `migrations/versions/3e6c7b506784_init.py`):
```python
op.create_index('ix_embeddings_vector', 'embeddings', ['vector'], unique=False)
```

Это B-tree индекс — pgvector его игнорирует при `<=>` поиске. Нужен `ivfflat`.

Создать новую миграцию `alembic revision -m "replace_vector_index_with_ivfflat"`:

```python
def upgrade():
    # Drop old B-tree index on vector
    op.drop_index('ix_embeddings_vector', table_name='embeddings')
    
    # Create ivfflat index for cosine distance search
    # lists=100 — стандарт для таблиц до ~1M строк
    op.execute("""
        CREATE INDEX ix_embeddings_vector_ivfflat
        ON embeddings
        USING ivfflat (vector vector_cosine_ops)
        WITH (lists = 100)
    """)

def downgrade():
    op.drop_index('ix_embeddings_vector_ivfflat', table_name='embeddings')
    op.create_index('ix_embeddings_vector', 'embeddings', ['vector'], unique=False)
```

> **Важно:** `ivfflat` требует что в таблице уже есть данные перед созданием индекса (иначе lists=100 бессмысленно). Для пустой таблицы — ок, просто менее оптимален.

### 2. Удалить `cosine_similarity` из `backend/search/service.py`

Функция помечена как "backward compat" но нигде реально не нужна:

```python
# УДАЛИТЬ:
def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Kept for backward compatibility. Prefer pgvector native search."""
    ...
```

Проверить что нигде не импортируется: `grep -r "cosine_similarity" backend/`

### 3. Добавить комментарий в `_python_cosine_search`

Оставить функцию (нужна для тестов с SQLite), но явно пометить:

```python
def _python_cosine_search(...):
    """
    SQLite/test fallback ONLY. NOT used in production.
    Production uses pgvector native <=> operator via search_similar_chunks().
    """
```

---

## Файлы для изменения

1. **Новая миграция** — `backend/migrations/versions/XXX_replace_vector_index_with_ivfflat.py`
2. **`backend/search/service.py`**:
   - удалить `cosine_similarity()`
   - обновить docstring `_python_cosine_search`

---

## Текущее состояние для reference

```python
# search/service.py — PostgreSQL путь (уже правильный)
distance_expr = Embedding.vector.cosine_distance(query_vector)
results_with_distance = (
    db.query(Embedding, distance_expr.label("distance"))
    .join(Document, Embedding.document_id == Document.id)
    .filter(Document.client_id == client_id)
    .filter(Embedding.vector.isnot(None))
    .order_by(distance_expr)
    .limit(top_k)
    .all()
)
# Convert distance → similarity
results = [(emb, max(0.0, 1.0 - distance)) for emb, distance in results_with_distance]

# Текущий индекс (неправильный тип):
op.create_index('ix_embeddings_vector', 'embeddings', ['vector'], unique=False)
# ↑ B-tree, pgvector его не использует для ANN поиска
```

---

## Почему ivfflat, не hnsw

- `ivfflat` — проще, меньше памяти, достаточно для MVP (тысячи/десятки тысяч векторов)
- `hnsw` — быстрее при поиске, но дороже по памяти и времени построения
- Для Chat9 MVP: `ivfflat` с `lists=100` оптимален
