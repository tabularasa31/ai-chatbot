# Phase Breakdown (11 Phases, 4 Weeks)

---

## Timeline

| Week | Phases | Focus |
|------|--------|-------|
| Week 1 | 1-3 | DB + Auth + Clients |
| Week 2 | 4-6 | Documents + Embeddings + Search |
| Week 3 | 7-8 | Chat API + Dashboard |
| Week 4 | 9-11 | Widget + Testing + Deploy |

---

## Phase 1: Specification & Architecture (Day 1-2)

**Deliverables:**
- OpenAPI/Swagger spec
- Database schema (SQL)
- Error codes & HTTP status codes
- Test case templates

**Time:** 2 days (you)

---

## Phase 2: Database Setup (Day 3)

**Branch:** `feature/database`

**Task:**
- Create database models (User, Client, Document, Embedding, Chat, Message)
- Create Alembic migrations
- Set up indexes & constraints
- Create seed data for testing
- Write 5+ test cases per table

**Spec (MUST NOT TOUCH):**
- ❌ backend/core/ (frozen)
- ❌ existing routes
- ✅ backend/migrations/
- ✅ backend/models.py

**Success Criteria:**
```
pytest tests/test_models.py → ALL PASS
- User creation, validation
- Client creation, API key generation (32-char random)
- Document insertion
- Embedding insertion (with mock vectors)

pytest --cov=backend/models --cov-min-percentage=80
```

---

## Phase 3: User Authentication (Day 4)

**Branch:** `feature/auth`

**Endpoints:**
```
POST /register
  Input: {email, password}
  Output: {user_id, email}
  Error: 400 (invalid), 409 (duplicate email)

POST /login
  Input: {email, password}
  Output: {token, expires_in}
  Error: 401 (wrong password)

GET /me (protected)
  Output: {id, email}
  Error: 401 (unauthorized)
```

**Spec:**
- ✅ Create: backend/auth/routes.py
- ✅ Create: backend/auth/service.py
- ✅ Create: tests/test_auth.py
- ❌ Do not modify database schema

**Success Criteria:**
```
pytest tests/test_auth.py → ALL PASS
- Can register with valid email/password
- Cannot register duplicate email
- Can login with correct credentials
- Cannot login with wrong password
- JWT token is valid for protected routes
- Token expires correctly

Tests: ≥80% code coverage
```

---

## Phase 4: Client Management (Day 4-5)

**Branch:** `feature/clients`

**Endpoints:**
```
POST /clients
  Input: {name}
  Output: {client_id, api_key, name, created_at}
  Auto-generate: 32-char random API key
  Auth: Required (JWT)

GET /clients/me
  Output: {id, name, api_key, created_at}
  Auth: Required
  Security: Only own client

GET /clients/{id}
  Output: client info
  Auth: Required
  Security: Only own client
```

**Spec:**
- ✅ Create: backend/clients/routes.py
- ✅ Create: backend/clients/service.py
- ✅ Create: tests/test_clients.py
- ❌ Do not modify auth tables

**Success Criteria:**
```
pytest tests/test_clients.py → ALL PASS
- Can create client with unique API key
- Can retrieve own client
- Cannot access other user's client
- API key is 32-char random string

Tests: ≥80%
```

---

## Phase 5: Document Upload (Day 5)

**Branch:** `feature/documents`

**Endpoints:**
```
POST /documents (multipart)
  Input: file (PDF, .md, .json/.yaml for Swagger)
  Validation:
  - File type check (.pdf, .md, .json, .yaml only)
  - File size max 50MB
  - Client owns the upload
  Output: {document_id, filename, status}

GET /documents
  Output: [{id, filename, status, created_at}]
  Filter: Only client's documents

DELETE /documents/{id}
  Cascade: Remove all embeddings for this document
```

**Parsing:**
- PDF: PyPDF2 → extract text
- Markdown: Read as-is
- Swagger/OpenAPI: Convert to readable text format

**Status Flow:** `processing` → `ready` | `error`

**Spec:**
- ✅ Create: backend/documents/routes.py
- ✅ Create: backend/documents/service.py
- ✅ Create: backend/documents/parsers.py (pdf, markdown, swagger)
- ✅ Create: tests/test_documents.py
- ✅ File storage: /tmp/uploads (or S3 later)
- ❌ Do not modify embeddings schema yet

**Success Criteria:**
```
pytest tests/test_documents.py → ALL PASS
- Can upload PDF and extract text
- Can upload Markdown
- Can upload Swagger/OpenAPI spec
- File size validation works (reject >50MB)
- File type validation works (reject .exe, .zip, etc.)
- Status changes: processing → ready
- Duplicate filenames allowed

Tests: ≥80%
```

---

## Phase 6: Embedding Creation (Day 6)

**Branch:** `feature/embeddings`

**Process (after document upload):**
1. Document marked `processing`
2. Async job: Split text into chunks (500 chars, 100 char overlap)
3. For each chunk:
   - Call OpenAI `text-embedding-3-small` API
   - Get 1536-dim vector
   - Save: {chunk_text, vector, document_id, metadata}
4. Mark document `ready`

**Error Handling:**
- If embedding fails → mark document `error` + save error message
- Retry mechanism (max 3 retries)
- Handle OpenAI rate limits gracefully

**Spec:**
- ✅ Create: backend/embeddings/service.py
- ✅ Create: backend/embeddings/worker.py (async task)
- ✅ Create: tests/test_embeddings.py
- ❌ Do not modify document parsing

**Success Criteria:**
```
pytest tests/test_embeddings.py → ALL PASS
- Can chunk document text
- Can embed chunks via OpenAI
- Vectors are 1536-dim
- Metadata saved correctly
- Handle OpenAI rate limits
- Retry on failure
- Document status changes to "ready"

Manual test:
- Upload document
- Wait 10 seconds
- Verify embeddings exist in DB
- Check each chunk has a vector
```

---

## Phase 7: Vector Search (Day 7)

**Branch:** `feature/search`

**Endpoint:**
```
POST /search
  Input: {query, client_id}
  Logic:
    1. Embed query (OpenAI API)
    2. Vector search: top 3 similar chunks
       SELECT chunk_text FROM embeddings
       WHERE document_id IN (
         SELECT id FROM documents
         WHERE client_id = $1  -- CRITICAL: always filter!
       )
       ORDER BY vector <-> query_vector
       LIMIT 3
  Output: [{chunk_text, score, document_id}, ...]
  Errors: No results found → return empty array
```

**Similarity Scoring:**
- pgvector cosine similarity (0.0 to 1.0)
- Filter: return score > 0.5 only

**Spec:**
- ✅ Create: backend/search/routes.py
- ✅ Create: backend/search/service.py
- ✅ Create: tests/test_search.py
- ❌ Do not modify embeddings table

**Success Criteria:**
```
pytest tests/test_search.py → ALL PASS
- Can search with query
- Returns top 3 similar chunks (or fewer if <3 match)
- Only returns client's documents (CRITICAL!)
- Similarity scores are correct (0.0-1.0)
- Handles "no results" gracefully

Manual test:
- Upload document: "How to reset password? Go to Settings..."
- Query: "reset my password"
- Should return the chunk with high score
```

---

## Phase 8: Chat API (Day 8)

**Branch:** `feature/chat`

**Endpoint:**
```
POST /chat
  Input: {question, client_id}
  Logic:
    1. Search relevant docs (Phase 7)
    2. Build prompt:
       "Based on:\n{chunk1}\n{chunk2}\n{chunk3}\n\n
        Answer: {question}"
    3. Call OpenAI gpt-4o-mini
       - temperature: 0.2 (fact-based, low creativity)
       - max_tokens: 500
    4. Save to DB: INSERT INTO messages
    5. Return answer + sources
  Output: {answer, source_documents, tokens_used}
  Errors:
    - No relevant docs → "I don't have info on this"
    - OpenAI down → return 503 error
```

**Spec:**
- ✅ Create: backend/chat/routes.py
- ✅ Create: backend/chat/service.py
- ✅ Create: tests/test_chat.py
- ❌ Do not modify search or embeddings

**Success Criteria:**
```
pytest tests/test_chat.py → ALL PASS
- Can generate answer based on docs
- Returns source documents used
- Handles "no docs" gracefully
- Saves chat history correctly
- Prompt format is correct

Manual test with real docs:
- Ask 5 real questions
- Verify answers are based on uploaded docs
- Check sources are correct
```

---

## Phase 9: Frontend Dashboard (Day 9-10)

**Branch:** `feature/dashboard`

**Pages:**
- `/login` — email, password, sign up link
- `/signup` — email, password, confirm, terms
- `/dashboard` — welcome, copy API key, copy embed code
- `/documents` — upload form, document list, delete button
- `/logs` — chat history, questions/answers, timestamps

**Styling:**
- TailwindCSS only
- Mobile responsive
- Basic colors (slate/blue)
- No custom theming in MVP

**Spec:**
- ✅ Create: app/page.tsx (home)
- ✅ Create: app/(auth)/login/page.tsx
- ✅ Create: app/(auth)/signup/page.tsx
- ✅ Create: app/(app)/dashboard/page.tsx
- ✅ Create: app/(app)/documents/page.tsx
- ✅ Create: app/(app)/logs/page.tsx
- ✅ Create: lib/api.ts (API client)
- ✅ Create: components/

**Success Criteria:**
```
Manual test:
- Can sign up → email in DB
- Can login → get JWT token
- Can upload document → see in list
- Can view chat logs
- Can copy API key
- Can copy embed code
- Responsive on mobile + desktop
```

---

## Phase 10: Embed Widget (Day 11)

**Branch:** `feature/widget`

**What to create:**
- `public/embed.js` (main widget script, ~50KB)
- `app/widget/page.tsx` (widget UI page)
- `backend/widget/routes.py` (widget endpoint)

**Functionality:**
```
Client adds to website:
<script src="https://api.com/embed.js"></script>
<div id="ai-chat-widget"></div>

Flow:
1. Script loads API key from data attribute
2. Creates iframe to /widget page
3. Sets up postMessage communication
4. Visitor types question → iframe sends POST /chat
5. Answer displays in chat
```

**No customization in MVP** — use default TailwindCSS styling.

**Spec:**
- ✅ Create: public/embed.js
- ✅ Create: app/widget/page.tsx
- ✅ Create: backend/widget/routes.py
- ✅ Create: tests/test_widget.py

**Success Criteria:**
```
Manual test:
- Create test HTML page
- Add embed script with valid API key
- Widget appears on page
- Can type and send question
- Get answer back
- Works on multiple test pages
```

---

## Phase 11: Deployment (Day 12-14)

**Backend (Railway):**
- Create project
- Connect PostgreSQL
- Set env vars (DATABASE_URL, OPENAI_API_KEY, JWT_SECRET)
- Deploy FastAPI
- Test endpoints

**Frontend (Vercel):**
- Connect Next.js repo
- Set NEXT_PUBLIC_API_URL
- Deploy
- Test login, upload, chat

**Widget (CDN):**
- Serve embed.js from backend
- Test on external pages

**Live Test:**
- Register as new client
- Upload 3 documents (PDF, MD, Swagger)
- Ask 10 questions
- Verify answers correct
- Check chat logs

**Documentation:**
- Setup guide
- Client onboarding
- API reference
- Troubleshooting

---

**Next:** See `05-code-discipline-and-deploy.md` for code rules and final deployment.
