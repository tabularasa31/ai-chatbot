# FI-CORS: Fix CORS Configuration for Production

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fix-cors-config
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/main.py` — CORS middleware configuration

**Do NOT touch:**
- Other backend files
- Frontend
- Database, migrations

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Current setup:**
```
Frontend: getchat9.live (Vercel)
Backend: ai-chatbot-production-6531.up.railway.app (Railway)
Auth: JWT in Authorization header (not cookies)
```

**Current CORS configuration:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Problem:**
- `allow_origins=["*"]` is insecure on production
- Allows any domain to access the API
- Should whitelist only trusted origins

**Solution:**
Add environment variable for allowed origins and use it in CORS config.

---

## WHAT TO DO

### 1. Update backend/main.py

**Step 1: Add import at the top**

Find the imports section (top of file, around lines 1-10). Add:
```python
import os
```

(Should be near `from pathlib import Path` and other standard library imports)

**Step 2: Find the CORS middleware block (around line 25-32):**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Step 3: Replace with:**
```python
# CORS configuration - whitelist allowed origins
ALLOWED_ORIGINS = [
    origin.strip() 
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,https://getchat9.live"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
```

**Why the .strip() and filtering?**
- Removes leading/trailing whitespace from each origin
- Filters out empty strings if config has trailing/leading commas
- Makes config more robust

### 2. Update .env.example

Find the section with other environment variables. Add:
```
# CORS configuration (comma-separated list of allowed origins)
# No spaces around commas. Examples:
#   Development: http://localhost:3000,https://getchat9.live
#   Production: https://getchat9.live,https://embed.getchat9.live
CORS_ALLOWED_ORIGINS=http://localhost:3000,https://getchat9.live
```

### 3. Configure for production deployment

When deploying to production (Railway), set this environment variable:
```
CORS_ALLOWED_ORIGINS=https://getchat9.live
```

Or if widget is served from separate domain:
```
CORS_ALLOWED_ORIGINS=https://getchat9.live,https://embed.getchat9.live
```

**Note:** No spaces around commas (the code strips them, but it's cleaner to not have them)

---

## WHY THIS CHANGE

**Before:**
- Any domain can make requests to the API
- Security risk if API contains sensitive data
- Open to abuse

**After:**
- Only whitelisted domains can access the API
- Configurable per environment (dev/prod)
- More secure

**Note on `allow_credentials=False`:**
- Correct, because we use JWT in Authorization header, not cookies
- Remains unchanged

---

## TESTING

Before pushing:
- [ ] Frontend can still communicate with backend (no CORS errors)
- [ ] Browser console shows no "CORS policy" warnings
- [ ] API requests work from getchat9.live
- [ ] `npm run build` passes without errors
- [ ] Locally (localhost:3000) can still hit backend

**Test locally:**
```bash
# Backend
CORS_ALLOWED_ORIGINS="http://localhost:3000" uvicorn backend.main:app --reload

# Frontend
NEXT_PUBLIC_API_URL="http://localhost:8000" npm run dev
# Try chat, should work without CORS errors
```

---

## GIT PUSH

```bash
git add backend/main.py .env.example
git commit -m "fix: configure CORS origins for production (whitelist approach)"
git push origin feature/fix-cors-config
```

Then create PR, review, and merge.

---

## NOTES

- `allow_methods` includes common REST methods. Add PATCH if your API uses it
- `allow_headers` covers JWT auth + JSON. Add others if frontend sends additional headers (e.g., X-Requested-With, Accept)
- `.strip()` and `if origin.strip()` handle whitespace and empty values robustly
- Environment variable defaults to dev + prod URLs for convenience
- Can be overridden per environment without code changes
- **Widget domain:** If embed widget runs on separate domain (e.g., https://embed.getchat9.live), include it in CORS_ALLOWED_ORIGINS

## References

- Backend file: `backend/main.py` (CORS middleware section)
- Env file: `.env.example` (add CORS_ALLOWED_ORIGINS)
- FastAPI CORS docs: https://fastapi.tiangolo.com/tutorial/cors/
