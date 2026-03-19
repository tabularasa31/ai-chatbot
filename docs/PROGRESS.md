# Chat9 Development Progress

**Last updated:** 2026-03-19 18:11 UTC  
**Overall status:** ✅ MVP feature-complete, pending production deploy

---

## ✅ COMPLETED (2026-03-19 — Full Session)

### Security & Code Quality
- ✅ Rate limiting: `/validate` (20/min), `/search` (30/min), `/chat` (30/min)
- ✅ Input validation: `limit/offset` (1-100, ≥0)
- ✅ `m.feedback` None protection
- ✅ `datetime.utcnow()` → `datetime.now(timezone.utc)` (3 files)
- ✅ Broad exceptions → explicit (crypto.py)
- ✅ Exception chaining: `from None` → `from e`
- ✅ N+1 queries fixed (list_sessions, list_bad_answers)
- ✅ pgvector native search — SQL `<=>` instead of Python cosine loop

### Features
- ✅ **FI-EMBED-MVP** — Zero-config widget embedding (CORS solved via iframe)
  - `public_id` on Client model (ch_xxx format)
  - `/embed.js` public endpoint
  - `/widget/chat` public API (no auth, clientId-based)
  - `/widget` iframe page + ChatWidget component
  - Dashboard shows embed code
  - Migration + backfill script
- ✅ **FI-AUTH: Forgot Password** — Full reset flow
  - `POST /auth/forgot-password` (Brevo email, rate limited 3/hour)
  - `POST /auth/reset-password` (token validation, 1h TTL)
  - Frontend pages: `/forgot-password`, `/reset-password`
  - "Forgot password?" link on login page
- ✅ **FI-UI: Sign in button** — Added to landing page navigation
  - Secondary style (cyan outline)
  - Desktop + mobile hamburger menu
  - Links to `/login`

### Infrastructure
- ✅ Vercel `deploy` branch created — decouple commits from deploys
  - `main` = development (no auto-deploy)
  - `deploy` = production (Vercel listens here)
- ✅ `NEXT_PUBLIC_APP_URL` set on Vercel

### Documentation
- ✅ `GROK-PROJECT-REVIEW.md` — comprehensive project review
- ✅ `LESSONS_2026-03-19.md` — process errors and improvements
- ✅ `BACKLOG_SECURITY-IMPROVEMENTS.md` — security roadmap
- ✅ `BACKLOG_EMBED-PHASE2.md` — embed feature roadmap
- ✅ FI-EMBED full spec (3 reviews: Grok 9/10, DeepSeek 9/10, Claude 8/10)

---

## ⏳ NOT YET IN PRODUCTION

All these are implemented (merged to main), but NOT yet deployed to getchat9.live:

| Feature | Branch/Status | Notes |
|---------|--------------|-------|
| FI-EMBED-MVP (widget) | ✅ merged to main | Needs deploy + test |
| Forgot password | ✅ merged to main | Needs deploy |
| Sign in button | ✅ merged to main | Needs deploy |
| pgvector native search | ✅ merged to main | Needs migration first! |

### ⚠️ DEPLOY ORDER (Important!)

**pgvector migration must run BEFORE code deploys:**

1. Create migration PR:
   - Add `vector Vector(1536)` column to `embeddings`
   - Backfill: `UPDATE embeddings SET vector = (metadata_json->>'vector')::vector`
   - HNSW index: `CREATE INDEX ON embeddings USING hnsw (vector vector_cosine_ops)`
2. Deploy migration to Railway
3. Then merge `main` → `deploy` → Vercel deploys

**Everything else (embed, forgot-password, sign in) can deploy without migration.**

### How to deploy (when ready):

```bash
# Merge main into deploy branch → triggers Vercel
git checkout deploy
git merge main
git push origin deploy
git checkout main
```

---

## 📋 ACTIVE CURSOR PROMPTS

**None queued.** All completed today.

---

## 📋 NEXT UP (Tomorrow)

### Immediate (before public launch):
1. **pgvector migration PR** — prerequisite for search performance
2. **Deploy to production** (merge main → deploy)
3. **Test FI-EMBED-MVP** on a real domain — does widget load? CORS ok?
4. **Test forgot password** end-to-end (email → reset link → login)

### Soon (P2):
5. **FI-EMBED Phase 2** — rate limiting for `/widget/chat`
6. **Per-client system prompt** (each client configures their bot personality)
7. **Multiple file upload**
8. **Soft-delete for documents**

### Medium-term (P3):
9. **CI/CD pipeline** (GitHub Actions: pytest + ruff + eslint on PR)
10. **Langfuse tracing** (LLM observability)
11. **Daily summary email** (FI-039)

---

## 📊 FEATURES COMPLETED (MVP)

- ✅ Document upload (PDF, Markdown, Swagger, Text)
- ✅ RAG pipeline (OpenAI text-embedding-3-small + gpt-4o-mini)
- ✅ Hybrid retrieval (vector + keyword fallback)
- ✅ pgvector native search (SQL cosine_distance)
- ✅ Multi-tenant isolation (client_id scoping)
- ✅ Chat widget (embeddable, ~6KB vanilla JS)
- ✅ Zero-config widget embed (public_id + iframe)
- ✅ Dashboard (documents, logs, feedback, analytics)
- ✅ Email verification (Brevo)
- ✅ Forgot password flow (Brevo)
- ✅ Admin metrics
- ✅ Chat logs with feedback (👍/👎)
- ✅ Bad answers review + training
- ✅ Landing page (getchat9.live)
- ✅ Sign in button on landing page
- ✅ CORS security (whitelist)
- ✅ Rate limiting (chat, search, validate)

---

## 🏗️ INFRASTRUCTURE

```
User → getchat9.live (Vercel, Next.js)
     ↘ ai-chatbot-production-6531.up.railway.app (FastAPI)
       ↘ PostgreSQL 15 + pgvector
       ↘ OpenAI API (embeddings + gpt-4o-mini)
       ↘ Brevo (transactional email)

Git branches:
  main   → development (no auto-deploy)
  deploy → production (Vercel listens here)
```

---

## ⚠️ KNOWN ISSUES / TECH DEBT

| Issue | Priority | Notes |
|-------|----------|-------|
| pgvector migration (create vector column) | 🔴 P1 | Must do before deploying search refactor |
| Static Stats on landing page | 🟡 P2 | Hardcoded, connect real API later |
| Footer links hardcoded | 🟡 P3 | Update when docs site ready |
| No CI/CD pipeline | 🟡 P2 | GitHub Actions needed |

---

## 🔍 CODE REVIEWS RECEIVED (2026-03-19)

| Reviewer | Area | Rating | Key Feedback |
|----------|------|--------|-------------|
| Grok | FI-EMBED spec | 9/10 | Rate limiting, versioning needed |
| DeepSeek | FI-EMBED spec | 9/10 | CSP docs, document.currentScript |
| Claude | FI-EMBED spec | 8/10 | Mobile, migration anti-pattern |
| Grok | Project-wide | 8.5/10 | Architecture solid, chunking/re-ranker needed |

---

## 📚 BACKLOG FILES

| File | Contents |
|------|---------|
| `BACKLOG_PRODUCT.md` | Product features (FI-xxx), RICE scored |
| `BACKLOG_TECH_DEBT.md` | Tech improvements |
| `BACKLOG_SECURITY-IMPROVEMENTS.md` | Security: vectorDB filter, rate limiting, tracing |
| `BACKLOG_EMBED-PHASE2.md` | Widget improvements (rate limiting, mobile, CSP) |
| `BACKLOG_RAG_QUALITY.md` | RAG quality: chunking, re-ranker |
| `BACKLOG_MONETIZATION.md` | Pricing strategy |

---

_Updated: 2026-03-19 18:11 UTC | Session: 07:30–18:11 UTC (~10.5 hours)_
