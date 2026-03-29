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
  - User access tokens include `typ=chat9_user`; internal **Eval QA** uses a separate secret `EVAL_JWT_SECRET` and `typ=eval_tester` on `/eval/*` only (`backend/eval/`, `backend/core/jwt_kinds.py`)
  
- **LLM Integration:** OpenAI API (via client's own API key)
  - gpt-4o-mini for chat (fast + cheap)
  - Optional second gpt-4o-mini call per chat turn for answer validation (FI-034): groundedness check; failures do not block the user-facing reply
  - **PII redaction / privacy hardening (FI-043 + follow-up hardening):** before embedding search, chat completion, and validation completion, the user question is passed through regex redaction (`backend/chat/pii.py`); placeholders such as `[EMAIL]`, `[PHONE]`, `[API_KEY]`, `[CARD]`, `[PASSWORD]`, `[ID_DOC]`, `[IP]`, `[URL_TOKEN]` are sent to OpenAI; the original text is stored encrypted in `messages.content_original_encrypted`, redacted text is stored in `messages.content_redacted`, and legacy `messages.content` now mirrors the redacted-safe version
  - text-embedding-3-small for vectors (1536-dim)
  - Each client brings their own key — no platform markup
  
- **Document Parsing:**
  - pypdf>=4.0.0 (PDF extraction — replaces deprecated PyPDF2)
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
  - Production release usually follows the repo branch workflow (`main` for development, `deploy` for production)
  
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
│  <script src="https://api/embed.js?clientId=ch_…">      │
│  optional: window.Chat9Config.widgetUrl → Next.js origin   │
│                                                           │
│  ↓                                                        │
│  Loader injects iframe → Next.js /widget?clientId=…      │
│                                                           │
├─────────────────────────────────────────────────────────┤
│              Next.js /widget (ChatWidget)                 │
│                                                           │
│  - Chat UI (messages, input)                             │
│  - Optional: POST /widget/session/init → session_id +   │
│    mode (identified | anonymous) for HMAC user context   │
│  - POST /widget/chat (BFF) → FastAPI /widget/chat        │
│                                                           │
│  ↓                                                        │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                    FastAPI Backend                       │
│                  (Railway deployment)                    │
│                                                           │
│  POST /widget/session/init (api_key, optional identity)  │
│  POST /widget/chat (public clientId) or POST /chat (X-API-Key) │
│    ↓                                                      │
│    1. Resolve client → client_id + openai_api_key        │
│    2. Redact PII in question (regex + tenant toggles)    │
│    3. Embed redacted question (OpenAI, client's key)      │
│    4. Search embeddings (pgvector)                       │
│    5. Build prompt (+ safe user context line if FI-KYC)  │
│    6. Call OpenAI gpt-4o-mini (client's key); optional     │
│       validation call (FI-034) also uses redacted text   │
│    7. Track token usage                                  │
│    8. Save encrypted original + redacted-safe message      │
│    9. Return {answer, sources, tokens_used}              │
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
│  - OpenAI API key + widget/agents settings               │
│  - Knowledge hub (files + URL sources)                   │
│  - Chat logs / feedback / escalations                    │
│  - Admin/privacy views                                   │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow: Question to Answer (~2 seconds)

```
1. Visitor types question
   ↓
2. Widget sends: `POST /widget/chat?client_id=ch_…&message=...`
   ↓
3. Backend resolves public `clientId` → gets tenant `client_id` + client's OpenAI key
   ↓
4. Regex PII redaction on question (FI-043) → typed placeholders for external calls
   ↓
5. OpenAI API: Embed redacted question → vector(1536)  [client's key]
   ↓
6. Retrieval pipeline:
   - pgvector / Python cosine candidate acquisition
   - BM25 over the shared candidate pool
   - Reciprocal Rank Fusion + reranking + selection
   - overlap / contradiction reliability assessment
   ↓
7. Build grounded prompt from the selected chunks
   ↓
8. OpenAI API: Chat completion  [client's key]
   gpt-4o-mini
   ↓
9. Optional: second gpt-4o-mini call for validation (FI-034) using same redacted question
   ↓
10. Track tokens used → save encrypted original question plus redacted-safe message fields
   ↓
11. Return answer + sources + reliability metadata
   ↓
12. Widget / dashboard displays answer
```

---

## Security Model

### API Key Authentication
- Client gets 32-character random API key
- Dashboard / private API calls use the tenant API key (`X-API-Key`)
- Public widget chat uses tenant `public_id` / `clientId`; optional identified-mode bootstrap uses `POST /widget/session/init` with the private API key plus signed identity token
- Backend validates the private API key only on the authenticated/private paths or widget session bootstrap, then retrieves `client_id` and the tenant OpenAI key
- All queries filter by client_id (no data leaks between tenants)

### OpenAI Key Isolation
- Each client's OpenAI key is stored encrypted per client
- Costs go directly to the client's OpenAI account
- Chat9 never marks up or proxies OpenAI costs

### User message privacy (FI-043)
- Regex redaction on the user question before any OpenAI call (embedding, chat, validation)
- `messages.content_original_encrypted` keeps the original wording encrypted at rest; `messages.content_redacted` and legacy `messages.content` keep the safe/redacted version
- Dashboard/admin flows are **safe-first**: redacted text is the default view; original text is available only for privileged admin access and is audit-logged via `pii_events`
- Tenant admins can manage optional regex entity toggles in `Settings → Privacy`; privacy audit rows are retained via admin retention controls

### Multi-Tenant Isolation
- Every query includes `WHERE client_id = $1`
- No way to see other client's documents
- No way to see other client's chat history

### Rate limiting (shipped baseline)
- **slowapi** on public and sensitive routes: e.g. `GET /clients/validate/{api_key}` (20/min), `POST /search` (30/min), `POST /chat` (30/min), `POST /widget/session/init` and `POST /widget/chat` (20/min) — see `backend/core/limiter.py` and route decorators.
- **Future / Phase 2 embed:** per-client daily quotas, global per-tenant caps, subscription-tier limits — see `docs/BACKLOG_EMBED-PHASE2.md`.

### OpenAI errors (ongoing)
- Invalid key / quota: surface clear errors in UI; retry/backoff for transient limits remains backlog where not yet implemented

---

## Scalability Considerations

### Database
- Indexes on `client_id`, `document_id`, `vector`
- Partitioning by `client_id` if needed (future)
- Connection pooling (pgBouncer)

### Backend
- Stateless design (can run multiple instances)
- Background embedding processing (FI-021) for document and URL-source indexing
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

- **Backend:** Railway serves FastAPI; production releases typically flow through the repo branch strategy (`main` / `deploy`)
- **Frontend:** Vercel serves Next.js; production branch often tracks `deploy`
- **CI (FI-026):** GitHub Actions on `push` / `pull_request` to `main` and `deploy` — backend Ruff + pytest (`tests/`), frontend ESLint + `next build` (`.github/workflows/ci.yml`)
- **Database:** PostgreSQL on Railway
- **Email:** Brevo HTTP API (transactional + future daily reports)
- **Secrets:** Environment variables (.env)

---

**Next:** See [`IMPLEMENTED_FEATURES.md`](./IMPLEMENTED_FEATURES.md), [`PROGRESS.md`](./PROGRESS.md), and [`BACKLOG_PRODUCT.md`](./BACKLOG_PRODUCT.md) for current shipped capabilities and roadmap.
