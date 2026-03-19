# AI Chatbot Platform — Architecture Overview

**Status:** MVP Planning  
**Timeline:** 4-5 weeks  
**Owner:** Elina

---

## What is this platform?

**AI Business Chat** — A SaaS platform where companies upload their documentation, and customers get an AI chatbot trained on that documentation.

### Multi-Tenant Architecture

```
┌─────────────────────────────────────────────────────┐
│           AI Business Chat Platform                  │
│  (One application, Multiple clients)                │
└─────────────────────────────────────────────────────┘
           ↓
    ┌──────────────┬──────────────┬──────────────┐
    ↓              ↓              ↓              ↓
 Client A       Client B      Client C      Client N
(Company 1)    (Company 2)    (Company 3)
    ├─ Docs       ├─ Docs       ├─ Docs       ├─ Docs
    ├─ API key    ├─ API key    ├─ API key    ├─ API key
    ├─ Widget     ├─ Widget     ├─ Widget     ├─ Widget
    └─ Users      └─ Users      └─ Users      └─ Users
```

---

## How It Works

### 1. Client Onboarding
- Client registers → gets unique API key + dashboard
- Can embed chat widget on their website

### 2. Document Upload
- Client uploads: PDF, Markdown, Google Docs, Swagger/OpenAPI
- Documents are parsed and processed

### 3. Indexing
- Documents split into chunks (500 chars with 100 char overlap)
- Each chunk vectorized using OpenAI embeddings
- Vectors stored in PostgreSQL with pgvector

### 4. Website Widget
- Client embeds simple `<script>` tag on their website
- Chat bubble appears on their pages

### 5. RAG Pipeline (Retrieval Augmented Generation)
1. Website visitor asks question in chat
2. Question is embedded
3. Similar document chunks found (vector search)
4. Top 3 chunks + question sent to OpenAI gpt-4o-mini
5. LLM generates answer based on client's documentation
6. Answer appears in chat (~2 seconds)

### 6. Logging & Analytics
- Client sees all conversations in dashboard
- Can approve/edit/reject answers
- Provides feedback for improvement

---

## Business Model (MVP - Free)

### For Clients (Companies)

**Free MVP includes:**
- ✅ Unlimited documents upload
- ✅ Unlimited questions
- ✅ API key + dashboard
- ✅ Basic embed code
- ✅ Chat log history
- ✅ Basic customization (none in MVP)

**Future (v2 features - paid tiers):**
- Advanced customization (colors, tone, personality)
- Team collaboration
- Advanced analytics
- Slack/Email notifications
- Webhooks
- Custom LLM fine-tuning

---

## User Journeys

### Client (Company Admin)

#### Day 1: Onboarding
```
1. Visit app → Sign up (email + password)
2. After login → Dashboard shows:
   ├─ Unique API Key (copy button)
   ├─ Embed code (HTML snippet)
   ├─ "Upload Documents" button
   └─ "Chat Logs" link
3. Copy code and integrate on website
```

#### Day 2: Document Upload
```
1. Dashboard → "Documents" section
2. Upload documents (drag & drop):
   - PDF files
   - Markdown files
   - Google Docs (exported)
   - Swagger/OpenAPI specs
3. Wait for processing (status: Processing → Ready)
4. See parsed content preview
5. Widget becomes active on website
```

#### Day 3+: Monitor Conversations
```
1. Dashboard → "Chat Logs"
2. View all conversations:
   ├─ Visitor question
   ├─ AI answer
   ├─ Source documents used
   ├─ Timestamp
   └─ Feedback (approve/edit/reject)
3. Optional: Analytics (top questions, success rate)
```

### End User (Website Visitor)

```
1. Visit Client A's website
2. See chat bubble in bottom-right corner 💬
3. Click → Chat opens
4. Type question: "How do I reset my password?"
5. AI responds: "Go to Settings → Password → Click 'Reset'..."
6. Conversation continues naturally
7. Close chat
```

---

## Technical Vision

- **Backend:** FastAPI (Python) + PostgreSQL with pgvector
- **Frontend:** Next.js (React/TypeScript) + TailwindCSS
- **LLM:** OpenAI gpt-4o-mini for chat + text-embedding-3-small for vectors
- **Embedding:** RAG (Retrieval Augmented Generation) pattern
- **Deployment:** Railway (backend) + Vercel (frontend)
- **Security:** Multi-tenant isolation by client_id on every query

---

## Key Features (MVP)

✅ User authentication (email/password + JWT)
✅ Multi-tenant client management (API keys)
✅ Document upload (PDF, MD, Swagger)
✅ Automatic embedding + vector search
✅ RAG-powered chat API
✅ Dashboard (documents, chat logs)
✅ Embeddable widget
✅ Chat history & logging

---

**Next:** See `02-mvp-scope-and-db.md` for detailed scope and database schema.
