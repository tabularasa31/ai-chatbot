# MVP Scope & Database Schema

---

## MVP Scope

### What's INCLUDED ✅

**Backend:**
- User authentication (email/password + JWT)
- Client management (create, generate API key)
- Document upload (PDF, Markdown, Swagger/OpenAPI)
- Document parsing & text extraction
- Embedding creation (OpenAI API)
- Vector search (similarity search with pgvector)
- RAG chat endpoint (Q&A generation)
- Chat history logging

**Frontend:**
- Login/signup page
- Dashboard (show API key, settings)
- Document manager (upload, list, delete)
- Chat logs viewer
- Responsive design (Tailwind CSS)

**Widget:**
- Embeddable chat widget (iframe)
- Send question → get answer
- Basic styling (no customization)

**Database:**
- PostgreSQL with pgvector extension
- Users, Clients, Documents, Embeddings, Messages tables
- Migrations (Alembic)

### What's EXCLUDED (v2) ❌

- User customization (colors, logos, tone)
- Team collaboration
- Analytics dashboard
- Slack/Email notifications
- Webhooks
- Fine-tuning
- Multiple LLM options
- Payment system
- Advanced security (SSO, 2FA)

---

## Database Schema

### Tables

#### Users (Platform Users)
```sql
users
├─ id (PK, UUID)
├─ email (UNIQUE, NOT NULL)
├─ password_hash (NOT NULL)
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)
```

#### Clients (Companies)
```sql
clients
├─ id (PK, UUID)
├─ user_id (FK → users, NOT NULL)
├─ name (VARCHAR, NOT NULL)
├─ api_key (UNIQUE, NOT NULL, 32-char random)
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
├─ chunk_text (TEXT, 500-char chunk)
├─ vector (vector(1536), pgvector from OpenAI)
├─ metadata (JSONB: {chunk_index, offset})
├─ created_at (TIMESTAMP)
└─ updated_at (TIMESTAMP)

Indexes:
├─ (document_id)
└─ (vector) - pgvector index for similarity search
```

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
├─ feedback (ENUM: approved, rejected, edited; nullable)
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

### Not Null Constraints
- `users.email`, `users.password_hash`
- `clients.user_id`, `clients.name`, `clients.api_key`
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

## Seed Data for Testing

```sql
-- Test user
INSERT INTO users (email, password_hash)
VALUES ('test@example.com', '$2b$12$...');

-- Test client
INSERT INTO clients (user_id, name, api_key)
VALUES (1, 'Test Company', 'abc123xyz...');

-- Test document
INSERT INTO documents (client_id, filename, file_type, parsed_text, status)
VALUES (1, 'FAQ.pdf', 'pdf', 'Q: How to reset password?\nA: Go to Settings...', 'ready');

-- Test embeddings (with mock vectors for testing)
INSERT INTO embeddings (document_id, chunk_text, vector)
VALUES (1, 'How to reset password...', '[0.1, 0.2, ..., 0.0]'::vector);
```

---

**Next:** See `03-tech-stack.md` for technology choices.
