# Migration: Fix vector column type + HNSW index — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b migration/fix-vector-column-hnsw
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- New Alembic migration file only: `backend/migrations/versions/XXX_fix_vector_column_and_hnsw_index.py`

**Do NOT touch:**
- `backend/models.py` — already correct
- Any route or service files
- Frontend files
- Other migration files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** The initial migration (`3e6c7b506784_init.py`) created the `vector` column as `postgresql.ARRAY(UUID)` — wrong type. The model (`models.py`) correctly declares `vector = Column(Vector(1536))`, but the actual DB column type is wrong. The pgvector `<=>` operator cannot work on an ARRAY(UUID) column.

Also: `ix_embeddings_vector` is a B-tree index — pgvector ignores it for ANN search.

**Current migration (wrong):**
```python
sa.Column('vector', postgresql.ARRAY(sa.UUID(as_uuid=False)), nullable=True),
...
op.create_index('ix_embeddings_vector', 'embeddings', ['vector'], unique=False)
```

**Vectors were previously stored in `metadata` JSON column** as `{"vector": [0.1, 0.2, ...], "chunk_index": 0}` — backfill must migrate them to the proper `vector(1536)` column.

---

## WHAT TO DO

Create a new Alembic migration:

```bash
cd backend
alembic revision -m "fix_vector_column_and_hnsw_index"
```

Then fill in the generated file:

```python
def upgrade():
    # 1. Ensure pgvector extension is enabled
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Drop the old incorrect B-tree index
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector")

    # 3. Drop the old wrong-type column and recreate as vector(1536)
    op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector")
    op.execute("ALTER TABLE embeddings ADD COLUMN vector vector(1536)")

    # 4. Backfill: migrate vectors from metadata JSON → vector column
    op.execute("""
        UPDATE embeddings
        SET vector = (metadata->'vector')::vector
        WHERE metadata ? 'vector'
          AND metadata->>'vector' IS NOT NULL
          AND vector IS NULL
    """)

    # 5. Create HNSW index for cosine distance
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

## TESTING

Before pushing:
- [ ] `alembic upgrade head` runs without errors on local DB
- [ ] After migration: `SELECT udt_name FROM information_schema.columns WHERE table_name='embeddings' AND column_name='vector'` returns `vector`
- [ ] After migration: `SELECT indexname FROM pg_indexes WHERE tablename='embeddings'` shows `ix_embeddings_vector_hnsw`
- [ ] Backfill check: `SELECT COUNT(*) FROM embeddings WHERE vector IS NOT NULL` > 0 (if data exists)
- [ ] `pytest -q` passes

---

## GIT PUSH

```bash
git add backend/migrations/versions/
git commit -m "migration: fix vector column type to vector(1536), add HNSW index"
git push origin migration/fix-vector-column-hnsw
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- ⚠️ This migration MUST be deployed to Railway BEFORE deploying any code that uses `<=>` vector search
- HNSW params: `m=16` (graph connectivity), `ef_construction=64` (build quality) — good defaults for MVP scale
- If `metadata` JSON has no `vector` key (all embeddings were created after the column existed), backfill UPDATE affects 0 rows — that's fine

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Fixed `vector` column type from `UUID[]` to `vector(1536)` and replaced B-tree index with HNSW for proper pgvector cosine search.

## Changes
- `backend/migrations/versions/XXX_fix_vector_column_and_hnsw_index.py` — new migration

## Testing
- [ ] Migration runs without errors
- [ ] vector column type is vector(1536)
- [ ] HNSW index created
- [ ] Backfill from metadata JSON completed
- [ ] pytest passes

## Notes
⚠️ Must be deployed to Railway BEFORE deploying search refactor code (FI-019).
```
