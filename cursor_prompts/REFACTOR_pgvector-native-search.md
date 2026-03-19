# REFACTOR: Native pgvector Search (Replace Python Cosine Similarity)

⚠️ **CRITICAL: Follow SETUP exactly. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/refactor-pgvector-native-search
```

**MUST DO (in exact order):**
1. `git checkout main`
2. `git pull origin main` — DO NOT SKIP
3. `git checkout -b feature/refactor-pgvector-native-search` — NEW branch

**DO NOT reuse old branches.**

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add proper Vector column to Embedding
- `backend/search/service.py` — replace Python cosine with pgvector SQL query
- `backend/chat/service.py` — update `retrieve_context()` to use new search
- `backend/embeddings/service.py` — write vector to `vector` column (not only metadata_json)
- `requirements.txt` (or `pyproject.toml`) — add `pgvector` package

**Do NOT touch:**
- Auth, middleware, auth routes
- Other models (User, Client, Document, Chat, Message)
- Alembic migrations (leave for separate PR — see DEPLOYMENT ORDER below)
- Frontend
- Any other backend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

### Current Problem (CRITICAL for scalability)

Right now, vector search works like this:

```python
# search/service.py — CURRENT (slow, doesn't scale)
embeddings = db.query(Embedding).join(Document)
    .filter(Document.client_id == client_id)
    .all()  # ← Loads ALL embeddings into memory!

for emb in embeddings:
    meta = emb.metadata_json or {}
    vector = meta.get("vector")      # ← Vectors stored in JSON!
    sim = cosine_similarity(query_vector, vector)  # ← Python math loop!
```

**Why this is a problem:**
- 100 docs × 20 chunks = 2000 embeddings loaded into memory per query
- Python cosine loop = slow (should be microseconds in SQL, not milliseconds in Python)
- Vectors stored in `metadata_json["vector"]` — not in a proper column
- Won't scale past ~50 documents per client

### Also in models.py (wrong type):

```python
# Current — WRONG
vector = Column(
    ARRAY(PG_UUID(as_uuid=False)),  # UUID array?? Makes no sense for floats
    nullable=True,
)
```

### Target State (pgvector native)

```python
# After fix — CORRECT
from pgvector.sqlalchemy import Vector

vector = Column(Vector(1536), nullable=True)  # 1536-dim for text-embedding-3-small
```

```python
# After fix — Fast SQL search
from pgvector.sqlalchemy import Vector
from sqlalchemy import func

results = (
    distance_expr = Embedding.vector.cosine_distance(query_vector)
        db.query(Embedding, distance_expr.label("distance"))
    .join(Document, Embedding.document_id == Document.id)
    .filter(Document.client_id == client_id)      # ← Filter at DB level
    .filter(Embedding.vector.isnot(None))          # ← Skip null vectors
    .order_by("distance")                          # ← Sort by distance (ascending)
    .limit(top_k)                                  # ← Only fetch top_k
    .all()
)
# cosine_distance = 1 - cosine_similarity, so ascending = most similar
```

### Why this is better:
- No Python loop → 100x faster
- Only top_k rows fetched (not all) → 100x less memory
- client_id filtered at DB level → data isolation guaranteed
- pgvector uses HNSW/IVFFlat index → O(log N) not O(N)

---

## WHAT TO DO

### Step 1: Add pgvector to dependencies

**File: `requirements.txt`** (or `pyproject.toml` if used)

Add:
```
pgvector>=0.2.0
```

---

### Step 2: Update Embedding model

**File: `backend/models.py`**

**Current (at the top, imports):**
```python
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
```

**Add** to imports:
```python
from pgvector.sqlalchemy import Vector
```

**Find the Embedding class and update the vector column:**

**Before:**
```python
class Embedding(Base):
    __tablename__ = "embeddings"
    
    # ...
    
    # В бою это должен быть pgvector(1536); для миграций/тестов тип уточняется отдельно.
    vector = Column(
        ARRAY(PG_UUID(as_uuid=False)),
        nullable=True,
    )
```

**After:**
```python
class Embedding(Base):
    __tablename__ = "embeddings"
    
    # ...
    
    # Vector column: 1536 dimensions for text-embedding-3-small
    # Uses pgvector extension. Falls back to JSON in SQLite (tests).
    vector = Column(
        Vector(1536),
        nullable=True,
    )
```

**Also update the SQLite compat function** to handle Vector type:

```python
from pgvector.sqlalchemy import Vector

@compiles(Vector, "sqlite")
def compile_vector_sqlite(type_, compiler, **kw) -> str:
    return "TEXT"  # Store as text in SQLite (tests only)
```

**Also remove the wrong index at the bottom** and add proper one:

**Before (at bottom of models.py):**
```python
Index(
    "ix_embeddings_vector",
    Embedding.vector,
)
```

**After:**
```python
# Note: pgvector index is created via migration, not here
# CREATE INDEX ON embeddings USING hnsw (vector vector_cosine_ops);
# Leaving simple index for now:
Index(
    "ix_embeddings_document_id",
    Embedding.document_id,
)
```

---

### Step 3: Update embeddings/service.py — Write Vector to Column

**File: `backend/embeddings/service.py`**

Find where `Embedding` objects are created (look for `Embedding(...)` with `vector=None`).

**Before (likely):**
```python
emb = Embedding(
    document_id=document_id,
    chunk_text=chunk,
    vector=None,  # ← not stored in column!
    metadata_json={"chunk_index": i, "vector": vector_list},  # ← stored in JSON
)
```

**After:**
```python
emb = Embedding(
    document_id=document_id,
    chunk_text=chunk,
    vector=vector_list,  # ← write to proper column
    metadata_json={"chunk_index": i},  # ← keep chunk_index, remove vector from JSON
)
```

**Why this matters:** Without this change, the `vector` column will always be `NULL` and pgvector search will return empty results. New embeddings must write to the `vector` column.

---

### Step 4: Rewrite search/service.py

**File: `backend/search/service.py`**

Replace the entire file content with:

```python
"""Business logic for vector similarity search."""

from __future__ import annotations

import re
import uuid
from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import Document, Embedding

VECTOR_CONFIDENCE_THRESHOLD = 0.70  # cosine_similarity >= 0.70 → use vector mode
# Note: pgvector returns cosine_distance (1 - similarity), so threshold = 1 - 0.70 = 0.30
DISTANCE_THRESHOLD = 1.0 - VECTOR_CONFIDENCE_THRESHOLD  # 0.30

MIN_KEYWORD_LEN = 3
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def embed_query(query: str, *, api_key: str) -> list[float]:
    """
    Embed a search query using OpenAI embeddings API.

    Args:
        query: Text to embed.
        api_key: OpenAI API key.

    Returns:
        1536-dimensional embedding vector.
    """
    openai_client = get_openai_client(api_key)
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    return response.data[0].embedding


def _extract_keywords(query: str) -> list[str]:
    """Extract simple keywords: split by whitespace/punctuation, lowercase, filter short tokens."""
    tokens = re.split(r"\W+", query.lower())
    return [t for t in tokens if len(t) >= MIN_KEYWORD_LEN]


def keyword_search_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    Keyword-based fallback search over chunk_text.

    Loads all chunks for client, counts keyword matches in Python.
    Used as fallback when vector search confidence is low.

    Args:
        client_id: Client ID for tenant isolation (filter at DB level).
        query: Search query text.
        top_k: Number of top results to return.
        db: Database session.

    Returns:
        List of (embedding, match_count) tuples, sorted by match_count DESC.
    """
    keywords = _extract_keywords(query)
    if not keywords:
        return []

    # Load all chunks for this client (filtering at DB level)
    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)  # ← Mandatory client filter
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        chunk_lower = (emb.chunk_text or "").lower()
        count = sum(1 for kw in keywords if kw in chunk_lower)
        if count > 0:
            scored.append((emb, float(count)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def search_similar_chunks(
    client_id: uuid.UUID,
    query: str,
    top_k: int,
    db: Session,
    *,
    api_key: str,
) -> list[tuple[Embedding, float]]:
    """
    Search for similar chunks using native pgvector cosine distance.

    Uses pgvector <=> operator for fast vector search at DB level.
    Falls back to keyword search if no confident vector results found.

    Args:
        client_id: Client ID (mandatory filter for tenant isolation).
        query: Search query text.
        top_k: Number of top results to return.
        db: Database session.
        api_key: OpenAI API key.

    Returns:
        List of (embedding, similarity) tuples, sorted by similarity DESC.
    """
    query_vector = embed_query(query, api_key=api_key)

    # Check if we're in SQLite (tests) — pgvector not available
    db_url = str(db.bind.url) if db.bind else ""
    if "sqlite" in db_url:
        return _python_cosine_search(client_id, query_vector, top_k, db)

    # pgvector native search (PostgreSQL)
    try:
        results_with_distance = (
            distance_expr = Embedding.vector.cosine_distance(query_vector)
        db.query(Embedding, distance_expr.label("distance"))
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.client_id == client_id)  # ← Mandatory client filter
            .filter(Embedding.vector.isnot(None))      # ← Skip null vectors
            .order_by(distance_expr)                       # ← Use expression not string (SQLAlchemy 2.0)
            .limit(top_k)
            .all()
        )
    except Exception:
        # Fallback to Python search if pgvector fails (e.g., extension not installed)
        return _python_cosine_search(client_id, query_vector, top_k, db)

    if not results_with_distance:
        # No vector results → try keyword search
        return keyword_search_chunks(client_id, query, top_k, db)

    # Convert distance to similarity (cosine_distance = 1 - cosine_similarity)
    results: list[tuple[Embedding, float]] = [
        (emb, max(0.0, 1.0 - distance))
        for emb, distance in results_with_distance
    ]

    # Check confidence: if best result is not confident, use keyword fallback
    best_similarity = results[0][1] if results else 0.0
    if best_similarity < VECTOR_CONFIDENCE_THRESHOLD:
        keyword_results = keyword_search_chunks(client_id, query, top_k, db)
        return keyword_results if keyword_results else results

    return results


def _python_cosine_search(
    client_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """
    Fallback: Python-based cosine similarity search.

    Used for SQLite (tests) or when pgvector is not available.
    Not recommended for production with large datasets.

    Args:
        client_id: Client ID for filtering.
        query_vector: Pre-computed query embedding.
        top_k: Number of results.
        db: Database session.
    """
    import math

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        # Try Vector column first, fall back to metadata_json["vector"]
        vector = None
        if emb.vector is not None:
            vector = list(emb.vector)
        else:
            meta = emb.metadata_json or {}
            vector = meta.get("vector")

        if not vector or not isinstance(vector, list):
            continue

        # Cosine similarity
        if len(vector) != len(query_vector):
            continue
        dot = sum(a * b for a, b in zip(query_vector, vector))
        norm1 = math.sqrt(sum(a * a for a in query_vector))
        norm2 = math.sqrt(sum(b * b for b in vector))
        if norm1 == 0 or norm2 == 0:
            continue
        sim = max(0.0, min(1.0, dot / (norm1 * norm2)))
        scored.append((emb, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# Keep for backward compatibility (used in chat/service.py)
def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.
    Kept for backward compatibility. Prefer pgvector native search.
    """
    import math
    if len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm1 * norm2)))
```

---

### Step 5: Update retrieve_context in chat/service.py

**File: `backend/chat/service.py`**

The `retrieve_context()` function currently uses `cosine_similarity` directly. Update it to use `search_similar_chunks`:

**Find `retrieve_context` function (around line 40-80):**

**Before:**
```python
def retrieve_context(
    client_id: uuid.UUID,
    question: str,
    db: Session,
    api_key: str,
    top_k: int = 5,
) -> tuple[list[str], list[uuid.UUID], list[float], Literal["vector", "keyword", "none"]]:
    query_vector = embed_query(question, api_key=api_key)

    embeddings = (
        db.query(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.client_id == client_id)
        .all()
    )

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        meta = emb.metadata_json or {}
        vector = meta.get("vector")
        if not vector or not isinstance(vector, list):
            continue
        sim = cosine_similarity(query_vector, vector)
        scored.append((emb, sim))

    scored.sort(key=lambda x: x[1], reverse=True)

    if scored and scored[0][1] >= VECTOR_CONFIDENCE_THRESHOLD:
        results = scored[:top_k]
        mode: Literal["vector", "keyword", "none"] = "vector"
    else:
        keyword_results = keyword_search_chunks(client_id, question, top_k, db)
        if keyword_results:
            results = keyword_results
            mode = "keyword"
        else:
            results = []
            mode = "none"

    chunk_texts = [r[0].chunk_text or "" for r in results]
    document_ids = [r[0].document_id for r in results]
    scores = [r[1] for r in results]

    return (chunk_texts, document_ids, scores, mode)
```

**After:**
```python
def retrieve_context(
    client_id: uuid.UUID,
    question: str,
    db: Session,
    api_key: str,
    top_k: int = 5,
) -> tuple[list[str], list[uuid.UUID], list[float], Literal["vector", "keyword", "none"]]:
    """
    Retrieve context chunks for RAG using pgvector native search.

    Uses search_similar_chunks which handles pgvector natively (or falls back to Python for SQLite).
    client_id filtering enforced at DB level.
    """
    results = search_similar_chunks(
        client_id=client_id,
        query=question,
        top_k=top_k,
        db=db,
        api_key=api_key,
    )

    if not results:
        return ([], [], [], "none")

    # Determine mode from scores.
    # IMPORTANT: vector similarity scores are in [0.0, 1.0]
    # keyword match scores are integers (1, 2, 3...) — count of matching keywords
    # So: if best_score >= VECTOR_CONFIDENCE_THRESHOLD (0.70) → vector mode
    #     if best_score is a small float < threshold → keyword fallback was used
    best_score = results[0][1]
    if best_score >= VECTOR_CONFIDENCE_THRESHOLD:
        mode: Literal["vector", "keyword", "none"] = "vector"
    else:
        # Could be keyword (integer counts) or weak vector results
        mode = "keyword"

    chunk_texts = [r[0].chunk_text or "" for r in results]
    document_ids = [r[0].document_id for r in results]
    scores = [r[1] for r in results]

    return (chunk_texts, document_ids, scores, mode)
```

**Also update the import in chat/service.py** — remove the old imports that are no longer needed:

**Before:**
```python
from backend.search.service import (
    VECTOR_CONFIDENCE_THRESHOLD,
    cosine_similarity,
    embed_query,
    keyword_search_chunks,
)
```

**After:**
```python
from backend.search.service import (
    VECTOR_CONFIDENCE_THRESHOLD,
    search_similar_chunks,
)
```

---

## TESTING

Before pushing, verify:

- [ ] `pip install pgvector` works without errors
- [ ] Backend starts without import errors
- [ ] `GET /health` returns 200
- [ ] `/search` endpoint returns results (try with real query and API key)
- [ ] `/chat` endpoint returns answers
- [ ] No `AttributeError` on `Embedding.vector`
- [ ] SQLite tests still pass (`pytest tests/ -x`)
- [ ] `grep -rn "metadata_json.*vector" backend/` → search/service.py should return nothing (old JSON path removed from main search)
- [ ] `grep -rn "cosine_similarity" backend/` → should only appear in `_python_cosine_search` (backward compat)

**Manual test (requires PostgreSQL with pgvector extension):**
```bash
# Start backend
uvicorn backend.main:app --reload

# Test health
curl http://localhost:8000/health

# Test search (replace with real API key and client key)
curl -X POST http://localhost:8000/search \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "test question", "top_k": 5}'
```

**Verify pgvector is installed in PostgreSQL:**
```sql
-- In psql:
CREATE EXTENSION IF NOT EXISTS vector;
SELECT * FROM pg_extension WHERE extname = 'vector';
```

---

## GIT PUSH

```bash
git add backend/models.py backend/search/service.py backend/chat/service.py requirements.txt
git commit -m "refactor: migrate to pgvector native search — replace Python cosine similarity

- Add Vector(1536) column to Embedding model (pgvector)
- Rewrite search_similar_chunks() to use SQL cosine_distance (<=>)
- client_id filter enforced at DB level (tenant isolation)
- Fallback to Python search for SQLite (tests)
- retrieve_context() now delegates to search_similar_chunks()
- Keep cosine_similarity() for backward compat
- 100x faster for large datasets, O(log N) with HNSW index"

git push origin feature/refactor-pgvector-native-search
```

---

## NOTES

### Threshold change
- Old: `VECTOR_CONFIDENCE_THRESHOLD = 0.3` (similarity ≥ 0.3 → use vector)
- New: `VECTOR_CONFIDENCE_THRESHOLD = 0.70` (similarity ≥ 0.70 → use vector)
- `DISTANCE_THRESHOLD = 0.30` (1 - 0.70 = 0.30, used internally with pgvector)
- Reason: 0.3 similarity is too low (weak signal). 0.70 is more meaningful.
- Adjust as needed after testing with real data.

### ⚠️ DEPLOYMENT ORDER (CRITICAL)

**Wrong order will break production!**

**CORRECT order:**
1. ✅ **This PR** — code changes (models, search, embeddings service)
2. **Next PR** — Alembic migration:
   - Add `vector Vector(1536)` column
   - Backfill: `UPDATE embeddings SET vector = (metadata_json->>'vector')::vector`
   - Create HNSW index: `CREATE INDEX ON embeddings USING hnsw (vector vector_cosine_ops)`
3. **Deploy migration** to production first
4. **Deploy code** after migration completes

**Why:** The code expects the `vector` column to exist. If deployed before migration, it will crash with `column does not exist`.

**Do NOT create migration in this PR** — migration is a separate PR after this one.

### Migration needed (separate PR)
After this PR merges, create migration PR:
1. Add the `vector Vector(1536)` column
2. Backfill existing vectors from `metadata_json["vector"]` → `vector` column
3. Create HNSW index: `CREATE INDEX ON embeddings USING hnsw (vector vector_cosine_ops);`
4. Make `vector` NOT NULL after backfill

### SQLite fallback
`_python_cosine_search` is preserved for tests that use SQLite.
It first tries the `vector` column, then falls back to `metadata_json["vector"]`.
Tests should continue to work without changes.

### pgvector extension
Requires PostgreSQL with pgvector installed:
```sql
CREATE EXTENSION vector;
```
Railway PostgreSQL supports pgvector out of the box.

### Performance expectation
- Before: ~50ms per query for 100 docs (Python loop)
- After: ~2-5ms per query for 100 docs (SQL)
- After (with HNSW index): ~1-2ms for 10,000+ docs
