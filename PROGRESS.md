# AI Chatbot Platform — Development Progress

**Last Updated:** 2026-03-18 12:05 UTC  
**Timeline:** Week 1 of 4 (MVP Sprint)  
**Status:** ALL 11 PHASES COMPLETE ✅ | Full Stack LIVE + Secured 🔐🚀 | RAG Quality Sprints IN PROGRESS 🧠

---

## 🎯 Project Overview

**Platform:** AI Business Chat SaaS  
**Repository:** https://github.com/tabularasa31/ai-chatbot (private)  
**Owner:** Elina (tabularasa31)  
**Architecture:** Multi-tenant (one app, many clients)  
**Deployment:** Railway (backend) + Vercel (frontend)

---

## ✅ Completed Phases

### Phase 1: Database Schema & Models (2026-03-17)

**Status:** ✅ MERGED to main

**What was done:**
- 7 SQLAlchemy models created:
  - `User` (email, password_hash, timestamps)
  - `Client` (user_id FK, name, api_key, settings)
  - `Document` (client_id FK, filename, file_type, parsed_text, status)
  - `Embedding` (document_id FK, chunk_text, vector(1536))
  - `Chat` (client_id FK, session_id)
  - `Message` (chat_id FK, role, content, sources)
  - 4 Enum types (DocumentType, DocumentStatus, MessageRole, MessageFeedback)

- Database infrastructure:
  - PostgreSQL 15 + pgvector extension (Docker container)
  - SQLAlchemy ORM with proper relationships
  - Alembic migrations (autogenerate enabled)
  - Connection pooling configured (pool_size=10, max_overflow=20)

- Core utilities:
  - `backend/core/config.py` — Pydantic BaseSettings from .env
  - `backend/core/db.py` — Engine + SessionLocal + get_db()
  - `backend/core/security.py` — hash_password, verify_password, create_access_token

- Testing:
  - 15 test cases (all passing)
  - 80%+ code coverage
  - SQLite in-memory for tests
  - Cascade delete relationships tested

**Files:**
- `backend/models.py` (368 lines)
- `backend/core/__init__.py`, `config.py`, `db.py`, `security.py`
- `backend/migrations/env.py` (Alembic configured)
- `tests/test_models.py` (15 test cases)
- `tests/conftest.py` (pytest fixtures)
- `.gitignore` (Python/IDE/OS)

**PR:** #1 — Merged ✅  
**Commit:** `feat(database): add SQLAlchemy models, Alembic migrations, and 15 test cases`

---

### Phase 2: User Authentication with JWT (2026-03-17)

**Status:** ✅ MERGED to main

**What was done:**
- Authentication endpoints:
  - `POST /auth/register` — Create user account
    - Email validation (EmailStr from Pydantic)
    - Password strength: min 8 chars, 1 uppercase, 1 number, 1 special char
    - bcrypt hashing (12 rounds)
    - Check duplicate email (409 Conflict)
    - Return JWT token + user info
  
  - `POST /auth/login` — User login
    - Verify email + password
    - Generate JWT token (24h expiration)
    - Return token + expires_in (seconds)
  
  - `GET /auth/me` — Protected endpoint
    - Requires JWT token in Authorization header
    - Return current user info (id, email, created_at)

- JWT Infrastructure:
  - Initially: custom JWTMiddleware + dependency
  - After refactor (Phase 4): pure FastAPI dependency injection only
  - `get_current_user()` now the single source of truth for authn
  - Token format: HS256 with SECRET from .env
  - Expiration: 24 hours

- Security:
  - Password validation (regex + Pydantic validators)
  - bcrypt password hashing
  - JWT signature verification
  - 401 errors on unauthorized access

- Testing:
  - 13 test cases (all passing)
  - ~94% code coverage
  - Tests: registration, login, password validation, JWT protection, edge cases

**Files:**
- `backend/auth/__init__.py`
- `backend/auth/schemas.py` (Pydantic models: RegisterRequest, LoginRequest, AuthResponse, etc.)
- `backend/auth/service.py` (Business logic: register_user, authenticate_user)
- `backend/auth/routes.py` (FastAPI endpoints: /auth/register, /auth/login, /auth/me)
- `backend/auth/middleware.py` (FROZEN: get_current_user dependency only)
- `backend/main.py` (FastAPI app with CORS, routers, /health endpoint)
- `tests/test_auth.py` (13 test cases)

**PR:** #2 — Merged ✅  
**Commit:** `feat(auth): add user authentication with JWT`

---

### Phase 3: Client Management API (2026-03-18)

**Status:** ✅ MERGED to main

**What was done:**
- Client management endpoints:
  - `POST /clients` — create a client for the current user
    - Exactly one client per user (409 if duplicate)
    - Auto-generate 32-char API key (`secrets.token_hex(16)`)
  - `GET /clients/me` — fetch own client
  - `GET /clients/{id}` — fetch specific client (ownership enforced)
  - `DELETE /clients/{id}` — delete client (cascade to documents via FK)
  - `GET /clients/validate/{api_key}` — public endpoint to validate API key

- Security:
  - All non-public routes protected via `Depends(get_current_user)`
  - Strict ownership checks: users see only their own clients

- Testing:
  - 12 test cases (all passing)
  - Scenarios: creation, duplicate prevention, unauthorized access, ownership, validation

**Files:**
- `backend/clients/__init__.py`
- `backend/clients/schemas.py`
- `backend/clients/service.py`
- `backend/clients/routes.py`
- `tests/test_clients.py`

**PR:** #3 — Merged ✅  
**Commit:** `feat(clients): add client management API`

---

### Phase 4: Document Upload & Parsing (2026-03-18)

**Status:** ✅ MERGED to main

**What was done:**
- Document endpoints:
  - `POST /documents` — upload document (multipart/form-data)
    - Supported types: PDF (`.pdf`), Markdown (`.md`), Swagger/OpenAPI (`.json`, `.yaml`, `.yml`)
    - Max file size: 50MB
    - Status: `processing` → `ready` or `error`
  - `GET /documents` — list documents for current client's workspace
  - `GET /documents/{id}` — get single document + parsed_text preview
  - `DELETE /documents/{id}` — delete document (cascade deletes embeddings)

- Parsing layer (`backend/documents/parsers.py`):
  - `parse_pdf` — uses PyPDF2 to extract text from all pages
  - `parse_markdown` — decodes bytes to UTF-8 and returns markdown text
  - `parse_swagger` — parses JSON/YAML and renders human-readable API summary

- Auth refactor (part of this phase):
  - Replaced JWTMiddleware with pure dependency-based `get_current_user`
  - `decode_access_token()` added to `core/security.py`
  - `backend/auth/middleware.py` now FROZEN and stable

- Testing:
  - 12+ test cases in `tests/test_documents.py`
  - Scenarios: valid uploads, unsupported types, oversize files, no-client cases, ownership

**Files:**
- `backend/documents/__init__.py`
- `backend/documents/schemas.py`
- `backend/documents/parsers.py`
- `backend/documents/service.py`
- `backend/documents/routes.py`
- `tests/test_documents.py`

**PR:** #4 — Merged ✅  
**Commit:** `feat(documents): add document upload and parsing`

---

### Phase 5: Embedding Creation (2026-03-18)

**Status:** ✅ MERGED to main

**What was done:**
- Embedding endpoints:
  - `POST /embeddings/documents/{document_id}` — create embeddings for a document
  - `GET /embeddings/documents/{document_id}` — list embeddings for a document
  - `DELETE /embeddings/documents/{document_id}` — delete all embeddings for a document

- Embedding pipeline (`backend/embeddings/service.py`):
  - `chunk_text(text, chunk_size=500, overlap=100)` — splits parsed_text into overlapping chunks
  - `create_embeddings_for_document(document_id, db)`:
    - Validates document exists, belongs to client, and is `ready`
    - Deletes existing embeddings (idempotent re-embed)
    - For each chunk:
      - Calls OpenAI `text-embedding-3-small` via `openai_client`
      - Stores vector (1536-dim) as JSON in metadata (pgvector later in Phase 6)
  - `get_embeddings_for_document` and `delete_embeddings_for_document` helpers

- OpenAI Integration:
  - `OPENAI_API_KEY` read from `.env` (real key needed in production)
  - Tests fully mock OpenAI client (no real API calls)

- Testing:
  - 12 new tests in `tests/test_embeddings.py`
  - Scenarios: chunking behavior, happy path embedding creation, invalid status, not found, ownership, OpenAI failure handling, re-embedding
  - Total test count now: **64** (all passing)

**Files:**
- `backend/embeddings/__init__.py`
- `backend/embeddings/schemas.py`
- `backend/embeddings/service.py`
- `backend/embeddings/routes.py`
- `tests/test_embeddings.py`

**PR:** #5 — Merged ✅  
**Commit:** `feat(embeddings): add embedding creation with OpenAI text-embedding-3-small`

---

## 🌍 Production Deployment

**Backend URL:** https://ai-chatbot-production-6531.up.railway.app

| Service | Status |
|---------|--------|
| **FastAPI Backend** | ✅ Online (Railway) |
| **PostgreSQL 15 + pgvector** | ✅ Online (Railway) |
| **Alembic Migrations** | ✅ All tables created |
| **E2E Test (real OpenAI)** | ✅ Tested — RAG pipeline works! |
| **Frontend (Next.js)** | ✅ Live on Vercel |
| **Embed Widget** | ✅ Live on Railway |

**Live endpoints:**
- `GET /health` → `{"status": "ok"}`
- `POST /auth/register` — регистрация
- `POST /auth/login` — логин
- `POST /clients` — создать клиента
- `POST /documents` — загрузить документ
- `POST /embeddings/documents/{id}` — векторизовать
- `POST /search` — семантический поиск
- `POST /chat` — RAG чат (X-API-Key)
- `GET /chat/history/{session_id}` — история чата

**Deployment config:**
- Start Command: `alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- Python: 3.11.15
- Auto-deploy: git push → Railway rebuilds automatically

---

## 🧪 E2E Production Test (2026-03-18)

**Tested live on Railway with real OpenAI API:**

```bash
# 1. Health check
GET /health → {"status": "ok"} ✅

# 2. Register + login
POST /auth/register → JWT token ✅

# 3. Create client → got 32-char API key ✅

# 4. Upload document (test.md with FAQ content) → status: ready ✅

# 5. Create embeddings → chunks_created: 2 ✅

# 6. Chat (RAG)
POST /chat
Question: "How to reset password?"
Answer: "Go to Settings -> Security -> Click Reset Password -> Check your email."
source_documents: ["6500a72c-..."] ✅
tokens_used: 230 ✅
```

**RAG pipeline confirmed working end-to-end in production!** 🎉

---

## ✅ Completed Phases (7/11)

### Phase 6: Vector Search (2026-03-18)

**Status:** ✅ MERGED to main

**What was done:**
- `POST /search` — semantic search over client's embeddings
- Cosine similarity in Python (pgvector optimization later)
- `embed_query()` via OpenAI text-embedding-3-small
- `cosine_similarity()` with zero-vector safety
- `search_similar_chunks()` — always filtered by client_id
- `top_k` parameter (default 3)
- Empty query → 422, top_k=0 → 422

**Tests:** 12 new, 76 total passing ✅  
**PR:** #6 — Merged ✅

---

### Phase 7: Chat API — RAG Pipeline (2026-03-18)

**Status:** ✅ MERGED to main

**What was done:**
- `POST /chat` — PUBLIC endpoint, auth via `X-API-Key` header
  - Full RAG pipeline: search → build prompt → GPT-3.5-turbo → save → return
  - `build_rag_prompt()` with context chunks + separator
  - `generate_answer()` — OpenAI gpt-3.5-turbo, temp=0.2, max_tokens=500
  - Fallback: "I don't have information about this." if no context
  - Session continuity (same session_id = same conversation)
  - Auto-generated session_id if not provided
  - `source_documents` returned (doc UUIDs used in answer)
  - OpenAI `APIError` → 503

- `GET /chat/history/{session_id}` — JWT protected, ownership enforced

**Note:** `source_documents` stored as None in SQLite tests; real UUIDs in PostgreSQL production.

**Tests:** 14 new, 90 total passing ✅  
**PR:** #7 — Merged ✅

---

### Migration: gpt-4o-mini (2026-03-19)

**Status:** ✅ Merged (FI-033)

**What was done:**
- **Было:** gpt-3.5-turbo
- **Стало:** gpt-4o-mini
- **Причина:** лучше качество, примерно равная стоимость, лучше работает с многоязычностью

**Files:** `backend/chat/service.py`, `tests/test_chat.py`, docs (tech-stack, overview, phase-breakdown)

**PR:** FI-033 — Merged ✅

---

## 📋 Remaining Phases (3 phases, ~1 week)

### Phase 8: Dashboard Backend
**Status:** ⏭️ SKIPPED — existing endpoints are sufficient for frontend MVP
- `GET /documents`, `GET /clients/me`, `GET /chat/history/{session_id}` already cover dashboard needs
- Can add dedicated dashboard endpoints later if needed

---

### Phase 9: Frontend Dashboard (Next.js) ✅ COMPLETE

**Branch:** feature/frontend → merged to main

**What to implement:**
- Login/signup pages
- Dashboard (API key, embed code, stats)
- Document manager (upload + auto-embed, list, delete, status badges)
- Chat logs viewer (by session_id)
- Responsive TailwindCSS styling

**Stack:** Next.js 14 (App Router), TypeScript, TailwindCSS  
**Env:** `NEXT_PUBLIC_API_URL=https://ai-chatbot-production-6531.up.railway.app`

**Files:**
- `frontend/lib/api.ts` (API client with JWT + localStorage)
- `frontend/app/page.tsx` (redirect logic)
- `frontend/app/(auth)/login/page.tsx`
- `frontend/app/(auth)/signup/page.tsx`
- `frontend/app/(app)/dashboard/page.tsx`
- `frontend/app/(app)/documents/page.tsx`
- `frontend/app/(app)/logs/page.tsx`
- `frontend/components/Navbar.tsx`
- `frontend/middleware.ts` (auth redirect)

---

### Phase 10: Embed Widget ✅ COMPLETE (2026-03-18)

**What was done:**
- Vanilla JS widget (~6KB) served from Railway at `/embed.js`
- Floating blue chat button (fixed, bottom-right)
- Toggle chat window (380×500px)
- User/assistant message bubbles
- Loading indicator ("...")
- Session continuity (stores session_id)
- All styles inline — no external CSS
- DOMContentLoaded fix — works regardless of script tag position
- CORS updated to allow_origins=["*"] for third-party embedding

**Usage:**

    <div id="ai-chat-widget" data-api-key="YOUR_API_KEY"></div>
    <script src="https://ai-chatbot-production-6531.up.railway.app/embed.js"></script>

**Files:**
- `backend/widget/static/embed.js`
- `backend/widget/routes.py`
- `backend/widget/__init__.py`

---

### Phase 11: Final Deploy + Testing ✅ COMPLETE (2026-03-18)

**What was done:**
- README.md with full documentation
- .env.example template
- Railway + Vercel auto-deploy connected to GitHub main
- E2E tested: register → upload → embed → chat → widget

### Security Sprint ✅ COMPLETE (2026-03-18)

**What was done:**
- JWT_SECRET rotated to strong 64-char random key
- Rate limiting via slowapi (IP-based):
  - POST /auth/register → 5/hour
  - POST /auth/login → 10/minute
  - POST /chat → 30/minute
  - POST /documents → 20/hour
- Document limit: max 20 per client
- CORS: allow_origins=["*"] for widget embedding
- Custom OpenAI API key per client (required):
  - `openai_api_key` on Client model
  - Encrypted at rest via Fernet + ENCRYPTION_KEY
  - Dashboard UI for setting/removing client key
- 100+ tests all passing (rate limiting + encryption disabled/handled in test env)

---

### RAG Quality Sprints 🧠 (2026-03-18+)

**What was done so far:**
- RAG prompt tuned for support-style answers:
  - assistant acts as technical support agent for client product (SaaS, API, docs)
  - answers in the same language as the question (RU/EN)
  - for "which setting / какая настройка" questions names the exact setting and UI path when present in context
  - avoids false "I don't know" when relevant context exists
- Hybrid retrieval:
  - primary: vector search (text-embedding-3-small + cosine similarity)
  - fallback: keyword search over chunk_text when max similarity < 0.3
- Debug tooling:
  - `/chat/debug` endpoint with full retrieval debug (mode + chunks)
  - Dashboard "Debug" page

### Chat Logs & Feedback (2026-03-18) ✅

- Inbox-style `/logs` page: sessions list + full message view
- 👍/👎 feedback on assistant messages + `ideal_answer` field
- `/review` page with retrieval debug per bad answer
- `GET /chat/sessions`, `GET /chat/logs/session/{id}`, `GET /chat/bad-answers`
- `POST /chat/messages/{id}/feedback`

### Admin & Auth (2026-03-18) ✅

- FI-014: Admin metrics dashboard (`/admin/metrics`) — summary + per-client table
- FI-015: Email verification via link (Brevo HTTP API)
- FI-016: Enforce email verification on mutating endpoints (403 for unverified)
- FI-017: Brevo HTTP API for email (replaces broken SMTP on Railway)
- FI-018: Token tracking per chat session (`Chat.tokens_used` → admin metrics)
- Domain: `getchat9.live` → Vercel frontend

### Research & Planning (2026-03-18) ✅

- Manual QA by tester → 18 issues logged → split into: missing docs, outdated data, prompt behavior
- RAG quality research from 5 models (Perplexity, ChatGPT, DeepSeek, Gemini, Claude):
  - Consensus: overlap chunking, hybrid search (BM25+vector+RRF), reranking, graceful degradation
  - Unique: HyDE (FI-036), Knowledge Tiers table (FI-031), Prompt versioning in DB, Query expansion, Sentence-window retrieval
- Product backlog RICE-prioritized
- New FIs created: FI-019 to FI-037

### Tomorrow's Plan (2026-03-19) 🎯

**Order of work:**
1. `gpt-4o-mini` upgrade (2 min)
2. FI-031 — Org config layer (support_email, trial_period, etc. always in system prompt)
3. FI-007 — Per-client system prompt (with 5 elements from research)
4. FI-005 — Greeting message in widget
5. Request updated docs from CDN2 (email ready)
6. FI-009 — Chunking with overlap + structure-aware (after new docs arrive)

**Cursor prompts ready:**
- `cursor_prompts/FI-015_auth_email_verification.txt`
- `cursor_prompts/FI-016_enforce_email_verification.txt`
- `cursor_prompts/FI-017_brevo_http_email.txt`
- `cursor_prompts/FI-018_tokens_tracking.txt`
  - Dashboard "Debug" page: question → answer + retrieval debug (vector/keyword/none, previews)

**Next candidates (see FEATURE_IDEAS_BACKLOG.md):**
- Per-client system prompt (FI-007)
- Improved chunking + metadata (FI-009)
- Feedback 👍/👎 + bad answers report (FI-010)

---

## 🔧 Local Development Setup

### Prerequisites
- Python 3.10+ (venv activated)
- PostgreSQL 15 + pgvector extension (Docker)
- Node.js 18+ (for frontend later)
- Git + GitHub SSH key

### Database
```bash
# Run PostgreSQL in Docker
docker run --name postgres-ai-chatbot \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=ai_chatbot \
  -p 5432:5432 \
  -d pgvector/pgvector:pg15
```

### Environment Variables (.env)
```
DATABASE_URL=postgresql://postgres:password@localhost:5432/ai_chatbot
ENVIRONMENT=development
JWT_SECRET=your-32-char-secret-key-here-xxxxx
OPENAI_API_KEY=sk-xxxx  # NEEDED for Phase 5+
```

### Run Backend
```bash
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Run Tests
```bash
pytest tests/ -v --cov=backend --cov-min-percentage=80
```

---

## 📊 Statistics

| Metric | Value |
|--------|-------|
| **Phases Complete** | 11/11 + Security Sprint ✅ |
| **Phases Remaining** | 0 |
| **Total Lines** | ~4,500 |
| **Test Cases (Total)** | 108 |
| **Code Coverage** | All tests passing ✅ |
| **Time Spent** | ~14 hours total |
| **Timeline** | MVP DONE in 2 days! 🚀 |
| **Backend URL** | https://ai-chatbot-production-6531.up.railway.app |
| **Frontend URL** | https://ai-chatbot-three-lovat-32.vercel.app |
| **Widget** | https://ai-chatbot-production-6531.up.railway.app/embed.js |

---

## 🚀 Git Workflow

### Current Status
- **Main branch:** Up to date with Phases 1-2 merged ✅
- **Active branch:** main (ready for Phase 3)
- **Local branches deleted:** feature/database, feature/auth

### Next Steps
```bash
# For Phase 3:
git checkout main
git pull origin main
git checkout -b feature/clients

# Work on Phase 3...

git add -A
git commit -m "feat(clients): add client management API"
git push -u origin feature/clients

# Create PR on GitHub → review → merge
```

### Branch Naming Convention
- `feature/clients` (Phase 3)
- `feature/documents` (Phase 4)
- `feature/embeddings` (Phase 5)
- `feature/search` (Phase 6)
- `feature/chat` (Phase 7)
- `feature/dashboard-backend` (Phase 8)
- etc.

---

## ⚠️ Critical Rules (FROZEN CODE)

### After Each Phase Merge:
- ✅ Previous modules are FROZEN (no changes)
- ✅ New phases create new modules only
- ✅ Only `backend/main.py` gets 1-line additions (router imports)
- ❌ NEVER modify: backend/core/*, backend/migrations/*, backend/models.py (after Phase 1)

### Code Discipline:
- Tests must pass (old + new): `pytest tests/ -v`
- Coverage ≥80%: `pytest --cov=backend --cov-min-percentage=80`
- Code review checklist before merge (see `docs/05-code-discipline-and-deploy.md`)

---

## 📝 Key Files & Paths

### Core Infrastructure (FROZEN after Phase 1)
```
backend/core/
├─ __init__.py
├─ config.py (Pydantic BaseSettings)
├─ db.py (PostgreSQL engine, SessionLocal)
└─ security.py (bcrypt, JWT utilities)

backend/models.py (7 models: User, Client, Document, Embedding, Chat, Message)
backend/migrations/ (Alembic: autogenerate, upgrade head)
```

### Phase 1 Auth Module (FROZEN after Phase 2)
```
backend/auth/
├─ __init__.py
├─ schemas.py (Pydantic request/response models)
├─ service.py (register_user, authenticate_user, get_current_user_from_token)
├─ routes.py (POST /register, POST /login, GET /me)
└─ middleware.py (JWTMiddleware, get_current_user dependency)
```

### Main App
```
backend/main.py (FastAPI app with CORS, middleware, routers)
tests/test_models.py (15 tests for DB)
tests/test_auth.py (13 tests for auth)
```

---

## 📚 Documentation

- `docs/01-overview.md` — Architecture, business model, user journeys
- `docs/02-mvp-scope-and-db.md` — MVP scope, database schema
- `docs/03-tech-stack.md` — Technologies, deployment topology
- `docs/04-phase-breakdown.md` — All 11 phases in detail
- `docs/05-code-discipline-and-deploy.md` — Code rules, deployment
- `PROGRESS.md` — This file (updated after each phase)

---

## 🎯 Tomorrow's Action Items

### For Elle (2026-03-18)
1. ✅ Pull latest main: `git pull origin main`
2. ✅ Create Phase 3 branch: `git checkout -b feature/clients`
3. ✅ Use Cursor prompt (provided by assistant) to generate Phase 3 code
4. ✅ Run local tests: `pytest tests/ -v`
5. ✅ Test endpoints manually (curl)
6. ✅ Create PR #3 on GitHub
7. ✅ Merge after approval

### For Assistant
1. ✅ Provide Phase 3 Cursor prompt
2. ✅ Review Phase 3 code
3. ✅ Approve merge
4. ✅ Provide Phase 4 spec
5. ✅ Continue cycle for Phases 5-11

---

## 🔐 Security Checklist

- ✅ Phase 1: DB isolation, cascade deletes
- ✅ Phase 2: JWT validation, bcrypt hashing, password strength
- ✅ Phase 3: API key validation, client ownership checks
- ✅ Phase 4: Per-client document isolation, file type/size validation
- ✅ Phase 5: Ownership-enforced embedding generation, OpenAI calls mocked in tests
- ⏳ Phase 6+: Always filter by `client_id` in queries (multi-tenancy) and enforce tenant isolation in search/chat

---

## 📞 Contact & Questions

**Repository:** https://github.com/tabularasa31/ai-chatbot  
**Owner:** Elina  
**Timezone:** GMT+4 (Tbilisi)

---

**Last Updated:** 2026-03-17 19:20 UTC  
**Next Update:** After Phase 3 completion
