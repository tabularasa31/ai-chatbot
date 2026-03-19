# Technical Stack & Architecture

---

## Technology Choices

### Backend
- **Framework:** FastAPI (Python 3.11)
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
  
- **Authentication:** JWT + bcrypt + email verification
  - Stateless auth (good for scalability)
  - Industry standard
  
- **LLM Integration:** OpenAI API (via client's own API key)
  - gpt-4o-mini for chat (fast + cheap)
  - text-embedding-3-small for vectors (1536-dim)
  - Each client brings their own key — no platform markup
  
- **Document Parsing:**
  - PyPDF2 (PDF extraction)
  - markdown (Markdown parsing)
  - yaml/json (Swagger/OpenAPI specs)
  
- **Email:** Brevo HTTP API
  - Transactional emails (email verification)
  - Daily summary reports (coming: FI-039)
  
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
  
- **HTTP Client:** fetch / axios
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
│    1. Validate API key → get client_id + openai_api_key  │
│    2. Embed question (OpenAI, client's key)              │
│    3. Search embeddings (pgvector)                       │
│    4. Build prompt with top 3 chunks                     │
│    5. Call OpenAI gpt-4o-mini (client's key)             │
│    6. Track token usage                                  │
│    7. Save to messages table                             │
│    8. Return {answer, sources, tokens_used}              │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                  PostgreSQL + pgvector                   │
│                                                           │
│  Tables:                                                 │
│  - users, clients (with openai_api_key)                 │
│  - documents, embeddings (vectors)                      │
│  - chats, messages (with token_count, feedback)         │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                OpenAI API (External)                      │
│                                                           │
│  - text-embedding-3-small (for vectors)                 │
│  - gpt-4o-mini (for chat)                               │
│  - Called with each client's own API key                │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                   Next.js Frontend                       │
│                  (Vercel deployment)                     │
│                                                           │
│  Client dashboard:                                       │
│  - Login/signup                                          │
│  - OpenAI API key setup                                  │
│  - Document upload                                       │
│  - Chat logs viewer with feedback                        │
│  - API key management                                    │
│  - Token usage stats                                     │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow: Question to Answer (~2 seconds)

```
1. Visitor types question
   ↓
2. Widget sends: POST /chat {question, api_key}
   ↓
3. Backend validates API key → gets client_id + client's openai_api_key
   ↓
4. OpenAI API: Embed question → vector(1536)  [client's key]
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
7. OpenAI API: Chat completion  [client's key]
   gpt-4o-mini (temperature=0.2, max_tokens=500)
   ↓
8. Track tokens used → save to messages table
   ↓
9. Return: {answer, source_docs, tokens_used}
   ↓
10. Widget displays answer
```

---

## Security Model

### API Key Authentication
- Client gets 32-character random API key
- Widget includes key in requests: `X-API-Key: abc123...`
- Backend validates key → retrieves client_id and openai_api_key
- All queries filter by client_id (no data leaks between tenants)

### OpenAI Key Isolation
- Each client's OpenAI key is stored encrypted per client
- Costs go directly to the client's OpenAI account
- Chat9 never marks up or proxies OpenAI costs

### Multi-Tenant Isolation
- Every query includes `WHERE client_id = $1`
- No way to see other client's documents
- No way to see other client's chat history

### Rate Limiting (Future)
- Per-API-key request rate limiting
- OpenAI error handling (invalid key, quota exceeded)

---

## Scalability Considerations

### Database
- Indexes on `client_id`, `document_id`, `vector`
- Partitioning by `client_id` if needed (future)
- Connection pooling (pgBouncer)

### Backend
- Stateless design (can run multiple instances)
- Background embedding processing (FI-021, coming next)
- OpenAI rate limit handling (retry logic)

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
- **Email:** Brevo HTTP API (transactional + future daily reports)
- **Secrets:** Environment variables (.env)

---

**Next:** See `04-phase-breakdown.md` for detailed implementation phases.
