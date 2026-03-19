# SECURITY: Add Rate Limit to /search

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/security-rate-limit-search
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/search/routes.py` — add rate limit to `/search`

**Do NOT touch:**
- Other routes
- Database, models, migrations
- Backend core files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Issue:** `POST /search` endpoint calls OpenAI embeddings for every request. Without rate limiting, user can make 1000s of requests → expensive API bills.

**Current code (backend/search/routes.py):**
```python
@search_router.post("", response_model=SearchResponse)
def search_route(
    body: SearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SearchResponse:
```

**Solution:** Add rate limit decorator (30/minute, same as /chat endpoint).

---

## WHAT TO DO

### 1. Add rate limit import

In `backend/search/routes.py`, add to imports:
```python
from fastapi import Request
from backend.core.limiter import limiter
```

### 2. Add rate limit decorator

Find the `search_route` function and add decorator:

**Before:**
```python
@search_router.post("", response_model=SearchResponse)
def search_route(
    body: SearchRequest,
```

**After:**
```python
@search_router.post("", response_model=SearchResponse)
@limiter.limit("30/minute")  # Add this line
def search_route(
    request: Request,  # Add this parameter
    body: SearchRequest,
```

### 3. Verify imports

Check that these are already in the file:
```python
from fastapi import Request, Depends, APIRouter
from typing import Annotated
```

If not, add them.

---

## TESTING

Before pushing:
- [ ] Backend starts without errors
- [ ] Valid search returns results
- [ ] After 30 requests/minute, get 429 Too Many Requests
- [ ] Different users have separate rate limit counters (per JWT token)

**Test:**
```bash
# First search (OK)
curl -X POST http://localhost:8000/search \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "test"}'

# After 30 requests from same token, should get 429:
# {"detail":"429: Too Many Requests"}
```

---

## GIT PUSH

```bash
git add backend/search/routes.py
git commit -m "security: add rate limiting to /search endpoint (30/min)"
git push origin feature/security-rate-limit-search
```

---

## NOTES

- Rate limit: 30/minute (matches /chat endpoint)
- Uses JWT token (user_id) for rate limit key
- slowapi already configured in backend/main.py
- This prevents abuse of expensive OpenAI embeddings API
