# Chat9 — Architecture Overview

**Status:** MVP  
**Owner:** Elina

---

## What is Chat9?

**Chat9** — "Your support mate, always on." A SaaS platform where companies upload their documentation and get an AI chatbot that answers customer questions 24/7.

Clients bring their own OpenAI key — full cost transparency, no platform markup.

### Multi-Tenant Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Chat9 Platform                      │
│       (One application, Multiple clients)           │
└─────────────────────────────────────────────────────┘
           ↓
    ┌──────────────┬──────────────┬──────────────┐
    ↓              ↓              ↓              ↓
 Client A       Client B      Client C      Client N
    ├─ Docs       ├─ Docs       ├─ Docs       ├─ Docs
    ├─ OpenAI key ├─ OpenAI key ├─ OpenAI key ├─ OpenAI key
    ├─ Widget     ├─ Widget     ├─ Widget     ├─ Widget
    └─ Users      └─ Users      └─ Users      └─ Users
```

---

## How It Works

### 1. Client Onboarding
- Client registers → adds their own OpenAI API key → gets unique API key + dashboard
- All AI calls use the client's own key — transparent costs, no platform markup
- Can embed chat widget on their website

### 2. Document Upload
- Client uploads: PDF, Markdown, Swagger/OpenAPI
- Documents are parsed, chunked, and embedded automatically

### 3. Indexing
- Documents split into chunks (500 chars with 100 char overlap)
- Each chunk vectorized using OpenAI `text-embedding-3-small`
- Vectors stored in PostgreSQL with pgvector

### 4. Website Widget
- Client embeds simple `<script>` tag on their website
- Chat bubble appears on their pages

### 5. RAG Pipeline
1. Website visitor asks question in chat
2. Question is embedded
3. Similar document chunks found (vector search)
4. Top 3 chunks + question sent to OpenAI `gpt-4o-mini`
5. LLM generates answer based on client's documentation
6. Answer appears in chat (~2 seconds)

### 6. Feedback & Improvement
- Client sees all conversations in dashboard
- 👍/👎 feedback + optional ideal answer
- Feedback loop improves answers over time

### 7. Logging & Analytics
- Full chat history with session tracking
- Token usage per client
- Debug mode: see which document chunks were used

---

## User Journeys

### Client (Company Admin)

#### Day 1: Onboarding
1. Sign up → Dashboard
2. Add your OpenAI API key (used for all AI calls)
3. Copy API key + embed code → paste `<script>` tag on website

#### Day 2: Upload Documents
1. Upload PDFs, Markdown, Swagger files
2. Processing happens automatically (status: Processing → Ready)
3. Widget becomes active

#### Day 3+: Monitor & Improve
1. Dashboard → Chat Logs
2. View questions + AI answers + source docs used
3. Leave 👍/👎 feedback or provide ideal answers

### End User (Website Visitor)
1. Visit client's website → see chat bubble 💬
2. Ask question → AI answers from client's docs
3. Conversation logged for client review

---

## Technical Stack

- **Backend:** FastAPI (Python 3.11) + PostgreSQL + pgvector
- **Frontend:** Next.js 14 (React/TypeScript) + TailwindCSS
- **LLM:** OpenAI `gpt-4o-mini` + `text-embedding-3-small` (via client's own API key)
- **Email:** Brevo HTTP API
- **Deployment:** Railway (backend) + Vercel (frontend)
- **Security:** Multi-tenant isolation by `client_id` on every query

---

## Key Features (MVP)

✅ User authentication (email/password + JWT + email verification)  
✅ Forgot password flow (Brevo email + token reset)  
✅ Multi-tenant client management (API keys)  
✅ Document upload (PDF, Markdown, Swagger, Text)  
✅ RAG-powered chat API (gpt-4o-mini)  
✅ pgvector native search with HNSW index  
✅ Zero-config embeddable widget (iframe, no CORS issues)  
✅ Chat history & session logging  
✅ Feedback system (👍/👎 + ideal answer)  
✅ Token usage tracking  
✅ Debug mode  
✅ Admin metrics  
✅ Rate limiting (validate, search, chat)  

---

**Next:** See `02-mvp-scope-and-db.md` for detailed scope and database schema.
