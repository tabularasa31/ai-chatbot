# FI-019 ext: BM25 Hybrid Search + HNSW — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b feature/fi-019ext-bm25-hybrid
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**Prerequisites:** pgvector migration AND FI-019 cleanup must be merged to main first.

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/search/service.py` — replace keyword search with BM25, add RRF fusion
- `backend/requirements.txt` — add `rank-bm25`

**Do NOT touch:**
- migrations
- `backend/models.py`
- `backend/chat/service.py`
- Frontend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** Current keyword fallback in `keyword_search_chunks()` counts simple word matches in Python — slow and imprecise. BM25 is the industry standard for full-text search (used in Elasticsearch, Lucene) and accounts for term frequency and document length.

**Goal:** Replace `keyword_search_chunks()` with `bm25_search_chunks()`, then combine vector + BM25 results using RRF (Reciprocal Rank Fusion) for true hybrid search.

**What is RRF:** Combines rankings from multiple sources without needing to normalize scores:
```
score(doc) = Σ 1 / (k + rank_in_source)   # k=60 is standard
```

---

## WHAT TO DO

### 1. Add `rank-bm25` to `backend/requirements.txt`
```
rank-bm25>=0.2.2
```

### 2. Replace `keyword_search_chunks` with `bm25_search_chunks`

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
    Replaces keyword_search_chunks(). Returns normalized scores [0, 1].
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

    corpus = [(emb.chunk_text or "").lower().split() for emb in embeddings]
    bm25 = BM25Okapi(corpus)
    query_tokens = query.lower().split()
    scores = bm25.get_scores(query_tokens)

    max_score = max(scores) if max(scores) > 0 else 1.0
    normalized = scores / max_score

    scored = [
        (emb, float(score))
        for emb, score in zip(embeddings, normalized)
        if score > 0
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
```

### 3. Add `reciprocal_rank_fusion`

```python
def reciprocal_rank_fusion(
    vector_results: list[tuple[Embedding, float]],
    bm25_results: list[tuple[Embedding, float]],
    k: int = 60,
    top_k: int = 5,
) -> list[tuple[Embedding, float]]:
    """Combine vector and BM25 results using Reciprocal Rank Fusion."""
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

### 4. Extract `_pgvector_search` helper

Refactor the inline pgvector query from `search_similar_chunks` into a separate function:

```python
def _pgvector_search(
    client_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: Session,
) -> list[tuple[Embedding, float]]:
    """Native pgvector cosine distance search. PostgreSQL only."""
    try:
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
        return [
            (emb, max(0.0, 1.0 - distance))
            for emb, distance in results_with_distance
        ]
    except Exception:
        return _python_cosine_search(client_id, query_vector, top_k, db)
```

### 5. Update `search_similar_chunks` to use hybrid pipeline

```python
def search_similar_chunks(...) -> list[tuple[Embedding, float]]:
    query_vector = embed_query(query, api_key=api_key)

    # SQLite fallback (tests only)
    if "sqlite" in str(db.bind.url if db.bind else ""):
        return _python_cosine_search(client_id, query_vector, top_k, db)

    # 1. Vector search
    vector_results = _pgvector_search(client_id, query_vector, top_k * 2, db)

    # 2. BM25 search
    bm25_results = bm25_search_chunks(client_id, query, top_k * 2, db)

    # 3. Handle edge cases
    if not vector_results and not bm25_results:
        return []
    if not bm25_results:
        return vector_results[:top_k]
    if not vector_results:
        return bm25_results[:top_k]

    # 4. Hybrid: RRF fusion
    return reciprocal_rank_fusion(vector_results, bm25_results, top_k=top_k)
```

### 6. Remove `keyword_search_chunks` and `_extract_keywords`

Verify they're not imported elsewhere:
```bash
grep -r "keyword_search_chunks\|_extract_keywords" backend/
```
Then delete both functions.

Also remove `VECTOR_CONFIDENCE_THRESHOLD` and `DISTANCE_THRESHOLD` constants — no longer needed.

---

## TESTING

Before pushing:
- [ ] `pip install rank-bm25` installs cleanly
- [ ] `grep -r "keyword_search_chunks" backend/` returns no results
- [ ] `pytest -q` passes
- [ ] Manual test: search returns relevant results (not empty)

---

## GIT PUSH

```bash
git add backend/search/service.py backend/requirements.txt
git commit -m "feat: replace keyword search with BM25 + RRF hybrid search (FI-019 ext)"
git push origin feature/fi-019ext-bm25-hybrid
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `rank-bm25` is pure Python, no system dependencies
- BM25 corpus built in-memory per request — fine for MVP (hundreds of chunks). For thousands+, consider caching.
- RRF doesn't require score normalization — main advantage over weighted sum approaches

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Replaced simple keyword fallback with BM25 full-text search and combined with vector search using Reciprocal Rank Fusion for true hybrid retrieval.

## Changes
- `backend/search/service.py` — added bm25_search_chunks(), reciprocal_rank_fusion(), _pgvector_search(); updated search_similar_chunks() to hybrid pipeline; removed keyword_search_chunks()
- `backend/requirements.txt` — added rank-bm25

## Testing
- [ ] All tests pass
- [ ] No references to keyword_search_chunks remain
- [ ] Search returns relevant results

## Notes
BM25 built in-memory per request. RRF merges rankings without score normalization.
```
