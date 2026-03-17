# Technical Stack & Architecture

---

## Technology Choices

### Backend
- **Framework:** FastAPI (Python 3.10+)
  - Modern, fast, automatic API docs
  - Built-in async support
  - Easy validation with Pydantic
  
- **Database:** PostgreSQL 14+ with pgvector extension
  - Proven production DB
  - pgvector: native vector similarity search
  - Full-text search, JSON support
  
- **ORM:** SQLAlchemy
  - SQL toolkit + ORM
  - Works well with Alembic for migrations
  
- **Migrations:** Alembic
  - Version control for database schema
  - Easy rollbacks
  
- **Authentication:** JWT + bcrypt
  - Stateless auth (good for scalability)
  - Industry standard
  
- **LLM Integration:** OpenAI API
  - GPT-3.5-turbo for chat (fast + cheap)
  - text-embedding-3-small for vectors (1536-dim)
  
- **Document Parsing:**
  - PyPDF2 (PDF extraction)
  - python-docx (Word docs)
  - markdown (Markdown parsing)
  - yaml/json (Swagger specs)
  
- **Testing:** pytest
  - Industry standard
  - Easy fixtures + mocking
  
- **Deployment:** Railway
  - PostgreSQL + app in one place
  - Git push → auto deploy
  
---

### Frontend
- **Framework:** Next.js 14 (React + TypeScript)
  - App Router (modern)
  - Built-in file routing
  - SSR when needed, static export
  
- **Styling:** TailwindCSS
  - Utility-first CSS
  - Responsive by default
  - Easy dark mode
  
- **State Management:** React hooks
  - useContext for global state
  - No Redux needed for MVP
  
- **HTTP Client:** axios or fetch
  - Simple API calls
  
- **Type Safety:** TypeScript
  - Catch bugs at compile time
  - Better IDE support
  
- **Deployment:** Vercel
  - Built for Next.js
  - Automatic deployments from git
  - Edge functions if needed later
  
---

### Embeddable Widget
- **Technology:** Vanilla JavaScript (no framework)
  - Minimal bundle size
  - No dependencies to conflict with client's code
  
- **Transport:** postMessage API
  - Secure cross-origin communication with iframe
  
- **Styling:** TailwindCSS (self-contained)
  - Single widget.css file
  - No global CSS conflicts
  
- **Hosting:** Served from FastAPI backend
  - CDN later (Cloudflare)
  
---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     Website Visitor                      │
│                   (Client's Website)                     │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  <script src="https://api.com/embed.js"></script>        │
│  <div id="ai-chat-widget"></div>                         │
│                                                           │
│  ↓ (postMessage)                                          │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                    embed.js (Widget)                      │
│                   (Vanilla JS, ~50KB)                    │
│                                                           │
│  - Chat UI (input, messages)                             │
│  - Sends questions to API                                │
│  - Displays answers                                      │
│                                                           │
│  ↓ (HTTPS API call)                                       │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                    FastAPI Backend                       │
│                  (Railway deployment)                    │
│                                                           │
│  POST /chat {question, api_key}                          │
│    ↓                                                      │
│    1. Validate API key → get client_id                   │
│    2. Embed question (OpenAI)                            │
│    3. Search embeddings (pgvector)                       │
│    4. Build prompt with top 3 chunks                     │
│    5. Call OpenAI GPT-3.5-turbo                          │
│    6. Save to messages table                             │
│    7. Return {answer, sources}                           │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                  PostgreSQL + pgvector                   │
│                                                           │
│  Tables:                                                 │
│  - users, clients, documents                            │
│  - embeddings (vectors)                                 │
│  - chats, messages                                      │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                OpenAI API (External)                      │
│                                                           │
│  - text-embedding-3-small (for vectors)                 │
│  - gpt-3.5-turbo (for chat)                             │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                   Next.js Frontend                       │
│                  (Vercel deployment)                     │
│                                                           │
│  Client dashboard:                                       │
│  - Login/signup                                          │
│  - Document upload                                       │
│  - Chat logs viewer                                      │
│  - API key management                                    │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow: Question to Answer (2 seconds)

```
1. Visitor types question
   ↓
2. Widget sends: POST /chat {question, api_key}
   ↓
3. Backend validates API key (DB lookup)
   ↓
4. OpenAI API: Embed question → vector(1536)
   ↓
5. PostgreSQL pgvector: Search similar chunks
   SELECT chunk_text FROM embeddings
   WHERE client_id = X
   ORDER BY vector <-> question_vector
   LIMIT 3
   ↓
6. Build prompt:
   "Based on:\n{chunk1}\n{chunk2}\n{chunk3}\n\nAnswer: {question}"
   ↓
7. OpenAI API: Chat completion
   GPT-3.5-turbo (temperature=0.2, max_tokens=500)
   ↓
8. Save to DB: INSERT INTO messages (role, content, sources...)
   ↓
9. Return: {answer, source_docs, tokens_used}
   ↓
10. Widget displays answer (~2 seconds total)
```

---

## Security Model

### API Key Authentication
- Client gets 32-character random API key
- Widget includes key in requests: `X-API-Key: abc123...`
- Backend validates key → retrieves client_id
- All queries filter by client_id (no data leaks)

### Multi-Tenant Isolation
- Every query includes `WHERE client_id = $1`
- No way to see other client's documents
- No way to see other client's chat history

### Rate Limiting (Future)
- Per-API-key request rate
- Per-document embedding jobs
- OpenAI cost tracking

---

## Scalability Considerations

### Database
- Indexes on `client_id`, `document_id`, `vector`
- Partitioning by `client_id` if needed (future)
- Connection pooling (pgBouncer)

### Backend
- Stateless design (can run multiple instances)
- Async embeddings job queue (Celery or simple queue)
- OpenAI rate limit handling (queue + retry)

### Frontend
- Static site generation where possible
- CDN for embed.js (Cloudflare)
- Lazy loading for large document lists

---

## Deployment Topology

```
┌─────────────────────┐
│   Source Code       │
│    (GitHub)         │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    ↓             ↓
┌──────────┐ ┌──────────┐
│ Railway  │ │ Vercel   │
│(Backend) │ │(Frontend)│
└──────────┘ └──────────┘
```

- **Backend:** `git push` → Railway auto-deploys FastAPI
- **Frontend:** `git push` → Vercel auto-builds Next.js
- **Database:** PostgreSQL on Railway
- **Secrets:** Environment variables (.env)

---

**Next:** See `04-phase-breakdown.md` for detailed implementation phases.
