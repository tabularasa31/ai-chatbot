# Fix: Rate Limiting for /widget/chat — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b fix/widget-rate-limiting
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
- `backend/routes/widget.py` — add rate limiting to `/widget/chat`

**Do NOT touch:**
- migrations
- `backend/main.py`
- `backend/core/limiter.py`
- Frontend files
- Any other route files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** `POST /widget/chat` is a public endpoint with no rate limiting. Anyone can send unlimited requests, consuming the client's OpenAI tokens at no cost to the attacker.

**Current state:** No `@limiter.limit()` decorator on the endpoint.

**Other endpoints for reference (already protected):**
```python
# chat/routes.py
@limiter.limit("30/minute")   # /chat
@limiter.limit("30/minute")   # /chat/debug
# search/routes.py
@limiter.limit("30/minute")   # /search
```

**`slowapi` requirement:** The endpoint must accept `request: Request` as the first parameter for rate limiting to work.

---

## WHAT TO DO

In `backend/routes/widget.py`, update the `/widget/chat` endpoint:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from backend.core.limiter import limiter

@widget_router.post("/chat")
@limiter.limit("20/minute")
def widget_chat(
    request: Request,   # ← required by slowapi — add as first param
    message: Annotated[str, Query(description="User message")],
    client_id: Annotated[str, Query(description="Public client ID (ch_xyz)")],
    session_id: Annotated[Optional[str], Query(description="Optional session ID")] = None,
    db: Session = Depends(get_db),
) -> dict:
    ...  # rest of function unchanged
```

That's it — one decorator and one parameter.

---

## TESTING

Before pushing:
- [ ] `pytest -q` passes
- [ ] Endpoint still returns correct response on normal request
- [ ] No import errors

---

## GIT PUSH

```bash
git add backend/routes/widget.py
git commit -m "fix: add rate limiting (20/min) to /widget/chat public endpoint"
git push origin fix/widget-rate-limiting
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `20/minute` per IP — slightly stricter than `30/minute` for authenticated endpoints, since this is fully public
- slowapi automatically returns `429 Too Many Requests` — handler is already registered in `main.py`
- In test environment, `limiter` uses unique UUID keys so rate limits never trigger in tests

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Added rate limiting (20 req/min per IP) to the public /widget/chat endpoint to prevent token abuse.

## Changes
- `backend/routes/widget.py` — added @limiter.limit("20/minute") and request: Request parameter

## Testing
- [ ] Tests pass
- [ ] Endpoint returns 429 after 20 requests/min (manual test or test with limiter)

## Notes
429 handler already registered in main.py via slowapi.
```
