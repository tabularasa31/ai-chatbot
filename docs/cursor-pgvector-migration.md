# Migration: pgvector vector column + HNSW index

## Проблема

Текущая миграция (`3e6c7b506784_init.py`) создаёт колонку `vector` как `postgresql.ARRAY(UUID)` — это неправильно:

```python
sa.Column('vector', postgresql.ARRAY(sa.UUID(as_uuid=False)), nullable=True),
```

Модель `Embedding` в `backend/models.py` объявляет `vector = Column(Vector(1536), nullable=True)` — правильно.

Значит в реальной БД колонка либо неправильного типа, либо отсутствует как `vector(1536)`.

Также нет HNSW индекса — есть только `ix_embeddings_vector` как обычный B-tree (который pgvector игнорирует).

---

## Что нужно сделать

Создать новую Alembic миграцию:

```bash
alembic revision -m "fix_vector_column_type_and_add_hnsw_index"
```

### upgrade():

```python
def upgrade():
    # 1. Убедиться что pgvector extension включен
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Удалить старый неправильный индекс
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector")

    # 3. Пересоздать колонку vector правильного типа
    # Сначала удалить старую (ARRAY UUID), создать новую (vector(1536))
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector")
    op.execute("ALTER TABLE embeddings ADD COLUMN vector vector(1536)")

    # 4. Бэкфилл: перенести векторы из metadata JSON → vector колонку
    # Данные хранились в metadata_json как {"vector": [0.1, 0.2, ...]}
    op.execute("""
        UPDATE embeddings
        SET vector = (metadata->'vector')::vector
        WHERE metadata ? 'vector'
          AND metadata->>'vector' IS NOT NULL
          AND vector IS NULL
    """)

    # 5. Создать HNSW индекс для cosine distance
    op.execute("""
        CREATE INDEX ix_embeddings_vector_hnsw
        ON embeddings
        USING hnsw (vector vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector_hnsw")
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector")
    op.execute("ALTER TABLE embeddings ADD COLUMN vector UUID[]")
    op.create_index('ix_embeddings_vector', 'embeddings', ['vector'], unique=False)
```

---

## Порядок деплоя (важно!)

1. Создать PR с миграцией
2. **Сначала запустить миграцию на Railway:** `alembic upgrade head`
3. Только потом деплоить новый код поиска (FI-019, FI-019 ext)

Если запустить новый код до миграции — `<=>` упадёт т.к. колонка неправильного типа.

---

## Проверка после миграции

```sql
-- Проверить тип колонки
SELECT column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_name = 'embeddings' AND column_name = 'vector';
-- Ожидаем: udt_name = 'vector'

-- Проверить индекс
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'embeddings';
-- Ожидаем: ix_embeddings_vector_hnsw с USING hnsw

-- Проверить бэкфилл
SELECT COUNT(*) FROM embeddings WHERE vector IS NOT NULL;
SELECT COUNT(*) FROM embeddings WHERE vector IS NULL;
```

---

## Файлы для изменения

1. **Новая миграция** — `backend/migrations/versions/XXX_fix_vector_column_type_and_add_hnsw_index.py`

Модель (`backend/models.py`) уже правильная — не трогать.
