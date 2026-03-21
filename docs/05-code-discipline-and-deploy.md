# Code Discipline & Deployment

---

## Code Discipline Rules

### Rule #1: Atomic Architecture

Actual repository layout (updated to match the codebase). Tests live in the root **`tests/`** directory, not inside each feature package.

```
backend/
├─ main.py                 # FastAPI app, router includes
├─ models.py               # SQLAlchemy models (single module)
├─ requirements.txt        # mirrors repo root dependencies (see below)
│
├─ core/                   # DB, config, JWT helpers, rate limiter, OpenAI wrapper
│  ├─ db.py
│  ├─ config.py
│  ├─ security.py
│  ├─ crypto.py
│  ├─ limiter.py           # slowapi
│  ├─ openai_client.py
│  └─ utils.py
│
├─ auth/                   # register, login, email verification, password reset
├─ admin/                  # admin metrics
├─ clients/                # tenant clients, API key, public_id
├─ documents/              # upload, parsing, status
├─ embeddings/             # chunking, embeddings (sync indexing; no separate worker.py)
├─ search/                 # chunk retrieval
├─ chat/                   # RAG chat: service, routes, pii.py (FI-043 redaction), …
├─ email/                  # transactional email (Brevo)
│
├─ routes/                 # cross-cutting public HTTP
│  ├─ public.py            # e.g. GET /embed.js
│  └─ widget.py            # public widget: POST /widget/chat (rate limited)
│
├─ static/
│  └─ embed.js             # loader served by backend (see public.py)
├─ widget/
│  ├─ routes.py            # small helper router (app wires `backend.routes.widget`, not this file)
│  └─ static/embed.js      # alternate script copy (do not confuse with backend/static/embed.js)
│
└─ migrations/             # Alembic

requirements.txt           # repo root — primary for local setup (see README)
tests/
├─ conftest.py
├─ chat/
│  └─ test_pii.py          # FI-043 redaction unit tests
├─ test_auth.py
├─ test_chat.py
├─ test_clients.py
├─ test_documents.py
├─ test_embeddings.py
├─ test_search.py
├─ test_widget.py
├─ test_admin_metrics.py
├─ test_models.py
└─ …
frontend/                  # Next.js app (separate from backend)
```

**RULE:** One phase = one coherent set of files in its area. Do not change other modules without an explicit spec and review.

---

### Rule #2: Spec with "MUST NOT TOUCH"

For each phase, I provide:

```
PHASE X: Feature Name

SPEC:
[detailed requirements]

FILES TO CREATE:
✅ backend/module/routes.py
✅ backend/module/service.py
✅ tests/test_module.py

FILES TO MODIFY:
✅ backend/main.py (minimal change: `include_router` / import line only if the spec says so)

MUST NOT TOUCH:
❌ backend/core/*
❌ backend/migrations/ (unless schema change approved)
❌ backend/auth/* (after Phase 3 is approved)
❌ Existing tests

IF YOU NEED TO CHANGE FROZEN CODE:
→ STOP AND ASK (do not proceed)
```

---

### Rule #3: Branch per Phase

This repo uses **`main`** for ongoing work; production deploys often track a **`deploy`** branch (Vercel / Railway). There may be no `develop` branch—treat **`main`** as the default base.

```bash
git checkout main
git pull origin main
git checkout -b feature/documents
# … work, commit, pytest locally …
git push -u origin feature/documents
# Open PR into main (or agreed base)

# After merge: merge or fast-forward into `deploy` when cutting a production release (your process).
# If you need to discard local work: git fetch origin && git reset --hard origin/main
```

---

### Rule #4: Tests as Guard Rails

**Before each phase:**
```bash
pytest tests/test_auth.py -v
# All PASS → locked tests
# If broken after next feature → REJECT MERGE
```

**After each phase:**
```bash
pytest tests/test_auth.py -v      # Must still pass
pytest tests/test_documents.py -v # New tests
pytest --cov=backend --cov-min-percentage=80
```

**If auth tests fail after documents phase → REJECT MERGE**

---

### Rule #5: Code Review Checklist

Before merge, I check:

```
☐ No modifications to core/* files
☐ Only new files created (unless in MODIFY list)
☐ All existing tests still pass (pytest)
☐ New tests ≥80% code coverage
☐ No imports from non-existent modules
☐ No SQL modifying old tables (unless approved)
☐ Error handling complete (try/except, HTTP status codes)
☐ Docstrings + type hints present
☐ Follows exact spec (no extra features)
☐ Database migrations included (if schema change)
☐ No secrets in code (use env vars only)
☐ Code follows PEP 8 (use black/flake8)

If ANY fail → Request changes, do not merge
```

---

### Rule #6: Incremental Testing

```
WRONG:
- "Implement entire document feature"
- Test everything at end
- Result: 10 bugs, hard to find

RIGHT:
- Phase 5a: Document model → test (MERGE)
- Phase 5b: Upload route skeleton → test (MERGE)
- Phase 5c: File parsing logic → test (MERGE)
- Phase 5d: Status tracking → test (MERGE)
- Phase 5e: Integration tests → test (MERGE)

Each sub-phase is merge-able and test-able
```

---

### Rule #7: FREEZE after Approval

Once a phase merges to main:

```python
# backend/documents/__init__.py

"""
FROZEN: This module is stable and production-ready.

If you need to modify this module:
1. Create a NEW module (e.g., documents_v2/)
2. Make backward-compatible changes only
3. Get code review approval
4. Write integration tests

Allowed changes:
- Adding new functions (not modifying existing)
- Adding optional parameters with defaults
- Bug fixes that don't change behavior

NOT allowed:
- Removing functions
- Changing function signatures
- Changing DB schema
- Removing tests

Please ask before making changes.
"""
```

---

## Git Workflow

### Initial Setup

```bash
git clone git@github.com:tabularasa31/ai-chatbot.git
cd ai-chatbot

git config user.name "Your Name"
git config user.email "your@email.com"

git checkout main
git pull origin main
# Optional: maintain `deploy` for production triggers
```

### Per-Phase Workflow

```bash
# 1. Feature branch from up-to-date main
git checkout main
git pull origin main
git checkout -b feature/documents

# 2. Changes: stay within spec; tests under tests/; run pytest tests/ -v

# 3. Atomic commits
git add backend/documents/
git commit -m "feat(documents): add PDF parsing"
git add tests/test_documents.py
git commit -m "test(documents): extend coverage"

# 4. Push and open PR to main
git push -u origin feature/documents

# 5. After approval: merge to main
# 6. Production: merge main → deploy when ready (per team process)

# Reset local branch after feedback:
git fetch origin
git reset --hard origin/main
```

---

## Deployment

### Backend (Railway)

**Step 1: Create Railway Project**
```bash
# Visit railway.app
# Create new project
# Select "Deploy from GitHub"
# Connect tabularasa31/ai-chatbot
```

**Step 2: Add PostgreSQL**
```bash
# In Railway dashboard:
# Add Service → PostgreSQL
# Copy DATABASE_URL from plugin
```

**Step 3: Set Environment Variables**
```bash
DATABASE_URL=postgresql://...
JWT_SECRET=<generate random 32-char string>
ENVIRONMENT=production
ENCRYPTION_KEY=<Fernet key for per-client OpenAI key storage>
# Also Brevo, FRONTEND_URL, CORS_ALLOWED_ORIGINS, etc. (see .env.example)
# Global OPENAI_API_KEY is optional—clients set keys in the dashboard
```

**Step 4: Deploy**
```bash
# Railway: deploy from the configured branch (often `deploy` or `main`)
# Check logs in Railway dashboard
```

**Step 5: Test**
```bash
curl https://api.yourdomain.com/health
# Should return: {"status": "ok"}
```

### Frontend (Vercel)

**Step 1: Connect Vercel**
```bash
# Visit vercel.com
# Import project from GitHub
# Select tabularasa31/ai-chatbot
```

**Step 2: Configure Build**
```
Framework: Next.js
Build Command: npm run build
Output Directory: .next
```

**Step 3: Set Environment Variables**
```bash
NEXT_PUBLIC_API_URL=https://api.yourdomain.com
```

**Step 4: Deploy**
```bash
# Vercel: production branch is often `deploy` (confirm in project settings)
```

**Step 5: Test**
```
Visit https://app.yourdomain.com
- Can login
- Can upload document
- Can view chat logs
```

### Widget (served from API)

**Script:** `GET /embed.js` — loader injects an iframe pointing at `/widget?clientId=…`.

**Typical snippet (public `clientId` like `ch_…` from the dashboard):**
```html
<script src="https://api.yourdomain.com/embed.js?clientId=YOUR_PUBLIC_CLIENT_ID"></script>
```

When frontend and API hosts differ (e.g. getchat9.live + Railway API), the dashboard “Copy embed code” may add `window.Chat9Config = { widgetUrl: "…" }` before the script tag.

**Smoke test:** paste the snippet into a static HTML page with a real `clientId` and open in the browser.

---

## Live Demo Checklist

```
✅ Register as new client
  - Email: demo@test.com
  - Password: Demo123!
  - See API key generated
  - Copy embed code

✅ Upload documents
  - Upload product-guide.pdf
  - Upload FAQ.md
  - Upload pricing.json (Swagger)
  - All show "ready" status

✅ Embed widget
  - Copy embed code from dashboard (`embed.js?clientId=ch_…`)
  - Paste into test.html
  - Open in browser → iframe widget visible

✅ Ask 10 questions
  1. "How much does it cost?"
  2. "How do I reset password?"
  3. "What's your refund policy?"
  ... (7 more)
  - All answers based on docs
  - Sources shown correctly

✅ View logs
  - Dashboard → Chat Logs
  - See all 10 conversations
  - See questions, answers, sources
  - Verify accuracy

✅ Performance
  - Answer appears in <2 seconds
  - Dashboard responsive
  - No errors in console
  - Database queries fast
```

---

## MVP Success Criteria

### Technical ✅
- All tests passing (100%)
- No critical bugs
- API response time <2 seconds
- Database optimized (indexes, queries <100ms)
- Security: no SQL injection, XSS, CSRF

### Functional ✅
- User can register & login
- User can create client
- User can upload PDF, MD, Swagger
- Embeddings created automatically
- Chat generates correct answers
- Dashboard shows documents & logs
- Widget embeddable on external sites

### UX ✅
- Dashboard responsive (mobile + desktop)
- Upload easy (drag & drop)
- Chat fast (<2s response)
- Widget doesn't break client's site
- Error messages helpful

### Security ✅
- Users only see own clients
- Clients only see own documents
- Dashboard/API chat uses API key; public widget uses `clientId` — tenant isolation in both paths
- No sensitive data in logs

---

## Post-Launch

### Monitor
- Server uptime
- API latency
- OpenAI API usage + costs
- Database queries
- Error logs

### Next Phase (v2)
- Custom styling (colors, logos)
- Team collaboration
- Advanced analytics
- Integrations (Slack, email)
- Fine-tuning per client
- Payment system

---

Keep Rule #1 in sync whenever the backend tree changes materially.
