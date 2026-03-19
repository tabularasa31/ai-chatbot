# SECURITY: Validate limit/offset in /chat/bad-answers

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/security-validate-list-params
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/chat/routes.py` — add validation to `list_bad_answers` function

**Do NOT touch:**
- Other routes
- Database, models, migrations
- Backend core files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Issue:** `GET /chat/bad-answers?limit=999999&offset=-1` parameters are not validated. Can cause:
- `limit=999999` → Heavy DB load (return millions of rows)
- `offset=-1` → SQL error or unexpected behavior
- `limit=0` → Empty results always

**Current code (backend/chat/routes.py):**
```python
@chat_router.get("/bad-answers", response_model=BadAnswerListResponse)
def list_bad_answers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 50,
    offset: int = 0,
) -> BadAnswerListResponse:
```

**Solution:** Add Pydantic validators for `limit` and `offset` using `Query`.

---

## WHAT TO DO

### 1. Add imports

In `backend/chat/routes.py`, ensure these imports exist:
```python
from fastapi import Query
from pydantic import Field
```

### 2. Update function signature

Find the `list_bad_answers` function and replace the parameter declarations:

**Before:**
```python
@chat_router.get("/bad-answers", response_model=BadAnswerListResponse)
def list_bad_answers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 50,
    offset: int = 0,
) -> BadAnswerListResponse:
```

**After:**
```python
@chat_router.get("/bad-answers", response_model=BadAnswerListResponse)
def list_bad_answers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BadAnswerListResponse:
```

**Explanation:**
- `limit: ... Query(ge=1, le=100)` — Between 1 and 100 (inclusive)
- `offset: ... Query(ge=0)` — Greater than or equal to 0 (no negative values)
- Default values preserved: `limit=50`, `offset=0`

### 3. No code body changes needed

The validation happens in FastAPI's parameter layer. No changes needed to the function body.

---

## TESTING

Before pushing:
- [ ] `?limit=50&offset=0` works (defaults)
- [ ] `?limit=100&offset=0` works (max limit)
- [ ] `?limit=1&offset=0` works (min limit)
- [ ] `?limit=0` returns 422 (Unprocessable Entity)
- [ ] `?limit=101` returns 422 (exceeds max)
- [ ] `?limit=-5` returns 422 (negative)
- [ ] `?offset=-1` returns 422 (negative offset)
- [ ] `?offset=999` works (large offset OK)

**Test with curl:**
```bash
# Valid
curl "http://localhost:8000/chat/bad-answers?limit=50&offset=0" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Invalid: returns 422 validation error
curl "http://localhost:8000/chat/bad-answers?limit=0" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Invalid: returns 422 validation error
curl "http://localhost:8000/chat/bad-answers?offset=-1" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## GIT PUSH

```bash
git add backend/chat/routes.py
git commit -m "security: add validation for limit/offset in /chat/bad-answers (1-100 limit, offset>=0)"
git push origin feature/security-validate-list-params
```

---

## NOTES

- `limit`: 1 to 100 (prevents huge queries)
- `offset`: >= 0 (prevents SQL errors)
- FastAPI automatically returns 422 if validation fails
- No database changes needed
- No function logic changes needed
