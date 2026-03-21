# Chat9 — implemented features registry

**Purpose:** A single grouped list of **what the product already does**, with pointers to code and APIs. It does **not** replace the full commit/session history — see [`PROGRESS.md`](./PROGRESS.md) for that.

**Last updated:** 2026-03-21

---

## Authentication & account

| ID / area | What shipped | Code / API |
|-----------|--------------|--------------|
| Registration, JWT login | Users, JWT sessions | `backend/auth/`, `POST /auth/register`, `POST /auth/login` |
| Email verification | Brevo email, token | `POST /auth/verify-email`, verify UI |
| Forgot password | Email request + token reset (1h TTL), rate limit | `POST /auth/forgot-password`, `POST /auth/reset-password` |
| Admin flag | `is_admin` for admin metrics | `User.is_admin`, `GET /admin/metrics/*` |

---

## Client (tenant) & settings

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| Client per user | API key, `public_id`, data isolation | `backend/models.py` `Client`, `POST /clients`, `GET /clients/me` |
| Per-client OpenAI key | Encrypted in DB, PATCH client | `PATCH /clients/me`, `backend/core/crypto.py` |
| **FI-DISC v1** | Single tenant-wide response detail level (detailed / standard / corporate), hard limits in prompt | `GET`/`PUT /clients/me/disclosure`, `backend/disclosure_config.py`, `backend/chat/service.py`, UI `/settings/disclosure` |
| **FI-KYC** | Widget signing secret, rotation | `POST /clients/me/kyc/secret`, `status`, `rotate`; UI `/settings/widget` |

---

## Documents & embeddings

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| Upload / parse | PDF (pypdf), MD, Swagger, text | `backend/documents/`, `POST /documents` |
| **FI-009** | Sentence-aware chunking, chunk metadata | `backend/embeddings/` (chunking), migrations |
| Embeddings | text-embedding-3-small, pgvector / SQLite test fallback | `backend/embeddings/`, `POST /embeddings/documents/{id}` |
| **FI-032 ph.1** | Document health check (GPT), `health_status`, re-check | `GET`/`POST /documents/{id}/health*`, `docs/qa/FI-032-document-health-check.md` |

---

## Search & RAG chat

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-008 / FI-019 ext** | Hybrid: pgvector + BM25 + RRF (Postgres); SQLite tests: cosine only | `backend/search/service.py`, `rank-bm25` |
| RAG pipeline | Retrieve → prompt → generate → persist messages | `backend/chat/service.py` `process_chat_message`, `POST /chat` (X-API-Key) |
| **FI-034** | LLM answer validation; fallback on low confidence | `validate_answer()`, `POST /chat/debug` → `validation` |
| **FI-043** | Regex PII redaction before OpenAI; original in `Message.content` | `backend/chat/pii.py` |
| Sessions / logs / feedback | Session list, logs, thumbs, ideal answer, bad answers | `GET /chat/sessions`, logs, feedback, bad-answers |

---

## Widget & public embed

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-EMBED-MVP** | iframe + `public_id`, `/embed.js`, public chat | `GET /embed.js`, `POST /widget/chat`, dashboard embed code |
| **FI-KYC** | `POST /widget/session/init` with optional `identity_token` → `chats.user_context` | `backend/routes/widget.py`, `backend/core/security.py` |
| **FI-038** | “Powered by Chat9” footer | `frontend/components/ChatWidget.tsx` |
| Widget rate limits | 20/min on `POST /widget/session/init` and `POST /widget/chat` | slowapi, `backend/routes/widget.py` |

---

## Product UI

| ID / area | What shipped | Where |
|-----------|--------------|-------|
| **FI-UI** | Dark brand, navbar, auth pages, post-login transition | `frontend/components/Navbar.tsx`, auth pages, `AuthTransition` |
| Dashboard, Documents, Logs, Review, Debug | Main app sections | `frontend/app/(app)/` |
| Landing | Marketing page, Sign in | `frontend/app/` (landing routes) |

---

## Security & infrastructure

| Area | What shipped | Where |
|------|--------------|-------|
| Rate limiting | `/validate`, `/search`, `/chat`, widget | `backend/core/limiter.py`, routes |
| CORS | Production allowlist | app config |
| pgvector + HNSW | Native vector column + index | migration `dd643d1a544a`, `embeddings.vector` |
| Deploy | `main` vs `deploy`, Vercel + Railway | see `PROGRESS.md` → Infrastructure |

---

## Related docs

| Document | Use for |
|----------|---------|
| [`PROGRESS.md`](./PROGRESS.md) | Chronology, session context, “what happened when” |
| [`BACKLOG_EMBED-PHASE2.md`](./BACKLOG_EMBED-PHASE2.md) | Widget Phase 2/3 backlog (embed.js hardening, CSP, quotas — after baseline limits) |
| [`BACKLOG_PRODUCT.md`](./BACKLOG_PRODUCT.md) | Queue & RICE; done items marked ~~Done~~ |
| [`README.md`](../README.md) | Runbook, short API overview |
| [`qa/PRODUCT-QA-TEST-PLAN.md`](./qa/PRODUCT-QA-TEST-PLAN.md) | Manual QA (Russian) |

---

## Maintenance

- After a **major** feature: add a row to the right table and, if needed, a block in `PROGRESS.md`.
- Small bugfixes **do not** need an entry here — only user-visible capabilities.
