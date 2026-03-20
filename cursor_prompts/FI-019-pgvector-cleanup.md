# FI-019: pgvector cleanup — remove dead code — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b feature/fi-019-pgvector-cleanup
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main (must run AFTER pgvector migration is deployed to Railway)
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/search/service.py` — remove `cosine_similarity()`, update `_python_cosine_search` docstring

**Do NOT touch:**
- migrations
- `backend/models.py`
- `backend/chat/service.py`
- Frontend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Current state in `backend/search/service.py`:**
- pgvector `<=>` operator is already used for PostgreSQL path ✅
- `cosine_similarity()` function exists "for backward compatibility" but is not actively used
- `_python_cosine_search()` is needed for SQLite tests but has no clear docstring
- `VECTOR_CONFIDENCE_THRESHOLD` and `DISTANCE_THRESHOLD` constants still exist

**Goal:** Clean up dead code, clarify what's test-only vs production.

---

## WHAT TO DO

### 1. Remove `cosine_similarity()` function

First verify it's not imported anywhere:
```bash
grep -r "cosine_similarity" backend/
```

If no results outside `search/service.py` itself — delete the entire function:
```python
# DELETE this entire function:
def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Kept for backward compatibility. Prefer pgvector native search."""
    ...
```

### 2. Update `_python_cosine_search` docstring

```python
def _python_cosine_search(...):
    """
    SQLite/test fallback ONLY. NOT used in production.
    Production uses pgvector native <=> operator via search_similar_chunks().
    Do not call this in production code.
    """
```

### 3. Keep everything else as-is

`VECTOR_CONFIDENCE_THRESHOLD`, `keyword_search_chunks`, `search_similar_chunks` — do not touch.

---

## TESTING

Before pushing:
- [ ] `grep -r "cosine_similarity" backend/` returns no results
- [ ] `pytest -q` passes
- [ ] No import errors

---

## GIT PUSH

```bash
git add backend/search/service.py
git commit -m "refactor: remove dead cosine_similarity(), clarify _python_cosine_search is test-only (FI-019)"
git push origin feature/fi-019-pgvector-cleanup
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- This is a safe cleanup — no behavior changes, only removal of unused code
- `_python_cosine_search` must stay — it's the SQLite fallback used in all tests

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Removed unused `cosine_similarity()` function and clarified that `_python_cosine_search` is a test-only SQLite fallback.

## Changes
- `backend/search/service.py` — removed `cosine_similarity()`, updated `_python_cosine_search` docstring

## Testing
- [ ] No references to cosine_similarity remain
- [ ] All tests pass (pytest)

## Notes
No behavior changes. Production already uses pgvector native <=> operator.
```
