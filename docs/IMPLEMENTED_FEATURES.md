# Chat9 — implemented features registry

**Purpose:** A single grouped list of **what the product already does**, with pointers to code and APIs. It does **not** replace the full commit/session history — see [`PROGRESS.md`](./PROGRESS.md) for that.

**Last updated:** 2026-03-27 (URL-source page deletion in Knowledge)

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
| Upload / parse | PDF (pypdf), MD, Swagger/OpenAPI | `backend/documents/`, `POST /documents` |
| **FI-009** | Sentence-aware chunking, chunk metadata | `backend/embeddings/service.py` (`chunk_text`), migrations |
| **TD-033** | Per-doc-type chunking: `swagger` 500 chars/0 overlap, `markdown` 700/1, `pdf` 1000/1; `CHUNKING_CONFIG` dict — tune in one place, no client UI | `backend/embeddings/service.py` |
| **FI-021** | Async embeddings: `202 Accepted` immediately, `BackgroundTasks` with own DB session, status `ready → embedding → ready/error`; frontend polls every 2 s | `backend/embeddings/routes.py`, `service.py`, `frontend/app/(app)/knowledge/page.tsx` |
| Embeddings | text-embedding-3-small, pgvector / SQLite test fallback | `backend/embeddings/`, `POST /embeddings/documents/{id}` |
| **FI-032 ph.1** | Document health check (GPT), `health_status`, re-check | `GET`/`POST /documents/{id}/health*`, `docs/qa/FI-032-document-health-check.md` |
| **FI-URL v1** | URL documentation source ingestion: preflight, same-domain crawl, background indexing, refresh, run history, inline source detail, shared client-wide knowledge capacity (files + URL pages), and per-page deletion that persists across refreshes | `backend/documents/url_service.py`, `GET/POST/PATCH/DELETE /documents/sources*`, `DELETE /documents/sources/{source_id}/pages/{document_id}`, `docs/qa/FI-URL-url-sources-v1.md` |

---

## Search & RAG chat

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-008 / FI-019 ext** | Hybrid: pgvector + BM25 + RRF (Postgres); SQLite tests: cosine only | `backend/search/service.py`, `rank-bm25` |
| RAG pipeline | Retrieve → prompt → generate → persist messages | `backend/chat/service.py` `process_chat_message`, `POST /chat` (X-API-Key) |
| **FI-034** | LLM answer validation; fallback on low confidence | `validate_answer()`, `POST /chat/debug` → `validation` |
| **FI-043** | Regex PII redaction before OpenAI; original in `Message.content` | `backend/chat/pii.py` |
| **FI-ESC v1** | L2 escalation: tickets (DB + tenant email), triggers (low similarity, no chunks, human phrase, manual), OpenAI JSON handoff UX, `chat_ended` on `POST /chat` | `backend/escalation/`, `process_chat_message`, `POST /chat/{session_id}/escalate`, JWT `GET/POST /escalations*`, migration `fi_esc_v1` |
| Sessions / logs / feedback | Session list, logs, thumbs, ideal answer, bad answers | `GET /chat/sessions`, logs, feedback, bad-answers |

---

## Widget & public embed

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-EMBED-MVP** | iframe + `public_id`, `/embed.js`, public chat | `GET /embed.js`, `POST /widget/chat`, dashboard embed code |
| **FI-KYC** | `POST /widget/session/init` with optional `identity_token` → `chats.user_context` | `backend/routes/widget.py`, `backend/core/security.py` |
| **FI-ESC (widget)** | Optional `locale` on session init and `/widget/chat`; `POST /widget/escalate` (public); response includes `chat_ended` | `backend/routes/widget.py`, `frontend/app/widget/escalate/route.ts`, `ChatWidget.tsx` |
| **FI-038** | “Powered by Chat9” footer | `frontend/components/ChatWidget.tsx` |
| Widget rate limits | 20/min on `POST /widget/session/init`, `/widget/chat`, `/widget/escalate` | slowapi, `backend/routes/widget.py` |
| **embed.js** | Passes `navigator.language` as `locale` into iframe URL | `backend/static/embed.js` |

---

## Product UI

| ID / area | What shipped | Where |
|-----------|--------------|-------|
| **FI-UI** | Dark brand, navbar, auth pages, post-login transition | `frontend/components/Navbar.tsx`, auth pages, `AuthTransition` |
| **UI-NAV** | Persistent sidebar (icons, active state, Settings/Admin sections); slim fixed navbar (brand + email + logout only) | `frontend/components/Sidebar.tsx`, `frontend/components/Navbar.tsx`, `frontend/app/(app)/layout.tsx` |
| **Knowledge hub** | `/knowledge` (replaces `/documents`): file upload + URL source ingestion, unified indexed sources table with type badges, status, schedule, indexed counts, health, row actions, inline expandable source detail, and per-page deletion for indexed URL pages | `frontend/app/(app)/knowledge/page.tsx` |
| **Code snippets UX** | Inline copy icon on embed / Node.js / debug answer blocks (shared component, light/dark tone) | `frontend/components/ui/code-block-with-copy.tsx` |
| **Agents** | `/settings`: OpenAI API key management (moved from Dashboard); status banners; save/update/remove flow | `frontend/app/(app)/settings/page.tsx` |
| Dashboard, **Knowledge**, Logs, Review, Debug, **Escalations** | Main app sections | `frontend/app/(app)/` |
| **Design system** | Unified card/button/link/input/error style across all app pages; `rounded-xl border border-slate-200`, `bg-violet-600` primary, `text-violet-600` links | All `frontend/app/(app)/**` pages |
| Landing | Marketing page, Sign in | `frontend/app/` (landing routes) |

---

## Security & infrastructure

| Area | What shipped | Where |
|------|--------------|-------|
| Rate limiting | `/validate`, `/search`, `/chat`, widget | `backend/core/limiter.py`, routes |
| CORS | Production allowlist | app config |
| pgvector + HNSW | Native vector column + index | migration `dd643d1a544a`, `embeddings.vector` |
| **FI-026** | GitHub Actions on `main` + `deploy`: backend Ruff + pytest + coverage; frontend ESLint + `next build` | `.github/workflows/ci.yml`, `backend/ruff.toml` |
| Coverage hardening (2026-03-24) | Added high-risk regression tests for escalation state machine, manual escalation endpoint, auth reset flow, and retrieval edge/error paths; stable `/search` OpenAI error contract (`503`) | `tests/test_chat.py`, `tests/test_escalation.py`, `tests/test_auth.py`, `tests/test_search.py`, `tests/pgvector_tests/test_search_pgvector.py`, `backend/search/routes.py` |
| Developer test runbook | Engineer-focused grouped test commands for local/CI runs | `docs/06-developer-test-runbook.md` |
| Deploy | `main` vs `deploy`, Vercel + Railway; promote via PR after green CI | see `PROGRESS.md` → Infrastructure |

---

## CI & quality

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-026** | GitHub Actions on `main` + `deploy`: backend `ruff` + `pytest tests/` + coverage; frontend `eslint` + `next build` | `.github/workflows/ci.yml`, `backend/ruff.toml` |

---

## Related docs

| Document | Use for |
|----------|---------|
| [`PROGRESS.md`](./PROGRESS.md) | Chronology, session context, “what happened when” |
| [`BACKLOG_EMBED-PHASE2.md`](./BACKLOG_EMBED-PHASE2.md) | Widget Phase 2/3 backlog (embed.js hardening, CSP, quotas — after baseline limits) |
| [`BACKLOG_PRODUCT.md`](./BACKLOG_PRODUCT.md) | Queue & RICE; done items marked ~~Done~~ |
| [`README.md`](../README.md) | Runbook, short API overview |
| [`qa/PRODUCT-QA-TEST-PLAN.md`](./qa/PRODUCT-QA-TEST-PLAN.md) | Manual QA (Russian) |
| [`qa/FI-URL-url-sources-v1.md`](./qa/FI-URL-url-sources-v1.md) | URL sources v1 — QA checklist |
| [`qa/FI-ESC-escalation-tickets-qa.md`](./qa/FI-ESC-escalation-tickets-qa.md) | FI-ESC escalation — чеклист для тестировщика |
| [`qa/UI-NAV-sidebar-redesign-qa.md`](./qa/UI-NAV-sidebar-redesign-qa.md) | UI-NAV sidebar redesign — QA checklist |

---

## Maintenance

- After a **major** feature: add a row to the right table and, if needed, a block in `PROGRESS.md`.
- Small bugfixes **do not** need an entry here — only user-visible capabilities.
