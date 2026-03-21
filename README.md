# Chat9 — Your Support Mate, Always On

**AI-powered support bot platform. Upload your docs, get a chat widget, your customers get instant answers — 24/7.**

![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104-green.svg)
![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)
![Railway](https://img.shields.io/badge/Railway-Deploy-0B0D0E.svg)
![Vercel](https://img.shields.io/badge/Vercel-Deploy-000000.svg)

---

## Live

| Resource | URL |
|----------|-----|
| **Dashboard** | https://getchat9.live |
| **API Docs (Swagger)** | https://ai-chatbot-production-6531.up.railway.app/docs |

**Embed on any website:**

```html
<div id="ai-chat-widget" data-api-key="YOUR_API_KEY"></div>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js"></script>
```

---

## Features

- **Multi-tenant** — one platform, many clients, full data isolation
- **Document upload** — PDF, Markdown, Swagger (JSON/YAML), plain text
- **RAG pipeline** — OpenAI embeddings (`text-embedding-3-small`) + `gpt-4o-mini`
- **Hybrid retrieval** — PostgreSQL: pgvector cosine + BM25 (`rank-bm25`) merged with RRF; SQLite/tests: Python cosine only
- **Embeddable JS widget** — chat bubble on any site (~6KB, no dependencies)
- **Dashboard** — Next.js: docs manager, chat logs, feedback, admin metrics
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
docker run --name postgres-chat9 \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=ai_chatbot \
  -p 5432:5432 \
  -d pgvector/pgvector:pg15
```

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

## Environment Variables

| Variable | Layer | Description |
|----------|-------|-------------|
| `DATABASE_URL` | Backend | PostgreSQL connection string |
| `JWT_SECRET` | Backend | Secret for JWT (min 32 chars) |
| `ENVIRONMENT` | Backend | `development` or `production` |
| `ENCRYPTION_KEY` | Backend | Fernet key for OpenAI key encryption |
| `FRONTEND_URL` | Backend | Frontend URL (e.g. https://getchat9.live) |
| `EMAIL_FROM` | Backend | Sender email (e.g. no-reply@getchat9.live) |
| `BREVO_API_KEY` | Backend | Brevo HTTP API key for transactional email |
| `NEXT_PUBLIC_API_URL` | Frontend | Backend API base URL |

> Note: Each client provides their own OpenAI API key in the dashboard. The platform does not require a global `OPENAI_API_KEY`.

---

## API Overview

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Register new user (sends verification email) |
| POST | `/auth/login` | Login, get JWT |
| POST | `/auth/verify-email` | Verify email with one-time token |

### Clients
| Method | Path | Description |
|--------|------|-------------|
| POST | `/clients` | Create client (JWT, verified only) |
| GET | `/clients/me` | Get current client info (JWT) |

### Documents
| Method | Path | Description |
|--------|------|-------------|
| POST | `/documents` | Upload document (JWT, verified only) |
| GET | `/documents` | List documents (JWT) |
| DELETE | `/documents/{id}` | Delete document (JWT) |
| POST | `/embeddings/documents/{id}` | Re-trigger embeddings generation (force re-index, JWT) |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | RAG chat (X-API-Key header) |
| GET | `/chat/sessions` | List chat sessions (JWT) |
| GET | `/chat/logs/session/{id}` | Full session log (JWT) |
| GET | `/chat/bad-answers` | Answers marked 👎 (JWT) |
| POST | `/chat/messages/{id}/feedback` | Set 👍/👎 + ideal answer (JWT) |
| POST | `/chat/debug` | Debug retrieval (JWT) |

### Admin
| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/metrics/summary` | Platform-wide stats (admin only) |
| GET | `/admin/metrics/clients` | Per-client stats (admin only) |

### Other
| Method | Path | Description |
|--------|------|-------------|
| POST | `/search` | Vector search (JWT) |
| GET | `/health` | Health check |
| GET | `/embed.js` | Widget script |

---

## Embed Widget

```html
<div id="ai-chat-widget" data-api-key="YOUR_API_KEY"></div>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js"></script>
```

Get your API key from the Dashboard. The widget loads a floating chat bubble; users can ask questions and get answers from your documents.

---

## Tech Stack

| Layer | Technology | Hosting |
|-------|-----------|---------|
| Backend | FastAPI + Python 3.11 | Railway |
| Database | PostgreSQL 15 + pgvector | Railway |
| AI | OpenAI text-embedding-3-small + gpt-4o-mini | OpenAI API |
| Frontend | Next.js 14 + TailwindCSS | Vercel |
| Widget | Vanilla JS | Railway (as /embed.js) |
| Email | Brevo HTTP API | Brevo |

---

## License

MIT
