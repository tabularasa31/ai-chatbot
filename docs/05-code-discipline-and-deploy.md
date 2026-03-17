# Code Discipline & Deployment

---

## Code Discipline Rules

### Rule #1: Atomic Architecture

```
backend/
├─ core/
│  ├─ __init__.py
│  ├─ db.py (database connection, pgvector setup)
│  ├─ config.py (settings from env vars)
│  ├─ security.py (JWT, bcrypt utilities)
│  └─ models.py (SQLAlchemy base models)
│
├─ auth/ [Feature 1]
│  ├─ routes.py
│  ├─ service.py
│  └─ tests/
│
├─ clients/ [Feature 2]
│  ├─ routes.py
│  ├─ service.py
│  └─ tests/
│
├─ documents/ [Feature 3]
│  ├─ routes.py
│  ├─ service.py
│  ├─ parsers.py
│  └─ tests/
│
├─ embeddings/ [Feature 4]
│  ├─ service.py
│  ├─ worker.py
│  └─ tests/
│
├─ search/ [Feature 5]
│  ├─ routes.py
│  ├─ service.py
│  └─ tests/
│
├─ chat/ [Feature 6]
│  ├─ routes.py
│  ├─ service.py
│  └─ tests/
│
├─ widget/ [Feature 7]
│  ├─ routes.py
│  └─ tests/
│
├─ main.py (FastAPI app, import all routes)
├─ requirements.txt
└─ tests/
   ├─ test_models.py
   └─ conftest.py (pytest fixtures)
```

**RULE:** Each phase = separate module. Don't touch other modules.

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
✅ main.py (only 1 import line: from .module import router)

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

```bash
# Start
git checkout develop
git checkout -b feature/documents

# Work on feature (edit, commit, test locally)
# ...

# Submit for review
git add .
git commit -m "feat(documents): add upload and parsing"
git push origin feature/documents

# Create PR on GitHub
# → I review
# → If approve: merge to main (you)
# → If reject: git reset --hard develop (start over with feedback)
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

# Create develop branch
git checkout -b develop
git push -u origin develop
```

### Per-Phase Workflow

```bash
# 1. Create feature branch from develop
git checkout develop
git pull
git checkout -b feature/documents

# 2. Make changes
# - Create files
# - Edit code
# - Write tests
# - Run pytest locally: pytest tests/ -v

# 3. Commit (atomic commits)
git add backend/documents/
git commit -m "feat(documents): add PDF parsing"
git add tests/test_documents.py
git commit -m "test(documents): add 15 test cases"

# 4. Push
git push -u origin feature/documents

# 5. Create PR on GitHub
# - Title: "feat: document upload and parsing"
# - Description: what changed, why
# - Link: closes #1 (if applicable)

# 6. Get review (me)

# 7a. If APPROVED:
git checkout main
git pull
git merge feature/documents --no-ff  # merge commit
git push

# 7b. If REJECTED:
git reset --hard develop
# Fix issues, repeat from step 2
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
OPENAI_API_KEY=sk-...
JWT_SECRET=<generate random 32-char string>
ENVIRONMENT=production
```

**Step 4: Deploy**
```bash
# Railway auto-deploys from git
# git push main → automatic deployment
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
# Vercel auto-deploys from git
# git push main → automatic build + deploy
```

**Step 5: Test**
```
Visit https://app.yourdomain.com
- Can login
- Can upload document
- Can view chat logs
```

### Widget (CDN)

**embed.js served from:**
```
https://api.yourdomain.com/embed.js
```

**Clients add:**
```html
<script src="https://api.yourdomain.com/embed.js"></script>
<div id="ai-chat-widget"></div>
```

**Test on external page:**
```bash
# Create test.html
<html>
  <script src="https://api.yourdomain.com/embed.js"></script>
  <div id="ai-chat-widget"></div>
</html>

# Open in browser → widget should appear
```

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
  - Copy embed code
  - Create test.html
  - Open in browser
  - Widget appears

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
- API key required for chat
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

**Ready to build!** 🚀
