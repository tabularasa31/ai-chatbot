# REFACTOR: Fix datetime.utcnow(), CORS, and broad exceptions

⚠️ **CRITICAL: Follow the SETUP commands EXACTLY in order. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/refactor-datetime-cors-exceptions
```

**MUST DO:**
1. `git checkout main` — switch to main
2. `git pull origin main` — fetch latest (do not skip!)
3. `git checkout -b feature/refactor-datetime-cors-exceptions` — create NEW branch

**DO NOT reuse old branches or skip the pull step.**

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/auth/routes.py` — replace `datetime.utcnow()`
- `backend/core/security.py` — replace `datetime.utcnow()`
- `backend/models.py` — replace `datetime.utcnow()` in `_utcnow()` function
- `backend/main.py` — verify CORS config (no changes needed if already correct)
- `backend/core/crypto.py` — narrow broad `except Exception`
- `backend/core/openai_client.py` — change `from None` to `from e`

**Do NOT touch:**
- Database, migrations
- Other modules
- Business logic (only fix imports and exception handling)

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Issues from CODE_REVIEW.md:**

1. **datetime.utcnow() deprecated in Python 3.12+**
   - Used in: auth/routes.py, core/security.py, models.py
   - Should use: `datetime.now(timezone.utc)`
   - Reason: Python 3.12 marks utcnow as deprecated

2. **Broad `except Exception` in crypto.py**
   - Catches everything, hides actual errors
   - Should: catch only expected exceptions (InvalidToken, ValueError, TypeError)
   - Reason: Better debugging, cleaner error handling

3. **`from None` hides error chain in openai_client.py**
   - Current: `raise HTTPException(...) from None`
   - Should: `raise HTTPException(...) from e`
   - Reason: Preserve exception chain for debugging

4. **CORS allow_credentials check**
   - Current: `allow_credentials=False` but uses cookies
   - Should: Verify if correct (token is JWT in Authorization header, not cookies)
   - Reason: Security, consistency with auth method

---

## WHAT TO DO

### 1. Fix datetime.utcnow() in three files

**File: backend/auth/routes.py**

Search for `datetime.utcnow()` (should be ~2 instances around lines 44, 110)

**Before:**
```python
from datetime import datetime, timedelta

# ... later in code:
user.email_verification_token_expires_at = datetime.utcnow() + timedelta(days=1)
```

**After:**
```python
from datetime import datetime, timedelta, timezone

# ... later in code:
user.email_verification_token_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
```

**File: backend/core/security.py**

Search for `datetime.utcnow()` (should be ~1 instance around line 54)

**Before:**
```python
exp = datetime.utcnow() + timedelta(hours=24)
```

**After:**
```python
exp = datetime.now(timezone.utc) + timedelta(hours=24)
```

**File: backend/models.py**

Search for `_utcnow()` function (should be ~line 20)

**Before:**
```python
def _utcnow():
    return datetime.utcnow()
```

**After:**
```python
def _utcnow():
    return datetime.now(timezone.utc)
```

**Add to imports in models.py if not present:**
```python
from datetime import datetime, timezone
```

---

### 2. Fix broad exception in crypto.py

**File: backend/core/crypto.py**

Search for `except (InvalidToken, Exception)` (around line 28-31)

**Before:**
```python
try:
    return f.decrypt(value.encode()).decode()
except (InvalidToken, Exception) as e:
    raise RuntimeError(f"Failed to decrypt: {e}") from e
```

**After:**
```python
try:
    return f.decrypt(value.encode()).decode()
except InvalidToken as e:
    raise RuntimeError(f"Failed to decrypt: invalid token") from e
except (ValueError, UnicodeDecodeError, Exception) as e:
    raise RuntimeError(f"Failed to decrypt: {e}") from e
```

Or simpler (if only InvalidToken is expected):
```python
try:
    return f.decrypt(value.encode()).decode()
except (InvalidToken, ValueError) as e:
    raise RuntimeError(f"Failed to decrypt: {e}") from e
```

---

### 3. Fix `from None` in openai_client.py

**File: backend/core/openai_client.py**

Search for `from None` (around line 32-36)

**Before:**
```python
except RuntimeError:
    raise HTTPException(
        status_code=500,
        detail="Failed to decrypt OpenAI API key.",
    ) from None
```

**After:**
```python
except RuntimeError as e:
    raise HTTPException(
        status_code=500,
        detail="Failed to decrypt OpenAI API key.",
    ) from e
```

---

### 4. Verify CORS config

**File: backend/main.py**

Check lines ~37-42 (CORS middleware):
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
```

**Status:** This is correct. JWT token is in Authorization header, not cookies, so `allow_credentials=False` is appropriate. **No changes needed.**

---

## TESTING

Before pushing:
- [ ] Backend starts without errors (`python -m pytest` if available)
- [ ] No import errors related to datetime or timezone
- [ ] Code review passes: no `utcnow()` remaining in codebase
- [ ] Exception handling still works (test decrypt with invalid key)
- [ ] CORS still allows requests from frontend

**Quick test:**
```bash
# Check for remaining utcnow (should find 0 results)
grep -r "utcnow()" backend/ --include="*.py" | grep -v ".pyc"

# Check for remaining "from None" (should find 0)
grep -r "from None" backend/ --include="*.py" | grep -v ".pyc"
```

---

## GIT PUSH

```bash
git add backend/auth/routes.py backend/core/security.py backend/models.py backend/core/crypto.py backend/core/openai_client.py
git commit -m "refactor: fix datetime.utcnow() deprecation, broad exceptions, exception chaining (CODE_REVIEW)"
git push origin feature/refactor-datetime-cors-exceptions
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `datetime.utcnow()` is deprecated since Python 3.10, removed in 3.12+
- Use `datetime.now(timezone.utc)` for UTC-aware datetime
- Broad `except Exception` is code smell — catch only expected exceptions
- Preserve exception chains with `from e` for better debugging
- CORS config is already correct — verify but no changes needed
