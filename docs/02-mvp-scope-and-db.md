# MVP Scope & Database Schema

---

## MVP Scope

### What's INCLUDED ✅

**Backend:**
- User authentication (email/password + JWT + email verification)
- Client management (create, generate API key, store OpenAI API key)
- Document upload (PDF, Markdown, Swagger/OpenAPI)
- Document parsing & text extraction
- Embedding creation (OpenAI API, via client's own key)
- Vector search (similarity search with pgvector)
- RAG chat endpoint (Q&A generation)
- Chat history logging with sessions
- Feedback system (👍/👎 + optional ideal answer)
- Token usage tracking per client
- Debug mode (show which chunks were used)
- Admin metrics

**Frontend:**
- Login/signup page
- Dashboard (API key, settings, OpenAI key setup)
- Document manager (upload, list, delete)
- Chat logs viewer with feedback
- Responsive design (Tailwind CSS)

**Widget:**
- Embeddable chat widget (iframe)
- Send question → get answer
- Basic styling (no customization)

**Database:**
- PostgreSQL with pgvector extension
- Users, Clients, Documents, Embeddings, Chats, Messages tables
- Migrations (Alembic)

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

#### Clients (Companies)
```sql
clients
├─ id (PK, UUID)
├─ public_id (VARCHAR(32), UNIQUE — used in widget/embed URLs)
├─ user_id (FK → users, NOT NULL)
├─ name (VARCHAR, NOT NULL)
├─ api_key (UNIQUE, NOT NULL, 32-char random)
├─ openai_api_key (VARCHAR, NOT NULL — client's own OpenAI key)
├─ settings (JSONB, default={})
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)
```

#### Documents (Uploaded Files)
```sql
documents
├─ id (PK, UUID)
├─ client_id (FK → clients, NOT NULL)
├─ filename (VARCHAR, NOT NULL)
├─ file_type (ENUM: pdf, markdown, swagger)
├─ original_content (TEXT, raw file content)
├─ parsed_text (TEXT, extracted text)
├─ status (ENUM: processing, ready, error)
├─ error_message (TEXT, nullable)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (client_id)
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
├─ client_id (FK → clients, NOT NULL)
├─ session_id (UUID, unique per visitor session)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
└─ (client_id)
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

**CRITICAL:** Always filter by `client_id` on every query.

```python
# Example: Get embeddings for a client's search
SELECT chunk_text, similarity_score
FROM embeddings
WHERE document_id IN (
  SELECT id FROM documents 
  WHERE client_id = $1  # ← ALWAYS filter by client_id
)
ORDER BY vector <-> query_vector
LIMIT 3;
```

### Foreign Keys
- `documents.client_id` → `clients.id` (CASCADE DELETE)
- `embeddings.document_id` → `documents.id` (CASCADE DELETE)
- `chats.client_id` → `clients.id` (CASCADE DELETE)
- `messages.chat_id` → `chats.id` (CASCADE DELETE)
- `clients.user_id` → `users.id` (CASCADE DELETE)

### Unique Constraints
- `users.email` UNIQUE
- `clients.api_key` UNIQUE
- `clients.public_id` UNIQUE

### Not Null Constraints
- `users.email`, `users.password_hash`
- `clients.user_id`, `clients.name`, `clients.api_key`, `clients.openai_api_key`
- `documents.client_id`, `documents.filename`, `documents.file_type`
- `embeddings.document_id`, `embeddings.chunk_text`, `embeddings.vector`
- `chats.client_id`
- `messages.chat_id`, `messages.role`, `messages.content`

---

## Migration Strategy (Alembic)

```
alembic/
├─ versions/
│  ├─ 001_init_users_clients.py
│  ├─ 002_documents.py
│  ├─ 003_embeddings_with_pgvector.py
│  ├─ 004_chats_and_messages.py
│  └─ 005_indexes.py
└─ env.py
```

Each migration is atomic and reversible.

---

**Next:** See `03-tech-stack.md` for technology choices.
