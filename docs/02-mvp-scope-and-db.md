# MVP Scope & Database Schema

---

## MVP Scope

### What's INCLUDED ✅

**Backend:**
- User authentication (email/password + JWT + email verification via Brevo)
- Tenant → Workspace → Bot hierarchy: tenant owns billing/API key/OpenAI key; workspace scopes knowledge and KYC secret; bot is the per‑channel behavior unit
- Bot management (per‑workspace bots with disclosure / response‑detail config)
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
- Tenants, Workspaces, Users, Bots, Documents, UrlSources, Embeddings, Chats, Messages, ContactSessions, EscalationTickets, Gap* (Analyzer), PiiEvents
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

### Entity model

```
User            Tenant              Workspace             Bot
────            ──────              ─────────             ───
id              id                  id                    id
email           name                tenant_id (FK)        workspace_id (FK)
password_hash   api_key             name                  public_id  ← widget access
tenant_id (FK)  openai_api_key      kyc_secret_key        name
                is_active           kyc_secret_previous   disclosure_config
                                    kyc_secret_expires    is_active
                                    kyc_secret_hint
                                    settings
                                    is_active
```

Separation of concerns:

- **Tenant** → *ownership*. Billing, tenant‑wide API key (`X-Api-Key` server‑to‑server), OpenAI API key.
- **Workspace** → *context*. Knowledge scope (documents, URL sources, chats, gap analysis) and KYC signing secret. MVP: exactly **one** workspace per tenant, created automatically on signup; UI hides the selector.
- **Bot** → *behavior*. Disclosure / response‑detail config, per‑channel tuning. MVP: exactly **one** bot per workspace (schema allows N, UI is single‑bot). Widget resolves by `Bot.public_id` via the `data-bot-id` attribute.
- **public_id** → *access*. Lives on Bot only. Tenant has no `public_id`.

All content tables (`documents`, `url_sources`, `chats`, `messages` via chat, `quick_answers`, `contact_sessions`, `escalation_tickets`, `gap_*`) scope by **`workspace_id`**, not `tenant_id`.

### Tables

#### Users (Platform Users)
```sql
users
├─ id (PK, UUID)
├─ email (UNIQUE, NOT NULL)
├─ password_hash (NOT NULL)
├─ tenant_id (FK → tenants, nullable — set on email verification)
├─ is_email_verified (BOOLEAN, default=false)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)
```

#### Tenants (ownership)
```sql
tenants
├─ id (PK, UUID)
├─ name (VARCHAR, NOT NULL)
├─ api_key (UNIQUE, NOT NULL, 32-char random — X-Api-Key for server-to-server)
├─ openai_api_key (VARCHAR, encrypted, nullable)
├─ is_active (BOOLEAN)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)
```

> Historical note: the model was previously named `Client`; all schema/API surfaces now use **tenant** / `tenant_id`. See `backend/models.py:Tenant`.
> Tenant has no `public_id` — widget access lives on `Bot.public_id`.

#### Workspaces (knowledge + KYC context)
```sql
workspaces
├─ id (PK, UUID)
├─ tenant_id (FK → tenants ON DELETE CASCADE, NOT NULL)
├─ name (VARCHAR, NOT NULL)
├─ kyc_secret_key (+ previous + previous_expires_at + hint) — FI-KYC signing secrets (encrypted)
├─ settings (JSONB, default={})
├─ is_active (BOOLEAN)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
└─ (tenant_id)
```

MVP: `bootstrap_tenant_for_user` creates one `Tenant` + one default `Workspace` + one default `Bot` in a single transaction. UI treats the single workspace as implicit and hides the selector.

#### Bots (behavior)
```sql
bots
├─ id (PK, UUID)
├─ workspace_id (FK → workspaces ON DELETE CASCADE, NOT NULL)
├─ public_id (VARCHAR(21), UNIQUE — widget access, matches data-bot-id)
├─ name (VARCHAR, NOT NULL)
├─ disclosure_config (JSONB, nullable)
├─ is_active (BOOLEAN)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (workspace_id)
└─ (public_id) UNIQUE
```

#### Documents (Uploaded Files)
```sql
documents
├─ id (PK, UUID)
├─ workspace_id (FK → workspaces, NOT NULL)
├─ filename (VARCHAR, NOT NULL)
├─ content_hash (VARCHAR(64), nullable) — SHA-256 hex of raw file bytes; NULL for URL-crawled pages
├─ file_type (ENUM: pdf, markdown, swagger)
├─ original_content (TEXT, raw file content)
├─ parsed_text (TEXT, extracted text)
├─ status (ENUM: processing, ready, error)
├─ error_message (TEXT, nullable)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (workspace_id)
├─ (status)
└─ (tenant_id, content_hash) — deduplication index for file uploads
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
├─ workspace_id (FK → workspaces, NOT NULL)
├─ bot_id (FK → bots, NOT NULL)
├─ session_id (UUID, unique per visitor session)
├─ user_context (JSONB, nullable — FI-KYC identified session payload fields)
├─ tokens_used (INTEGER)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (workspace_id)
└─ (bot_id)
```

#### Contact sessions (FI-KYC identified-user lifecycle)
```sql
contact_sessions   -- cross-session history for identified users
├─ id (PK, UUID)
├─ workspace_id (FK → workspaces ON DELETE CASCADE, NOT NULL)
├─ contact_id (VARCHAR(255), NOT NULL — the KYC token's user_id)
├─ email, name, plan_tier, audience_tag (nullable)
├─ session_started_at, session_ended_at (nullable)
├─ conversation_turns (INTEGER, default 0)
└─ created_at (TIMESTAMP)

Indexes:
├─ ix_contact_sessions_workspace_contact (workspace_id, contact_id)
└─ uq_contact_sessions_workspace_contact_active — partial unique on
   (workspace_id, contact_id) WHERE session_ended_at IS NULL
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

**CRITICAL:** Always filter by `workspace_id` on every content query. Tenant‑scoped tables are only `workspaces` (many per tenant), `bots` (via workspace), `users` (members of a tenant), and tenant billing/credentials.

```python
# Example: Get embeddings for a workspace's search
SELECT chunk_text, similarity_score
FROM embeddings
WHERE document_id IN (
  SELECT id FROM documents
  WHERE workspace_id = $1  # ← ALWAYS filter by workspace_id
)
ORDER BY vector <-> query_vector
LIMIT 3;
```

### Foreign Keys
- `users.tenant_id` → `tenants.id` (ON DELETE SET NULL)
- `workspaces.tenant_id` → `tenants.id` (CASCADE DELETE)
- `bots.workspace_id` → `workspaces.id` (CASCADE DELETE)
- `documents.workspace_id` → `workspaces.id` (CASCADE DELETE)
- `embeddings.document_id` → `documents.id` (CASCADE DELETE)
- `chats.workspace_id` → `workspaces.id` (CASCADE DELETE)
- `chats.bot_id` → `bots.id` (CASCADE DELETE)
- `messages.chat_id` → `chats.id` (CASCADE DELETE)
- `contact_sessions.workspace_id` → `workspaces.id` (CASCADE DELETE)

### Unique Constraints
- `users.email` UNIQUE
- `tenants.api_key` UNIQUE
- `bots.public_id` UNIQUE

### Not Null Constraints
- `users.email`, `users.password_hash`
- `tenants.name`, `tenants.api_key`
- `workspaces.tenant_id`, `workspaces.name`
- `bots.workspace_id`, `bots.public_id`, `bots.name`
- `documents.workspace_id`, `documents.filename`, `documents.file_type`
- `embeddings.document_id`, `embeddings.chunk_text`, `embeddings.vector`
- `chats.workspace_id`, `chats.bot_id`
- `messages.chat_id`, `messages.role`, `messages.content`

---

## Current gap (target vs reality, 2026‑04)

The schema above is the **target**. As of April 2026 the code still reflects the pre‑Workspace model:

| Area | Target | Current |
|---|---|---|
| Workspace entity | dedicated table, FK on every content row | **missing** |
| Bot → parent | `workspace_id` | `tenant_id` ([backend/models.py:275](backend/models.py:275)) |
| Content scope (`documents`, `url_sources`, `chats`, `quick_answers`, `contact_sessions`, `escalation_tickets`, `gap_*`) | `workspace_id` | `tenant_id` |
| KYC secrets | on `workspaces` | on `tenants` ([backend/models.py:218](backend/models.py:218)) |
| Tenant public_id | not present | still exists ([backend/models.py:210](backend/models.py:210)) |

Closing this gap is tracked as a single hard‑cutover migration (no production data to preserve). See `docs/adr/0001-tenant-workspace-bot.md`.

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
