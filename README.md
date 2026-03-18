# AI Chatbot Platform

**AI-powered chatbot platform. Upload your docs, get an API key, embed a chat widget on any website.**

![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104-green.svg)
![Next.js](https://img.shields.io/badge/Next.js-14-black.svg)
![Railway](https://img.shields.io/badge/Railway-Deploy-0B0D0E.svg)
![Vercel](https://img.shields.io/badge/Vercel-Deploy-000000.svg)

---

## Live Demo

| Resource | URL |
|----------|-----|
| **Dashboard** | https://ai-chatbot-three-lovat-32.vercel.app |
| **API Docs (Swagger)** | https://ai-chatbot-production-6531.up.railway.app/docs |

**Embed example** — add this to any website:

```html
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js" data-api-key="YOUR_API_KEY"></script>
```

---

## Features

- **Multi-tenant** — one platform, many clients
- **Document upload** — PDF, Markdown, Swagger (JSON/YAML)
- **RAG pipeline** — OpenAI embeddings + GPT-3.5-turbo
- **REST API** — JWT auth for dashboard, API key for chat
- **Embeddable JS widget** — chat bubble on any site
- **Dashboard** — Next.js app for managing docs and API keys

---

## Architecture

```
User → Vercel (Next.js) → Railway (FastAPI) → PostgreSQL + pgvector
                                               → OpenAI API
```

---

## Quick Start (Self-hosted)

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
| `DATABASE_URL` | Backend (.env) | PostgreSQL connection string |
| `JWT_SECRET` | Backend (.env) | Secret for JWT (min 32 chars) |
| `OPENAI_API_KEY` | Backend (.env) | OpenAI API key |
| `ENVIRONMENT` | Backend (.env) | `development` or `production` |
| `NEXT_PUBLIC_API_URL` | Frontend (.env.local) | Backend API base URL |

---

## API Overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Register new user |
| POST | `/auth/login` | Login, get JWT |
| POST | `/clients` | Create client (JWT) |
| GET | `/clients/me` | Get current client (JWT) |
| GET | `/clients/validate/{api_key}` | Validate API key (public) |
| POST | `/documents` | Upload document (JWT) |
| GET | `/documents` | List documents (JWT) |
| GET | `/documents/{id}` | Get document detail (JWT) |
| DELETE | `/documents/{id}` | Delete document (JWT) |
| POST | `/embeddings/documents/{id}` | Create embeddings (JWT) |
| POST | `/chat` | RAG chat (X-API-Key) |
| POST | `/search` | Vector search (JWT) |
| GET | `/health` | Health check |
| GET | `/embed.js` | Embed widget script |

---

## Embed Widget

Add the chat widget to any website with two lines:

```html
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js" data-api-key="YOUR_API_KEY"></script>
```

Replace `YOUR_API_KEY` with your client API key from the dashboard. The widget loads a chat bubble; users can ask questions and get RAG-powered answers from your documents.

---

## Tech Stack

| Layer | Technology | Hosting |
|-------|------------|---------|
| Backend | FastAPI + Python 3.11 | Railway |
| Database | PostgreSQL 15 + pgvector | Railway |
| AI | OpenAI text-embedding-3-small + GPT-3.5-turbo | OpenAI API |
| Frontend | Next.js 14 + TailwindCSS | Vercel |
| Widget | Vanilla JS | Railway (served as `/embed.js`) |

---

## License

MIT
