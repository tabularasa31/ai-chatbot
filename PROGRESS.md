# AI Chatbot Platform — Development Progress

**Last Updated:** 2026-03-18 05:50 UTC  
**Timeline:** Week 1 of 4 (MVP Sprint)  
**Status:** Phase 1-5 COMPLETE ✅ | Phase 6 READY 🚀

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

## 📋 Remaining Phases (6 phases, ~2 weeks)

### Phase 6: Vector Search (2-3 hours)

**What to implement:**
- `POST /search` endpoint
- Input: query string + client_id
- Logic:
  1. Embed query using OpenAI
  2. Vector search in embeddings table (pgvector similarity)
  3. Filter by client_id (SECURITY: always!)
  4. Return top 3 chunks with similarity scores

**Files to create:**
- `backend/search/__init__.py`
- `backend/search/schemas.py`
- `backend/search/service.py`
- `backend/search/routes.py`
- `tests/test_search.py` (10+ test cases)

---

### Phase 7: Chat API (RAG) (3-4 hours)

**What to implement:**
- `POST /chat` endpoint
- Input: question + client_id
- Logic:
  1. Search relevant documents (Phase 6)
  2. Build prompt with top 3 chunks
  3. Call OpenAI GPT-3.5-turbo
  4. Save message to DB
  5. Return answer + source documents
- Error handling (no docs, API failure)

**Files to create:**
- `backend/chat/__init__.py`
- `backend/chat/schemas.py`
- `backend/chat/service.py` (RAG pipeline)
- `backend/chat/routes.py`
- `tests/test_chat.py` (10+ test cases)

---

### Phase 8: Dashboard Backend (2-3 hours)

**What to implement:**
- `GET /api/documents` — List documents for client
- `GET /api/chats` — List chat sessions
- `GET /api/messages?chat_id={id}` — Get chat history
- All endpoints protected (JWT + client ownership)

**Files:**
- Likely merged into existing route files
- Add new response schemas

---

### Phase 9: Frontend Dashboard (Next.js) (4-6 hours)

**What to implement:**
- Login/signup pages
- Dashboard (show API key, copy buttons)
- Document manager (upload, list, delete)
- Chat logs viewer
- Responsive TailwindCSS styling

**Stack:** Next.js 14, React, TypeScript, TailwindCSS

---

### Phase 10: Embed Widget (2-3 hours)

**What to implement:**
- Vanilla JS widget script (~50KB)
- Chat UI (input, messages, bubble)
- postMessage communication with iframe
- Serve from backend CDN

**Files:**
- `public/embed.js`
- `app/widget/page.tsx` (Next.js widget UI)
- `backend/widget/routes.py`

---

### Phase 11: Deployment & Testing (2-3 hours)

**What to implement:**
- Deploy backend to Railway
- Deploy frontend to Vercel
- Serve widget from CDN
- Live E2E testing
- Documentation

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
| **Phases Complete** | 5/11 |
| **Phases Remaining** | 6 |
| **Total Lines (Phases 1-5)** | ~2,600 |
| **Test Cases (Total)** | 64 |
| **Code Coverage** | All tests passing; target ≥80% maintained |
| **Time Spent** | ~8–10 hours (coding + setup) |
| **Estimated Total Time** | 20–25 hours |
| **Timeline** | 4 weeks (MVP) |

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
