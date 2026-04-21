# MVP Scope & Database Schema

---

## MVP Scope

### What's INCLUDED ✅

**Backend:**
- User authentication (email/password + JWT + email verification via Brevo)
- Tenant management (create, generate API key, store per-tenant OpenAI API key)
- Bot management (per-tenant bots with disclosure / response-detail config)
- Document upload (PDF, Markdown, Swagger/OpenAPI)
- URL knowledge sources (crawl + refresh)
- Document parsing & text extraction (incl. structured OpenAPI ingestion)
- Embedding creation (OpenAI API, via tenant's own key)
- Hybrid retrieval (pgvector + BM25 + RRF + reranking, contradiction adjudication)
- RAG chat endpoint (Q&A generation with clarification outcomes)
- Chat history logging with sessions; optional identified (KYC) sessions
- Feedback system (👍/👎 + optional ideal answer)
- Manual escalation → tickets dashboard
- Gap Analyzer (Mode A docs-gap + Mode B user-signal clustering)
- Token usage tracking per tenant
- Debug mode (show which chunks were used)
- Admin metrics + PII-event audit log
- Internal eval QA framework (`/eval/*`) with a separate JWT secret

**Frontend:**
- Login/signup page
- Dashboard (API key, settings, OpenAI key setup)
- Document manager (upload, list, delete)
- Chat logs viewer with feedback
- Responsive design (Tailwind CSS)

**Widget:**
- Embeddable chat widget (iframe)
- Send question → get answer
- Optional **identified sessions** (FI-KYC): server-signed token at `POST /widget/session/init`; context on `chats.user_context`
- Basic styling (no customization)

**Database:**
- PostgreSQL 15 with pgvector extension
- Tenants, Users, Bots, Documents, UrlSources, Embeddings, Chats, Messages, ContactSessions, EscalationTickets, Gap* (Analyzer), PiiEvents, Tester/EvalSession/EvalResult (internal QA)
- Migrations (Alembic) — ~40 versioned revisions under `backend/migrations/versions/`

### What's EXCLUDED (v2) ❌

- User customization (colors, logos, tone)
- Team collaboration
- Client analytics dashboard (FI-040)
- Webhooks
- Fine-tuning
- Multiple LLM options
- Payment system
- Advanced security (SSO, 2FA)

### Coming Next 🔜

- Background embedding processing (FI-021) — async queue
- Daily summary email (FI-039) — daily reports to clients via Brevo
- Client analytics widget (FI-040)
- Status page integration (FI-041) — real-time incident awareness

---

## Database Schema

### Tables

#### Users (Platform Users)
```sql
users
├─ id (PK, UUID)
├─ email (UNIQUE, NOT NULL)
├─ password_hash (NOT NULL)
├─ is_email_verified (BOOLEAN, default=false)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)
```

#### Tenants (Workspaces)
```sql
tenants
├─ id (PK, UUID)
├─ public_id (VARCHAR(20), UNIQUE — used in widget/embed URLs, ch_…)
├─ user_id (FK → users, NOT NULL — workspace owner)
├─ name (VARCHAR, NOT NULL)
├─ api_key (UNIQUE, NOT NULL, 32-char random)
├─ openai_api_key (VARCHAR, encrypted, nullable — tenant's OpenAI key)
├─ kyc_secret_key (+ previous + previous_expires_at + hint) — FI-KYC signing secrets (encrypted)
├─ settings (JSONB, default={})
├─ is_active (BOOLEAN)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)
```

> Historical note: the model was previously named `Client`; all schema/API surfaces now use **tenant** / `tenant_id`. See `backend/models.py:Tenant`.

#### Documents (Uploaded Files)
```sql
documents
├─ id (PK, UUID)
├─ tenant_id (FK → tenants, NOT NULL)
├─ filename (VARCHAR, NOT NULL)
├─ file_type (ENUM: pdf, markdown, swagger)
├─ original_content (TEXT, raw file content)
├─ parsed_text (TEXT, extracted text)
├─ status (ENUM: processing, ready, error)
├─ error_message (TEXT, nullable)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (tenant_id)
└─ (status)
```

#### Embeddings (Vector Chunks)
```sql
embeddings
├─ id (PK, UUID)
├─ document_id (FK → documents, NOT NULL)
├─ chunk_text (TEXT — текст чанка для поиска и RAG)
├─ vector (vector(1536), pgvector — native column, not JSON)
├─ metadata (JSONB: chunk_index, char_offset, char_end, filename, file_type; см. `embeddings.service.chunk_text`)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (document_id)
└─ (vector) USING hnsw (vector_cosine_ops) — fast ANN search
```

> **Note:** Migration `dd643d1a544a` added the native `vector` column and HNSW index.
> Backfill uses `(metadata->>'vector')::vector` (text cast, not JSON cast).

#### Chat Sessions
```sql
chats
├─ id (PK, UUID)
├─ tenant_id (FK → tenants, NOT NULL)
├─ session_id (UUID, unique per visitor session)
├─ user_context (JSONB, nullable — FI-KYC identified session payload fields)
├─ tokens_used (INTEGER)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
└─ (tenant_id)
```

#### Contact sessions (FI-KYC identified-user lifecycle)
```sql
contact_sessions   -- cross-session history for identified users
├─ id (PK, UUID)
├─ tenant_id (FK → tenants ON DELETE CASCADE, NOT NULL)
├─ contact_id (VARCHAR(255), NOT NULL — the KYC token's user_id)
├─ email, name, plan_tier, audience_tag (nullable)
├─ session_started_at, session_ended_at (nullable)
├─ conversation_turns (INTEGER, default 0)
└─ created_at (TIMESTAMP)

Indexes:
├─ ix_contact_sessions_tenant_contact (tenant_id, contact_id)
└─ uq_contact_sessions_tenant_contact_active — partial unique on
   (tenant_id, contact_id) WHERE session_ended_at IS NULL
```

#### Internal manual QA (Eval)

Not tenant-scoped; internal testers only. Migration `eval_qa_mvp_v1`. See `docs/04-features.md` §11.

```sql
testers
├─ id (PK, UUID)
├─ username (UNIQUE, NOT NULL)
├─ password (TEXT, plain MVP — internal only)
├─ is_active (BOOLEAN)
└─ created_at (TIMESTAMP)

eval_sessions
├─ id (PK, UUID)
├─ tester_id (FK → testers, NOT NULL)
├─ bot_id (VARCHAR — client public_id, ch_…)
├─ started_at (TIMESTAMP)
└─ INDEX (tester_id), (bot_id), (tester_id, started_at)

eval_results
├─ id (PK, UUID)
├─ session_id (FK → eval_sessions, NOT NULL)
├─ question, bot_answer (TEXT — snapshots)
├─ verdict ('pass' | 'fail')
├─ error_category (nullable enum-like string)
├─ comment (nullable)
└─ created_at (TIMESTAMP)
-- CHECK constraints on verdict / category / comment (see migration)
```

#### Chat Messages
```sql
messages
├─ id (PK, UUID)
├─ chat_id (FK → chats, NOT NULL)
├─ role (ENUM: user, assistant)
├─ content (TEXT, question or answer)
├─ source_documents (UUID[], JSON array of doc IDs)
├─ feedback (ENUM: positive, negative; nullable)
├─ ideal_answer (TEXT, nullable — provided when feedback is negative)
├─ token_count (INTEGER, nullable — tokens used for this response)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
└─ (chat_id)
```

---

## Key Constraints

### Multi-Tenancy Security

**CRITICAL:** Always filter by `tenant_id` on every query.

```python
# Example: Get embeddings for a tenant's search
SELECT chunk_text, similarity_score
FROM embeddings
WHERE document_id IN (
  SELECT id FROM documents
  WHERE tenant_id = $1  # ← ALWAYS filter by tenant_id
)
ORDER BY vector <-> query_vector
LIMIT 3;
```

### Foreign Keys
- `documents.tenant_id` → `tenants.id` (CASCADE DELETE)
- `embeddings.document_id` → `documents.id` (CASCADE DELETE)
- `chats.tenant_id` → `tenants.id` (CASCADE DELETE)
- `messages.chat_id` → `chats.id` (CASCADE DELETE)
- `tenants.user_id` → `users.id` (CASCADE DELETE)
- `contact_sessions.tenant_id` → `tenants.id` (CASCADE DELETE)

### Unique Constraints
- `users.email` UNIQUE
- `tenants.api_key` UNIQUE
- `tenants.public_id` UNIQUE

### Not Null Constraints
- `users.email`, `users.password_hash`
- `tenants.user_id`, `tenants.name`, `tenants.api_key`
- `documents.tenant_id`, `documents.filename`, `documents.file_type`
- `embeddings.document_id`, `embeddings.chunk_text`, `embeddings.vector`
- `chats.tenant_id`
- `messages.chat_id`, `messages.role`, `messages.content`

---

## Migration Strategy (Alembic)

Migrations live under `backend/migrations/versions/` (~40 revisions as of April 2026). `alembic.ini` points `script_location = backend/migrations`. Revision IDs are short snake_case and must fit the 32-char `alembic_version.version_num` column (see `AGENTS.md` → Alembic safety rule).

```
backend/migrations/
├─ versions/
│  ├─ 3e6c7b506784_init.py
│  ├─ fi_disc_v1.py
│  ├─ qa_url_answers_v1.py
│  ├─ phase4_user_sessions_active_v1.py
│  ├─ phase4_message_embeddings_v1.py
│  └─ …
└─ env.py
```

Each migration is atomic and, wherever practical, reversible. Railway runs `alembic upgrade head` on every deploy via the Procfile `release` step.

---

**Next:** See `03-tech-stack.md` for technology choices.
