# SECURITY: Add Rate Limit to /clients/validate/{api_key}

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/security-rate-limit-validate
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/clients/routes.py` — add rate limit to `/clients/validate/{api_key}`

**Do NOT touch:**
- Other routes
- Database, models, migrations
- Backend core files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Issue:** `GET /clients/validate/{api_key}` endpoint is public and allows anyone to check if an API key is valid. Without rate limiting, attacker can brute-force 32-char hex keys.

**Current code (backend/clients/routes.py):**
```python
@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
def validate_api_key(
    api_key: str,
    db: Annotated[Session, Depends(get_db)],
) -> ValidateApiKeyResponse:
```

**Solution:** Add rate limit decorator from `slowapi` (already imported in backend/main.py).

---

## WHAT TO DO

### 1. Add rate limit import

In `backend/clients/routes.py`, add to imports:
```python
from slowapi.util import get_remote_address
from backend.core.limiter import limiter
```

### 2. Add rate limit decorator

Find the `validate_api_key` function and add decorator:

**Before:**
```python
@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
def validate_api_key(
```

**After:**
```python
@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
@limiter.limit("20/minute")  # Add this line
def validate_api_key(
    request: Request,  # Add this parameter
    api_key: str,
    db: Annotated[Session, Depends(get_db)],
) -> ValidateApiKeyResponse:
```

### 3. Add `Request` import

Add to imports:
```python
from fastapi import Request
```

---

## TESTING

Before pushing:
- [ ] Backend starts without errors
- [ ] Valid API key returns `{valid: true}`
- [ ] Invalid API key returns `{valid: false}`
- [ ] After 20 requests/minute, get 429 Too Many Requests
- [ ] Different IPs have separate rate limit counters

**Test rate limit:**
```bash
# First request (OK)
curl http://localhost:8000/clients/validate/validkey123456789012345678901234

# After 20 requests, should get 429:
# {"detail":"429: Too Many Requests"}
```

---

## GIT PUSH

```bash
git add backend/clients/routes.py
git commit -m "security: add rate limiting to /clients/validate endpoint (20/min)"
git push origin feature/security-rate-limit-validate
```

---

## NOTES

- Rate limit: 20/minute (prevents brute-force but allows normal usage)
- Uses IP address for rate limit key (auto from `get_remote_address`)
- slowapi already configured in backend/main.py with limiter instance
