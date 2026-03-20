# Fix: CORS — Split Public and Private Endpoints — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b fix/cors-split-public-private
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
- `backend/main.py` — replace single CORS middleware with split logic

**Do NOT touch:**
- Individual route files
- migrations
- Frontend files
- Any other backend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** A single global CORS middleware applies the same `ALLOWED_ORIGINS` whitelist to ALL endpoints. The widget is embedded on clients' websites (e.g. `shop.example.com`) — these domains are not in the whitelist, so browsers block requests to `/widget/chat`. The widget is broken for any real client domain.

**Current state in `backend/main.py`:**
```python
ALLOWED_ORIGINS = [
    x.strip()
    for x in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,https://getchat9.live",
    ).split(",")
    if x.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
```

**Why `allow_origins=["*"]` is safe for `/widget/chat`:**
- Authentication is done via `X-API-Key` query param, not cookies/sessions
- `allow_credentials=False` must stay — browser blocks `credentials=True` with `origins=["*"]`

---

## WHAT TO DO

Replace the single `CORSMiddleware` in `backend/main.py` with a custom middleware that applies different CORS rules based on the request path.

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Paths accessible from any domain (widget on client sites)
PUBLIC_PATHS = ("/widget/", "/health", "/embed.js")

class SplitCORSMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_origins: list[str]):
        super().__init__(app)
        self.allowed_origins = allowed_origins

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "")
        path = request.url.path
        is_public = any(path.startswith(p) for p in PUBLIC_PATHS)

        # Handle preflight
        if request.method == "OPTIONS":
            response = Response(status_code=200)
        else:
            response = await call_next(request)

        if is_public:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin in self.allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"

        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key"
        response.headers["Access-Control-Allow-Credentials"] = "false"
        return response
```

Replace the `app.add_middleware(CORSMiddleware, ...)` call with:

```python
app.add_middleware(SplitCORSMiddleware, allowed_origins=ALLOWED_ORIGINS)
```

Remove the `from fastapi.middleware.cors import CORSMiddleware` import if no longer used.

---

## TESTING

Before pushing:
- [ ] `pytest -q` passes
- [ ] `/health` returns 200 with `Access-Control-Allow-Origin: *`
- [ ] `/widget/chat` OPTIONS preflight returns `Access-Control-Allow-Origin: *`
- [ ] `/auth/login` only allows origin from `CORS_ALLOWED_ORIGINS`
- [ ] No console errors on dashboard at `getchat9.live`

---

## GIT PUSH

```bash
git add backend/main.py
git commit -m "fix: split CORS — allow_origins=* for widget, whitelist for dashboard"
git push origin fix/cors-split-public-private
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `X-API-Key` must be in `Access-Control-Allow-Headers` — widget sends it as query param but keep header support for future.
- Do NOT set `allow_credentials=True` with `allow_origins=["*"]` — browsers reject this combination.
- `PUBLIC_PATHS` tuple is the single source of truth for what's public.

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Replaced single CORS middleware with path-aware split: widget endpoints allow any origin, dashboard endpoints use origin whitelist.

## Changes
- `backend/main.py` — custom `SplitCORSMiddleware` replacing `CORSMiddleware`

## Testing
- [ ] Tests pass (pytest)
- [ ] Widget chat endpoint accepts requests from arbitrary origins
- [ ] Dashboard endpoints restricted to CORS_ALLOWED_ORIGINS whitelist
- [ ] Preflight OPTIONS returns correct headers

## Notes
Widget uses X-API-Key auth (not cookies), so allow_origins=* is safe.
```
