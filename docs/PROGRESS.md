# Chat9 Development Progress

**Last updated:** 2026-03-20 08:40 UTC  
**Overall status:** ✅ MVP feature-complete, deployed to production

---

## ✅ COMPLETED (2026-03-20)

### Dependencies & Infrastructure
- ✅ **PyPDF2 → pypdf** migration (branch `chore/deps-pypdf2-openai`)
  - `requirements.txt` (root + backend): removed PyPDF2, added pypdf>=4.0.0, openai>=1.70.0
  - `documents/parsers.py`: `from pypdf import PdfReader`
  - `tests/test_documents.py`: updated PdfWriter to pypdf
  - 135 tests passed

### pgvector Migration
- ✅ **Migration `dd643d1a544a`** — Fix vector column type + HNSW index
  - Added `vector Vector(1536)` column to `embeddings` table
  - Backfill: `(metadata->>'vector')::vector` (note: `->>`  not `->`, json→text→vector)
  - HNSW index: `CREATE INDEX USING hnsw (vector vector_cosine_ops)`
  - Ran successfully on Railway prod DB

### Production Deploy (2026-03-20)
- ✅ `main` → `deploy` → Vercel + Railway auto-deployed
- ✅ Forgot password tested end-to-end (email → reset link → login)
- ✅ All features now live at getchat9.live

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
- ✅ **FI-AUTH: Forgot Password** — Full reset flow
  - `POST /auth/forgot-password` (Brevo email, rate limited 3/hour)
  - `POST /auth/reset-password` (token validation, 1h TTL)
  - Frontend pages: `/forgot-password`, `/reset-password`
  - "Forgot password?" link on login page
- ✅ **FI-UI: Sign in button** — Added to landing page navigation

### Infrastructure
- ✅ Vercel `deploy` branch created — decouple commits from deploys
  - `main` = development (no auto-deploy)
  - `deploy` = production (Vercel listens here)
- ✅ `NEXT_PUBLIC_APP_URL` set on Vercel

---

## 📋 NEXT UP

### Widget Testing:
1. **Test FI-EMBED-MVP on real domain** — waiting for domain admin to update embed script

### Backlog (P1–P2):
2. **FI-021** — Background embeddings (async processing)
3. **FI-039** — Daily summary email (Brevo)
4. **FI-040** — Client analytics dashboard
5. **FI-041** — Status page integration (real-time incident awareness)

### Medium-term (P3):
6. **CI/CD pipeline** (GitHub Actions: pytest + ruff + eslint on PR)
7. **Langfuse tracing** (LLM observability)
8. **Per-client system prompt**
9. **Multiple file upload**

---

## 📊 FEATURES LIVE IN PRODUCTION

- ✅ Document upload (PDF, Markdown, Swagger, Text)
- ✅ RAG pipeline (OpenAI text-embedding-3-small + gpt-4o-mini)
- ✅ Hybrid retrieval (vector + keyword fallback)
- ✅ pgvector native search (SQL cosine_distance, HNSW index)
- ✅ Multi-tenant isolation (client_id scoping)
- ✅ Chat widget (embeddable, ~6KB vanilla JS)
- ✅ Zero-config widget embed (public_id + iframe)
- ✅ Dashboard (documents, logs, feedback, analytics)
- ✅ Email verification (Brevo)
- ✅ Forgot password flow (Brevo) — tested end-to-end
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
  deploy → production (Vercel + Railway listen here)
```

---

## ⚠️ KNOWN ISSUES / TECH DEBT

| Issue | Priority | Notes |
|-------|----------|-------|
| FI-EMBED-MVP real-domain test | 🟡 P1 | Waiting for admin to update embed script |
| Static Stats on landing page | 🟡 P2 | Hardcoded, connect real API later |
| No CI/CD pipeline | 🟡 P2 | GitHub Actions needed |
| Footer links hardcoded | 🟢 P3 | Update when docs site ready |

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

_Updated: 2026-03-20 08:40 UTC_
