# SECURITY: Add Rate Limit to /clients/validate/{api_key}

⚠️ **CRITICAL: Follow the SETUP commands EXACTLY in order. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/security-rate-limit-validate
```

**MUST DO:**
1. `git checkout main` — switch to main
2. `git pull origin main` — fetch latest (do not skip!)
3. `git checkout -b feature/security-rate-limit-validate` — create NEW branch from latest main

**DO NOT reuse old branches or skip the pull step.**

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

**Current code (backend/clients/routes.py) — before rate limiting:**
```python
@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
def validate_api_key(
    api_key: str,
    db: Annotated[Session, Depends(get_db)],
) -> ValidateApiKeyResponse:
```

**Solution:** Add rate limit decorator from `slowapi` via `limiter` (imported from backend.core.limiter).

---

## WHAT TO DO

### 1. Add rate limit import

In `backend/clients/routes.py`, add to imports:
```python
from backend.core.limiter import limiter
```

(Note: `get_remote_address` is not needed — limiter already uses it via `_key_func` in backend/core/limiter.py)

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
- [ ] Valid API key returns `200 + {client_id, name}`
- [ ] Invalid API key returns `404 + {"detail": "Invalid API key"}`
- [ ] After 20 requests/minute, get `429` (Too Many Requests) with rate limit detail message
- [ ] Different IPs have separate rate limit counters

**Test rate limit:**
```bash
# Get a real API key from your test database or create one via /clients

# First request (OK — valid key)
curl http://localhost:8000/clients/validate/<YOUR_REAL_API_KEY>

# Response: 200 + {"client_id": "...", "name": "..."}

# Invalid key
curl http://localhost:8000/clients/validate/invalidkey12345678901234567890

# Response: 404 + {"detail": "Invalid API key"}

# After 20 requests/minute from same IP, should get 429:
# Response: 429 + {"detail": "...rate limit exceeded..."}

# Test per-IP rate limiting (different IPs separate counters):
# From machine A: curl ... (counter A increments)
# From machine B: curl ... (counter B increments, independent from A)
# Or use -H "X-Forwarded-For: <different-ip>" if testing from localhost
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
- Uses IP address for rate limit key (via `get_remote_address` in backend/core/limiter.py's `_key_func`)
- slowapi is configured in backend/main.py with limiter instance available for all routes
- Each IP address gets its own separate rate limit counter (independent for 127.0.0.1, 192.168.1.100, etc.)
- 429 response format may vary by slowapi version — check for 429 status + detail message about rate limit
- For additional security: consider logging frequent 429s for anomaly detection
- Distributed attacks (botnet) not protected against IP-based limiting, but good first line of defense
- For local testing with curl: use `-H "X-Forwarded-For: <ip>"` to simulate different IPs if needed
