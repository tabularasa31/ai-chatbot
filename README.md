# Chat9 — Your Support Mate, Always On

**AI-powered support bot platform. Upload your docs, get a chat widget, your customers get instant answers — 24/7.**

![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)
![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)
![Railway](https://img.shields.io/badge/Railway-Deploy-0B0D0E.svg)
![Vercel](https://img.shields.io/badge/Vercel-Deploy-000000.svg)

---

## Live

| Resource | URL |
|----------|-----|
| **Dashboard** | https://getchat9.live |
| **API Docs (Swagger)** | https://ai-chatbot-production-6531.up.railway.app/docs |

**Embed on any website** (use the snippet from your Dashboard — it matches your `public_id` / URLs):

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js?clientId=ch_YOUR_PUBLIC_ID"></script>
```

`clientId` is your client **`public_id`** (`ch_…`) from the Dashboard. If the frontend and API share the same origin (self‑hosted), you can omit `Chat9Config` and use a single script tag with `?clientId=…` only.

---

## Features

- **Multi-tenant** — one platform, many clients, full data isolation
- **Document upload** — PDF, Markdown, Swagger/OpenAPI (JSON/YAML), Word (DOCX/DOC), plain text (TXT)
- **URL knowledge sources** — add a documentation website URL, crawl up to 50 same-domain pages, refresh on demand
- **Structured OpenAPI ingestion** — operation-aware indexing for Swagger/OpenAPI files and structured URL sources instead of treating specs as raw text
- **RAG pipeline** — OpenAI embeddings (`text-embedding-3-small`) + `gpt-5-mini` for answers, with lightweight `gpt-4o-mini` guard/validation classifiers
- **Hybrid retrieval** — PostgreSQL: pgvector cosine candidate acquisition + BM25 (`rank-bm25`) + RRF + reranking; SQLite/tests: Python cosine candidate acquisition followed by the same downstream lexical/ranking orchestration
- **Retrieval reliability policy** — canonical reliability includes overlap/contradiction evidence; a single contradiction fact stays evidence-only, while corroborated contradiction caps to `low`
- **Controlled clarification** — chat/widget replies can return `answer`, `clarification`, or `partial_with_clarification` with structured clarification payloads
- **Localized default greeting** — new empty chats can start with a product-aware greeting localized from locale hints before the first real question
- **Embeddable widget** — vanilla loader (`/embed.js`) + iframe UI on Next.js (`/widget`), no dependencies on the host page
- **Response controls (FI-DISC v1)** — tenant-wide answer detail level (Detailed / Standard / Corporate); dashboard **Response controls**; `GET`/`PUT /clients/me/disclosure`
- **Optional identified sessions (FI-KYC)** — HMAC-signed identity token + `POST /widget/session/init`; dashboard **Widget API** page for signing secrets
- **Gap Analyzer** — bounded docs-gap + user-signal backlog with Mode A/Mode B pipelines, linking/dedupe, archive lifecycle, draft generation, weekly reclustering, and lightweight badge summary endpoint
- **Dashboard** — Next.js: API key + embed snippet, **Knowledge hub** (`/knowledge`) for files and URL sources, **Agents / Settings**, chat logs, feedback, escalations, admin metrics
- **Chat logs** — inbox-style view of all conversations
- **Feedback loop** — 👍/👎 on answers + ideal answer + review bad answers
- **Email verification** — signup link via Brevo HTTP API
- **Admin metrics** — platform-wide and per-client stats (admin role)
- **Per-client OpenAI key** — encrypted at rest, client controls AI costs

---

## Architecture

```
User → Vercel (Next.js) → Railway (FastAPI) → PostgreSQL + pgvector → OpenAI API
                                                                    ↘ Brevo (email)
```

---

## Quick Start (Self-hosted)

### Prerequisites

- Python 3.11+
- PostgreSQL 15 + pgvector extension (Docker recommended)
- Node.js 18+

### Database

```bash
docker compose up -d db
```

The local `docker-compose.yml` provides a PostgreSQL + pgvector instance for development and pgvector integration tests.

### Backend

```bash
git clone https://github.com/tabularasa31/ai-chatbot
cd ai-chatbot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
alembic upgrade head
uvicorn backend.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # fill in API URL
npm run dev
```

---

## CI (GitHub Actions)

On every **push** to **`main`** and every **pull request** targeting **`main`**, GitHub runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml): **Ruff** + **pytest** for the backend (from the repo root, suite in `tests/`) and **ESLint** + **`next build`** for the frontend.

For grouped developer-focused test commands (P0 smoke, auth reset, escalation, RAG edge cases, pgvector), see [`docs/06-developer-test-runbook.md`](docs/06-developer-test-runbook.md).

**Local checks** (after `pip install -r requirements.txt`):

```bash
ruff check backend
make smoke
make test-sqlite

# With Docker Postgres (pgvector integration):
make test-pgvector
```

### Full local test run + coverage

Run the full suite (SQLite tests + pgvector tests) and show combined coverage at the end:

```bash
make test
make coverage-all
```

### pgvector test credentials (local)

`tests/pgvector_tests/` connects to Postgres using `PG_HOST/PG_PORT/PG_USER/PG_PASSWORD`.
Defaults match [`docker-compose.yml`](docker-compose.yml): `postgres` / `password` on `chatbot`.

If you still have an older data volume created with a different role (e.g. `user`), either run `docker compose down -v` and recreate, or override:

```bash
PG_USER=user PG_PASSWORD=password pytest -m pgvector tests/pgvector_tests/ -q
```

---

## Environment Variables

| Variable | Layer | Description |
|----------|-------|-------------|
| `DATABASE_URL` | Backend | PostgreSQL connection string |
| `JWT_SECRET` | Backend | Secret for JWT (min 32 chars) |
| `EVAL_JWT_SECRET` | Backend | Separate secret for internal `/eval/*` tester JWT (min 32 chars) |
| `ENVIRONMENT` | Backend | `development` or `production` |
| `ENCRYPTION_KEY` | Backend | Fernet key for OpenAI key encryption |
| `FRONTEND_URL` | Backend | Frontend URL (e.g. https://getchat9.live) |
| `AUTH_COOKIE_DOMAIN` | Backend | Parent cookie domain for same-site auth (e.g. `.getchat9.live`). Frontend and API must share this parent — otherwise the cookie is not sent (e.g. on `*.vercel.app` previews). |
| `AUTH_COOKIE_SAMESITE` | Backend | Auth cookie SameSite policy (`lax` for same-site API) |
| `AUTH_COOKIE_SECURE` | Backend | Override auth cookie `Secure`; set `true` in production |
| `CORS_ALLOWED_ORIGINS` | Backend | Allowed dashboard origins (e.g. `https://getchat9.live`) |
| `EMAIL_FROM` | Backend | Sender email (e.g. no-reply@getchat9.live) |
| `BREVO_API_KEY` | Backend | Brevo HTTP API key for transactional email |
| `NEXT_PUBLIC_API_URL` | Frontend | Backend API base URL (production: `https://api.getchat9.live`) |

> Note: Each client provides their own OpenAI API key in the dashboard. The platform does not require a global `OPENAI_API_KEY`.

---

## API Overview

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Register new user (sends verification email) |
| POST | `/auth/login` | Login, get JWT |
| POST | `/auth/verify-email` | Verify email with one-time token and provision the user's client/workspace |

### Tenants
| Method | Path | Description |
|--------|------|-------------|
| POST | `/tenants` | Create tenant manually; the normal signup flow provisions it on `/auth/verify-email` |
| GET | `/tenants/me` | Get current tenant info (JWT) |
| PATCH | `/tenants/me` | Update tenant name / OpenAI key (JWT, verified) |
| GET | `/tenants/me/privacy` | Tenant privacy / PII-redaction config (JWT) |
| PUT | `/tenants/me/privacy` | Update privacy config (JWT, verified) |
| GET | `/tenants/me/support-settings` | Support / escalation routing (JWT) |
| PUT | `/tenants/me/support-settings` | Update support settings (JWT, verified) |
| POST | `/tenants/me/kyc/secret` | Generate KYC signing secret (shown once) (JWT, verified) |
| GET | `/tenants/me/kyc/status` | KYC secret metadata (JWT) |
| POST | `/tenants/me/kyc/rotate` | Rotate the KYC signing secret (JWT, verified) |
| GET | `/tenants/validate/{api_key}` | Public tenant lookup by API key (rate-limited) |

### Bots
| Method | Path | Description |
|--------|------|-------------|
| GET | `/bots` | List bots for the current tenant (JWT) |
| POST | `/bots` | Create bot (JWT, verified) |
| GET | `/bots/{bot_id}` | Bot detail (JWT) |
| PATCH | `/bots/{bot_id}` | Update bot (JWT, verified) |
| DELETE | `/bots/{bot_id}` | Delete bot (JWT, verified) |
| GET | `/bots/{bot_id}/disclosure` | Response detail level: `detailed` \| `standard` \| `corporate` (JWT) |
| PUT | `/bots/{bot_id}/disclosure` | Update bot disclosure config (JWT, verified) |

### Documents
| Method | Path | Description |
|--------|------|-------------|
| POST | `/documents` | Upload document (JWT, verified only) |
| GET | `/documents` | List documents (JWT) |
| GET | `/documents/sources` | List Knowledge sources: uploaded files + URL sources (JWT) |
| POST | `/documents/sources/url` | Create URL source and start background indexing (JWT, verified only) |
| GET | `/documents/sources/{source_id}` | Get URL source detail: recent runs + indexed pages (JWT) |
| PATCH | `/documents/sources/{source_id}` | Update URL source name, schedule, exclusions (JWT, verified only) |
| POST | `/documents/sources/{source_id}/refresh` | Re-crawl a URL source on demand (JWT, verified only) |
| DELETE | `/documents/sources/{source_id}` | Delete a URL source and its indexed pages (JWT) |
| DELETE | `/documents/sources/{source_id}/pages/{document_id}` | Delete one indexed URL-derived page and persist manual exclusion for later refreshes (JWT) |
| DELETE | `/documents/{id}` | Delete document (JWT) |
| POST | `/embeddings/documents/{id}` | Re-trigger embeddings generation (force re-index, JWT) |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | RAG chat (X-API-Key header); returns canonical `text`, `message_type`, optional `clarification`, legacy `answer`, and `chat_ended`; optional header `X-Browser-Locale` |
| POST | `/chat/{session_id}/escalate` | Manual escalation / “not helpful” path (X-API-Key); JSON body: `user_note`, `trigger` (`user_request` or `answer_rejected`) |
| GET | `/chat/sessions` | List chat sessions (JWT) |
| GET | `/chat/logs/session/{id}` | Full session log (JWT) |
| GET | `/chat/bad-answers` | Answers marked 👎 (JWT) |
| POST | `/chat/messages/{id}/feedback` | Set 👍/👎 + ideal answer (JWT) |
| POST | `/chat/debug?bot_id=ch_...` | Debug retrieval for a specific bot (JWT) |

### Gap Analyzer
| Method | Path | Description |
|--------|------|-------------|
| GET | `/gap-analyzer` | Full Gap Analyzer dashboard payload: `summary`, `mode_a_items`, `mode_b_items` (JWT, verified) |
| GET | `/gap-analyzer/summary` | Lightweight summary payload for navigation badge reads (JWT, verified) |
| POST | `/gap-analyzer/recalculate` | Enqueue Mode A / Mode B recalculation (`mode_a`, `mode_b`, `both`) and return orchestration status (`202`) |
| POST | `/gap-analyzer/{source}/{gap_id}/dismiss` | Dismiss a Mode A topic or Mode B cluster (JWT, verified) |
| POST | `/gap-analyzer/{source}/{gap_id}/reactivate` | Reactivate a dismissed or inactive gap item (JWT, verified) |
| POST | `/gap-analyzer/{source}/{gap_id}/draft` | Generate transient draft markdown for a gap item (JWT, verified) |

### Escalations (FI-ESC)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/escalations` | List support tickets for the tenant (JWT); optional query `status` = `open`, `in_progress`, or `resolved` |
| GET | `/escalations/{id}` | Ticket detail (JWT) |
| POST | `/escalations/{id}/resolve` | Mark resolved with `resolution_text` (JWT) |

### Knowledge
| Method | Path | Description |
|--------|------|-------------|
| GET | `/knowledge/profile` | Tenant knowledge profile (product name, topics, glossary, aliases) (JWT) |
| PATCH | `/knowledge/profile` | Update knowledge profile (JWT, verified) |
| GET | `/knowledge/faq` | List FAQ candidates extracted from docs/logs (JWT) |
| POST | `/knowledge/faq` | Create FAQ candidate (JWT, verified) |
| PATCH | `/knowledge/faq/{faq_id}` | Update / approve a FAQ candidate (JWT, verified) |
| DELETE | `/knowledge/faq/{faq_id}` | Delete FAQ candidate (JWT) |
| POST | `/knowledge/faq/approve-all` | Bulk-approve pending FAQ candidates (JWT, verified) |

### Admin
| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/metrics/summary` | Platform-wide stats (admin only) |
| GET | `/admin/metrics/tenants` | Per-tenant stats (admin only) |
| GET | `/admin/privacy/pii-events` | PII redaction / access audit log (admin only) |
| DELETE | `/admin/privacy/pii-events/retention` | Purge expired PII-event rows (admin only) |

### Other
| Method | Path | Description |
|--------|------|-------------|
| POST | `/search` | Vector search (JWT); returns `503` when OpenAI is unavailable |
| GET | `/health` | Health check |
| GET | `/embed.js` | Widget script |
| POST | `/widget/session/init` | Public widget session bootstrap; optional identified mode via signed `identity_token` |
| POST | `/widget/chat` | Public widget chat by `clientId`; returns canonical `text`, `message_type`, optional `clarification`, legacy `response`, and `chat_ended` |
| POST | `/widget/escalate` | Public widget manual escalation |

### Internal manual QA (Eval)

Internal testers only; separate **`EVAL_JWT_SECRET`** (not dashboard JWT). See **`docs/04-features.md`** §11.

**`bot_id`** in eval is the same string as **`clientId`** in the widget embed URL (`embed.js?clientId=ch_…`), i.e. dashboard **public id** — **not** the secret `api_key`.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/eval/login` | Tester username/password → eval `access_token` |
| POST | `/eval/sessions` | Create eval session (`bot_id` = client `public_id` / widget `clientId`); same readiness rules as `/widget/chat` |
| POST | `/eval/sessions/{id}/results` | Append pass/fail result (snapshot question + answer) |
| GET | `/eval/sessions/{id}/results` | List results (owner session only) |

Frontend: **`/eval/login`**, **`/eval/chat?bot_id=ch_…`**. CLI: **`scripts/create_tester.py`**.

---

## Embed Widget

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js?clientId=ch_YOUR_PUBLIC_ID"></script>
```

Copy the exact snippet from the Dashboard (it fills in your `public_id` and URLs). The loader adds a floating iframe; users chat against your uploaded documents via `POST /widget/chat` (optional query `locale`; response includes `chat_ended`, structured `message_type`, and optional clarification payload). Optional identified sessions: `POST /widget/session/init` with `locale` and optional signed `identity_token`. Manual escalation from the widget UI uses `POST /widget/escalate` (proxied on the Next app as `/widget/escalate`).

---

## Gap Analyzer at a glance

Gap Analyzer is the operator-facing backlog for documentation gaps and repeated user pain.

- **Mode A** scans the indexed tenant corpus for under-covered documentation topics with deterministic sampling, extraction hashing, and dismissal persistence
- **Mode B** clusters low-confidence / fallback / rejected / escalated / thumbs-down user questions into reusable product gaps
- linked active Mode A + Mode B pairs dedupe in the dashboard with Mode B as the primary item
- archive views stay source-specific; older archived Mode B items can age into an explicit `inactive` bucket
- manual recalc and chat-side follow-ups now run through a durable DB-backed Gap Analyzer job queue with retryable orchestration state

---

## Tech Stack

| Layer | Technology | Hosting |
|-------|-----------|---------|
| Backend | FastAPI + Python 3.11 | Railway |
| Database | PostgreSQL 15 + pgvector | Railway |
| AI | OpenAI `text-embedding-3-small`, `gpt-5-mini`, lightweight `gpt-4o-mini` guards | OpenAI API |
| Frontend | Next.js 14 + TailwindCSS | Vercel |
| Widget | Vanilla loader + Next.js `/widget` (iframe) | Vercel + Railway (`/embed.js`) |
| Email | Brevo HTTP API | Brevo |

---

## License

MIT
