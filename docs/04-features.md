# Chat9 — Product Features

A complete description of every implemented capability. Written for a technical reader who has no prior context on the codebase.

**Last updated:** 2026-03-25  
**Status:** Production (getchat9.live)

---

## What Chat9 is

Chat9 is a **multi-tenant SaaS platform** that lets businesses embed an AI support chatbot on their website. Each customer ("tenant") uploads their own documentation, connects their own OpenAI API key, and gets a ready-to-embed chat widget. The bot answers user questions by searching those documents with RAG (Retrieval-Augmented Generation).

---

## 1. Authentication & Accounts

### Registration and login

Users sign up with email + password. Passwords are hashed with bcrypt. On login, the server issues a **JWT access token** (stateless, no server-side session). All dashboard API calls carry this token in `Authorization: Bearer <token>`.

### Email verification

After registration, Brevo sends a verification link. The user must verify before they can use write-only endpoints (e.g. update OpenAI key, change settings). Unverified users see a warning banner in the dashboard.

### Forgot password

Full reset flow:
1. User enters their email on `/forgot-password`
2. Backend sends a reset link via Brevo (rate-limited: 3 requests/hour per email)
3. Link contains a one-time token (1-hour TTL)
4. User sets a new password at `/reset-password`

### Admin flag

Users can have `is_admin = true`. Admins see an **Admin** section in the **sidebar** (app shell) and can access platform-wide metrics (`GET /admin/metrics/*`) — total users, sessions, tokens used across all tenants.

---

## 2. Tenant (Client) Management

Each registered user has exactly one **Client** record. The client is the unit of isolation — all documents, chats, API keys, and settings belong to a client.

### API key

Every client gets a random 32-character `api_key` at registration. This key is used for **server-to-server** chat calls (`X-Api-Key` header) and widget authentication. It can be rotated if compromised (delete + recreate client).

### Public ID

A human-readable `public_id` (format: `ch_xxxxxxxxxxxxxxxx`) is used in the embeddable widget snippet. It is safe to expose in public HTML — it only identifies the client, grants no write access.

### OpenAI API key (per tenant)

Each client provides their own OpenAI key. It is **encrypted at rest** (AES-GCM via `backend/core/crypto.py`). The platform never uses a shared OpenAI key — no markup, no shared quota. The key is decrypted in memory only when making an OpenAI API call.

---

## 3. Document Upload & Processing

### Supported formats

| Format | Parser | Notes |
|--------|--------|-------|
| PDF | pypdf | Text extraction, multi-page |
| Markdown | markdown lib | CommonMark |
| Swagger / OpenAPI | PyYAML / json | Paths, descriptions, schemas |

Upload endpoint: `POST /documents` (multipart/form-data, max 50 MB).

### Processing pipeline

1. File is saved and parsed to `parsed_text` (plain text)
2. Document status → `ready`
3. User triggers embedding via dashboard (or automatically on upload)
4. Status → `embedding` (background task starts)
5. Status → `ready` (embedding done) or `error`

### Asynchronous embedding (FI-021)

Embedding is expensive for large documents (20+ chunks → multiple OpenAI calls → seconds of latency). To avoid HTTP timeouts:

- `POST /embeddings/documents/{id}` returns **`202 Accepted`** immediately
- A `FastAPI BackgroundTask` runs the actual work in the same process but after the response is sent
- The background task opens its own database session (independent of the request session)
- Document status transitions: `ready → embedding → ready` (success) or `error` (failure)
- The **Knowledge hub** UI (`/knowledge`) **polls** `GET /documents/{id}` every 2 seconds and updates the status badge in real time (timeout: 120 seconds)

### Chunking (FI-009, TD-033)

Documents are split into overlapping chunks before embedding. Chunk boundaries follow sentence endings (not character positions) to preserve semantic coherence.

Optimal parameters differ by document type:

| Document type | Chunk size | Overlap |
|---------------|-----------|---------|
| PDF | 1000 chars | 1 sentence |
| Markdown | 700 chars | 1 sentence |
| Swagger / YAML | 500 chars | 0 sentences |
| Logs *(planned)* | 300 chars | 0 sentences |
| Code *(planned)* | 600 chars | 1 sentence |

Each chunk stores: `chunk_text`, `chunk_index`, `char_offset`, `char_end`, `filename`, `file_type`.

### Document health check (FI-032)

After embedding, the system runs a GPT-based quality analysis on the document:

- Checks for: missing content, very short chunks, encoding issues, low information density
- Produces a `health_score` (0–100) and a list of `warnings` with severity levels
- Visible in the dashboard as a colored dot (green / amber / red) next to each document
- User can manually trigger a re-check at any time via the **Re-check** button
- API: `GET /documents/{id}/health`, `POST /documents/{id}/health/run`

### URL knowledge sources (FI-URL v1)

The Knowledge hub can also index a documentation website from a root URL.

How it works:

1. User adds a root URL in **Knowledge**
2. Backend validates the URL and runs a preflight reachability check
3. The crawler discovers pages on the same domain (sitemap + HTML links)
4. Each readable page is extracted into text, chunked, embedded, and stored as `DocumentType.url`
5. The source keeps crawl metadata and a run history for the dashboard

Current v1 limits and rules:

- Only `http` / `https`
- Same-domain crawling only
- Shared knowledge capacity: maximum **100 documents per client** across uploaded files and indexed URL pages
- Maximum discovery depth of **3**
- Schedules: `daily`, `weekly`, `manual`
- Optional exclusion patterns to skip paths

Security hardening in v1:

- The crawler rejects localhost, loopback, private, link-local, multicast, reserved, and unspecified IP ranges
- Redirects are validated hop-by-hop instead of being followed automatically
- Requests ignore environment proxy variables (`trust_env=False`)
- Oversized responses are rejected before indexing

User-visible states:

- `queued` — ready to start
- `indexing` — crawl in progress
- `ready` — successfully indexed
- `paused` — blocked until the client config is fixed (for example, missing OpenAI key)
- `error` — crawl failed

API:

- `GET /documents/sources`
- `POST /documents/sources/url`
- `GET /documents/sources/{source_id}`
- `PATCH /documents/sources/{source_id}`
- `POST /documents/sources/{source_id}/refresh`
- `DELETE /documents/sources/{source_id}`

Contract notes:

- `exclusions` accepts up to `50` patterns, each up to `255` characters.
- `recent_runs[].failed_urls` uses a fixed object shape: `{ "url": string, "reason": string }`.
- Mutating URL source actions (`create`, `edit`, `refresh`, `delete`) require a verified user.

---

## 4. Search & Retrieval

### Vector search (pgvector + HNSW)

Each chunk is embedded with `text-embedding-3-small` (1536 dimensions) and stored in PostgreSQL with the `pgvector` extension. Similarity search uses **cosine distance** (`<=>` operator) on an **HNSW index** — sub-millisecond lookup even with millions of vectors.

### Hybrid search: BM25 + RRF (FI-008)

Pure vector search struggles with exact keyword matches (product names, error codes). Chat9 combines two signals:

1. **Vector search** — semantic similarity (pgvector, top `2×top_k` candidates)
2. **BM25** — keyword frequency ranking (`rank-bm25` library, run over all client chunks in memory)

The two ranked lists are merged with **Reciprocal Rank Fusion** (RRF, k=60): a chunk scores higher if it appears near the top of both lists. This reliably outperforms either method alone on technical documentation queries.

> Note: in the test environment (SQLite), pgvector is not available — tests use Python cosine similarity only. BM25 is not applied in tests.

---

## 5. RAG Chat Pipeline

### How a chat turn works

```
User message
  ↓
PII redaction (regex)
  ↓
Hybrid search → top-k chunks
  ↓
Build RAG prompt (system + context + history + question)
  ↓
gpt-4o-mini → answer
  ↓
Answer validation (second gpt-4o-mini call)
  ↓
Store message → return response
```

### PII redaction (FI-043)

Before any text is sent to OpenAI, the user's message is passed through a regex redactor (`backend/chat/pii.py`). Detected patterns are replaced with neutral placeholders:

| Pattern | Placeholder |
|---------|-------------|
| Email addresses | `[EMAIL]` |
| Phone numbers | `[PHONE]` |
| API keys (common formats) | `[API_KEY]` |
| Credit card numbers | `[CREDIT_CARD]` |

The **original unredacted text** is stored in `messages.content` for the dashboard log. Only placeholders go to OpenAI.

### Answer validation (FI-034)

After generating an answer, a **second LLM call** (`temperature=0`) checks whether the answer is grounded in the retrieved chunks:

- Returns `is_valid` (bool) and `confidence` (0.0–1.0)
- If `is_valid = false` **and** `confidence < 0.4`, the answer is replaced with a safe fallback: *"I don't have enough information to answer this question."*
- Validation errors (e.g. OpenAI timeout) do not block the response — the original answer is returned with `validation_skipped: true`
- Full validation result is visible in `POST /chat/debug` → `debug.validation`

### Chat channels

| Channel | Auth | Endpoint |
|---------|------|----------|
| Dashboard / API | `X-Api-Key` | `POST /chat` |
| Widget (public) | `clientId` (public_id) | `POST /widget/chat` |
| Debug tool | JWT | `POST /chat/debug` |

### Sessions and history

Each conversation is a **session** (UUID). Messages within a session are stored and passed as history in subsequent turns (last N messages). Sessions are scoped to a client — no cross-tenant leakage.

---

## 6. L2 Escalation Tickets (FI-ESC)

When the bot cannot adequately answer, the conversation is **escalated to a human** and a support ticket is created.

### Escalation triggers

| Trigger | What happens |
|---------|-------------|
| Low similarity score | No retrieved chunk is relevant enough |
| No documents | Client has no embedded documents |
| User phrase | Message contains phrases like "talk to a person", "human", "agent" |
| Manual escalation | Client calls `POST /chat/{session_id}/escalate` |

### What happens on escalation

1. An `EscalationTicket` record is created with a sequential number **ESC-####** (per tenant, e.g. ESC-0001)
2. The bot sends a GPT-generated handoff message to the user explaining the situation
3. The owner of the tenant receives an **email notification** (via Brevo) with ticket details
4. The chat session is **closed** — the user sees a banner and the input is disabled
5. The user can initiate a new session at any time

### Ticket inbox (dashboard)

Tenants see all their tickets at `/escalations`:
- Status: `open` / `resolved`
- Trigger type, session link, creation time
- One-click resolve button → `POST /escalations/{id}/resolve`

### Widget UX

- A **"Talk to support"** button appears in the widget after escalation
- A ticket banner shows the ticket number
- Input is locked (chat is ended)
- `POST /widget/escalate` is a public endpoint (no auth required) — the widget can escalate without a JWT

---

## 7. Response Controls / Disclosure (FI-DISC)

Tenants can set a **tenant-wide response detail level** that controls how the bot phrases answers across all channels (widget + API).

| Level | Behaviour |
|-------|-----------|
| **Detailed** | Full technical content — paths, error names, vendor details, stack traces if in docs |
| **Standard** | Plain language — avoids internal paths, tool names, affected-user counts |
| **Corporate** | Polished, non-technical — no ETAs, no deep technical detail; offers support contact for ongoing issues |

The selected level is injected into the RAG system prompt as a hard instruction block. It applies to every chat turn, for every user, on every channel.

**API:** `GET /clients/me/disclosure`, `PUT /clients/me/disclosure`  
**Dashboard:** Settings → Response controls

---

## 8. Widget User Identification (FI-KYC)

By default the widget is **anonymous** — no information about the end user is passed to the bot. Optionally, tenants can pass structured user context via a signed identity token.

### How it works

1. Tenant generates a **signing secret** in the dashboard (Settings → Widget API)
2. On their server, the tenant creates an `identity_token` — a short-lived HMAC-SHA256 signed JWT containing user metadata: `plan_tier`, `locale`, `audience_tag`
3. The widget passes this token to `POST /widget/session/init`
4. Backend validates the signature, stores the context in `chats.user_context`
5. In the RAG prompt, only the safe fields (`plan_tier`, `locale`, `audience_tag`) are included — no raw user PII

### Session modes

| Mode | Description |
|------|-------------|
| `identified` | Token was valid; user context is available |
| `anonymous` | No token provided; standard anonymous session |

### Secret management

- `POST /clients/me/kyc/secret` — generate secret (one-time display)
- `GET /clients/me/kyc/status` — check if secret exists, see hint (first 4 chars)
- `POST /clients/me/kyc/rotate` — issue new secret; old one stays valid for **1 hour** (grace period for rolling deployments)
- Secret is encrypted at rest (same mechanism as OpenAI key)

---

## 9. Embeddable Widget

### How embedding works

Tenants copy a snippet from the **Dashboard** (and optionally from docs). The canonical pattern is a **script tag** whose URL includes `clientId` (the client **`public_id`**, `ch_…`). If the chat app (Next.js) is hosted on a **different origin** than the API that serves `embed.js`, set `window.Chat9Config.widgetUrl` to the app origin so the iframe loads the widget UI from the correct host.

Example (placeholders — the Dashboard fills in your real `clientId` and URLs):

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://<your-api-host>/embed.js?clientId=ch_xxxxxxxxxxxxxxxx"></script>
```

If the frontend and API share the same origin, you can omit `Chat9Config` and use a single script tag with `?clientId=…` only.

`embed.js` (vanilla JS, served from the API):
- Reads `clientId` from the **script URL** query string (required)
- Uses `window.Chat9Config?.widgetUrl` if set, otherwise the script’s origin, as the **iframe base URL**
- Appends a fixed-position container and an `<iframe>` pointing to `/widget?clientId=…&locale=<navigator.language>`
- The iframe renders the full `ChatWidget` React component

The iframe isolation means the widget has **no access to the host page DOM** — clean CORS boundary, no XSS risk.

### Widget features

- Streaming-style message display
- Session continuity within a page load
- Escalation button → triggers `POST /widget/escalate`
- Ticket banner after escalation
- Locale passed automatically (`navigator.language`)
- "Powered by Chat9 →" footer (links to getchat9.live)

### Rate limits

All widget endpoints are rate-limited to **20 requests/minute per IP**:
- `POST /widget/session/init`
- `POST /widget/chat`
- `POST /widget/escalate`

---

## 10. Dashboard

The web dashboard at `getchat9.live` is a Next.js 14 app. Authenticated pages use a **left sidebar** for navigation (main items, **SETTINGS**, and **Admin** for `is_admin` users); the top bar shows brand, email, and logout.

| Page / route | What it shows |
|--------------|---------------|
| **Dashboard** (`/dashboard`) | **API key** (server-to-server `X-Api-Key`), **embed code** snippet (`public_id` / `ch_…`); banner linking to Agents if OpenAI key is missing |
| **Knowledge** (`/knowledge`) | Upload files, add URL sources, trigger embeddings/crawls, health indicators, delete; unified indexed sources table (replaces legacy `/documents`) |
| **Agents** (`/settings`) | Per-tenant **OpenAI API key** (encrypted), save/update/remove |
| **Logs** (`/logs`) | Full chat history across sessions; thumbs up/down feedback |
| **Review** (`/review`) | Bad answers (thumbs down) with ideal answer input |
| **Escalations** (`/escalations`) | L2 ticket inbox; resolve tickets |
| **Debug** (`/debug`) | Run RAG debug; answer + retrieval table with chunk previews and scores (code blocks use inline copy) |
| **Response controls** (`/settings/disclosure`) | Disclosure level (Detailed / Standard / Corporate) |
| **Widget API** (`/settings/widget`) | Generate / rotate KYC signing secret; Node.js token example |
| **Admin** (`/admin/metrics`, admins only) | Platform-wide metrics |

---

## 11. Security

| Area | Implementation |
|------|---------------|
| Authentication | JWT (HS256), bcrypt passwords |
| Data isolation | All queries scoped by `client_id`; no cross-tenant access possible |
| API key storage | AES-GCM encrypted at rest |
| KYC secret storage | AES-GCM encrypted at rest |
| PII protection | Regex redaction before all OpenAI calls |
| Rate limiting | slowapi (per-IP): chat 30/min, search 30/min, validate 20/min, widget 20/min |
| CORS | Production allowlist; widget served via iframe (same-origin for widget API calls) |

---

## 12. Infrastructure

```
getchat9.live (Vercel, Next.js 14)
  ↕  HTTPS
api.getchat9.live (Railway, FastAPI + Uvicorn)
  ↕  SQLAlchemy
PostgreSQL 15 + pgvector extension (Railway managed DB)
  ↕
OpenAI API  (tenant's own key)
Brevo       (transactional email: verification, password reset, escalation notifications)
```

**Git branching:**
- `main` — development; no auto-deploy
- `deploy` — production; Vercel and Railway listen to this branch

---

*For the chronological development history, see [`PROGRESS.md`](./PROGRESS.md).*  
*For the feature registry with code pointers, see [`IMPLEMENTED_FEATURES.md`](./IMPLEMENTED_FEATURES.md).*  
*For the tech stack details, see [`03-tech-stack.md`](./03-tech-stack.md).*
