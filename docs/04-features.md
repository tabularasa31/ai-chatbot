# Chat9 — Product Features

A complete description of every implemented capability. Written for a technical reader who has no prior context on the codebase.

**Last updated:** 2026-04-07 (verification-first dashboard access + bot-scoped debug contract)  
**Status:** Production (getchat9.live)

---

## What Chat9 is

Chat9 is a SaaS platform that lets businesses embed an AI support bot on their website. Each customer owns one client in the backend model and one bot in the product/UI model. Customers upload their own documentation, connect their own OpenAI API key, and get a ready-to-embed chat widget. The bot answers user questions by searching those documents with RAG (Retrieval-Augmented Generation).

---

## 1. Authentication & Accounts

### Registration and login

Users sign up with email + password. Passwords are hashed with bcrypt. On login, the server issues a **JWT access token** (stateless, no server-side session). All dashboard API calls carry this token in `Authorization: Bearer <token>`.

### Email verification

After registration, Brevo sends a verification link. The user must verify before they can use the authenticated dashboard or any tenant-scoped JWT API routes. Successful `POST /auth/verify-email` also provisions the user's single client/workspace on the backend, so the authenticated app no longer relies on frontend fallback creation.

In practice this means:

- public onboarding routes such as register, login, verify-email, forgot-password, and reset-password stay available without a verified dashboard session
- dashboard/API routes that operate on a tenant workspace use `require_verified_user`
- public widget flows and eval/widget-specific auth paths are not part of this rule unless they explicitly use the dashboard JWT stack

### Forgot password

Full reset flow:
1. User enters their email on `/forgot-password`
2. Backend sends a reset link via Brevo (rate-limited: 3 requests/hour per email)
3. Link contains a one-time token (1-hour TTL)
4. User sets a new password at `/reset-password`

### Admin flag

Users can have `is_admin = true`. Admins see an **Admin** section in the **sidebar** (app shell) and can access platform-wide metrics (`GET /admin/metrics/*`) — total users, sessions, tokens used across all clients.

---

## 2. Tenant (Client) Management

Each registered user has exactly one **Client** record. The client is the unit of isolation — all documents, chats, API keys, and settings belong to a client. This one-to-one rule is enforced in both application logic and the database schema.

### API key

Every client gets a random 32-character `api_key` when the workspace client is provisioned during successful email verification. This key is used for **server-to-server** chat calls (`X-Api-Key` header) and widget authentication. It can be rotated if compromised (delete + recreate client).

### Public ID

A human-readable `public_id` (format: `ch_xxxxxxxxxxxxxxxx`) is used in the embeddable widget snippet. It is safe to expose in public HTML — it only identifies the client, grants no write access.

### OpenAI API key (per client)

Each client provides their own OpenAI key. It is **encrypted at rest** (AES-GCM via `backend/core/crypto.py`). The platform never uses a shared OpenAI key — no markup, no shared quota. The key is decrypted in memory only when making an OpenAI API call.

---

## 3. Document Upload & Processing

### Supported formats

| Format | Parser | Notes |
|--------|--------|-------|
| PDF | pypdf | Text extraction, multi-page |
| Markdown | markdown lib | CommonMark |
| Swagger / OpenAPI | PyYAML / json | JSON/YAML parse, OpenAPI validation, endpoint-aware rendering |

Upload endpoint: `POST /documents` (multipart/form-data, max 50 MB).
Supported OpenAPI extensions: `.json`, `.yaml`, `.yml`.

### Processing pipeline

1. File is saved and parsed to `parsed_text`
   - PDF / Markdown become plain text
   - Swagger / OpenAPI is normalized into a deterministic human-readable preview
2. Document status → `ready`
3. User triggers embedding via dashboard (or automatically on upload)
4. Status → `embedding` (background task starts)
5. Status → `ready` (embedding done) or `error`

For Swagger / OpenAPI documents, the embedding pipeline does **not** embed raw JSON/YAML. Instead it:

1. Parses JSON or YAML into an object
2. Validates that it is a Swagger/OpenAPI spec with supported operations
3. Renders one primary chunk per `method + path`
4. Adds request/response schema detail chunks for rich operations
5. Stores chunk metadata such as `path`, `method`, `operation_id`, `tags`, `deprecated`, `content_types`, and `auth_schemes`

URL sources can also be auto-routed into the same Swagger/OpenAPI pipeline when fetched content is structured JSON/YAML and matches OpenAPI heuristics (`openapi`, `swagger`, or `paths`), followed by semantic validation.

### Asynchronous embedding (FI-021)

Embedding is expensive for large documents (20+ chunks → multiple OpenAI calls → seconds of latency). To avoid HTTP timeouts:

- `POST /embeddings/documents/{id}` returns **`202 Accepted`** immediately
- A `FastAPI BackgroundTask` runs the actual work in the same process but after the response is sent
- The background task opens its own database session (independent of the request session)
- Document status transitions: `ready → embedding → ready` (success) or `error` (failure)
- The **Knowledge hub** UI (`/knowledge`) **polls** `GET /documents/{id}` every 2 seconds and updates the status badge in real time (timeout: 120 seconds)

### Chunking (FI-009, TD-033)

Documents are split into chunks before embedding. For text documents, chunk boundaries follow sentence endings (not character positions) to preserve semantic coherence.

Optimal parameters differ by document type:

| Document type | Chunk size | Overlap |
|---------------|-----------|---------|
| PDF | 1000 chars | 1 sentence |
| Markdown | 700 chars | 1 sentence |
| Swagger / OpenAPI | Operation-aware chunks | No sentence overlap between operations |
| Logs *(planned)* | 300 chars | 0 sentences |
| Code *(planned)* | 600 chars | 1 sentence |

Swagger/OpenAPI chunking rules:

- Primary unit: one API operation (`method + path`)
- No sliding-window overlap between operations
- Rich operations may emit secondary chunks for request schema and response schema detail
- Secondary chunks repeat the endpoint header for retrieval context, but do not duplicate neighbouring operations

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
6. Users can delete a single indexed page from a source; that page is removed from Knowledge and excluded from future refreshes for the same source

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
- `DELETE /documents/sources/{source_id}/pages/{document_id}`
- `DELETE /documents/sources/{source_id}`

Contract notes:

- `exclusions` accepts up to `50` patterns, each up to `255` characters.
- Deleting a single URL-derived page adds its `source_url` to a persistent manual exclusion list for that source, so the crawler does not recreate it on later refreshes.
- `recent_runs[].failed_urls` uses a fixed object shape: `{ "url": string, "reason": string }`.
- Mutating URL source actions (`create`, `edit`, `refresh`, `delete`, `delete page`) require a verified user.
- Read-side dashboard routes for documents, knowledge, embeddings, search, chat logs/history/feedback, escalations, and gap analyzer now also require a verified user, keeping the whole tenant workspace behind the same verification boundary.

---

## 4. Search & Retrieval

### Vector search (pgvector + HNSW)

Each chunk is embedded with `text-embedding-3-small` (1536 dimensions) and stored in PostgreSQL with the `pgvector` extension. Similarity search uses **cosine distance** (`<=>` operator) on an **HNSW index** — sub-millisecond lookup even with millions of vectors.

### Hybrid search: BM25 + RRF (FI-008)

Pure vector search struggles with exact keyword matches (product names, error codes). Chat9 combines two signals:

1. **Vector candidate acquisition** — semantic similarity (`pgvector` in PostgreSQL, Python cosine in SQLite tests)
2. **Candidate-pool BM25** — keyword ranking (`rank-bm25` library, run only over the in-memory candidate pool for the current request)

The two ranked lists are merged with **Reciprocal Rank Fusion** (RRF, k=60), then passed through heuristic reranking and post-ranking selection stages. This reliably outperforms either method alone on technical documentation queries while keeping SQLite/test retrieval close to the production orchestration contract.

Vector remains the recall stage and shared candidate acquisition step. BM25 stays a lexical confirmation / precision stage over that already-built in-memory pool; even when lexical expansion is enabled, it adds repeated lexical scoring over the same shared pool rather than a second corpus-acquisition search.

BM25 lexical expansion is an explicit policy:

- `asymmetric` — default; BM25 evaluates only the original query text
- `symmetric_variants` — BM25 evaluates the lexical-safe normalized variant set, merges hits deterministically, then sends the merged/capped lexical list into RRF

“Symmetric” here applies to query handling only. It does not mean BM25 stops depending on the vector-built pool, and it does not imply that future freer rewrites/paraphrases from vector expansion automatically become valid BM25 inputs. BM25 should continue consuming only lexical-safe normalization variants unless that contract is revisited deliberately.

> Note: in the test environment (SQLite), pgvector is still unavailable, so vector candidates come from Python cosine similarity. After candidate-set construction (acquisition + merge/dedup + truncation), SQLite follows the same BM25 → RRF → reranking → post-ranking orchestration contract as PostgreSQL over that in-memory candidate pool.

### Retrieval observability (FI-115)

Retrieval is instrumented with Langfuse-style traces for both chat requests and direct `/search` calls. The search path now records:

- query variant fan-out (`variant_mode`, `query_variant_count`)
- extra work caused by expansion (`extra_embedded_queries`, `extra_embedding_api_requests`, `extra_vector_search_calls`)
- lexical expansion policy and workload (`bm25_expansion_mode`, `bm25_query_variant_count`, `bm25_variant_eval_count`, `extra_bm25_variant_evals`)
- lexical merge visibility (`bm25_merged_hit_count_before_cap`, `bm25_merged_hit_count_after_cap`)
- timing split (`retrieval_duration_ms`, `query-embedding`, `vector-search`)

The `bm25-search` span keeps the lexical inputs and merged lexical output explicit, including compact winner provenance for merged hits. This makes it possible to compare p50/p95 latency for single-vs-multi vector expansion and asymmetric-vs-symmetric lexical expansion without changing the default retrieval behavior first. The production review template lives in `docs/qa/FI-115-query-variant-cost.md`.

### FAQ match routing (Phase 3)

After injection detection and relevance pre-check, chat requests run a client FAQ semantic match layer before retrieval:

- `faq_direct` — direct FAQ answer allowed only for high-score approved FAQ and a passed cheap applicability guard.
- `faq_context` — FAQ candidates are injected into the system prompt as `VERIFIED FAQ CANDIDATES` hints, then normal retrieval + generation runs.
- `rag_only` — FAQ is ignored for low-score cases.

Embedding generation is done once per request and reused by both FAQ match and retrieval candidate acquisition.

Observability for this layer is emitted through a single `faq_match` span with stable metadata:

- `strategy`, `faq_ids`, `selected_faq_id`
- `top_score` (best raw FAQ candidate score)
- `selected_score` (score of the FAQ selected for direct/context decisioning)
- `direct_guard_used`, `direct_guard_passed`, `decision_reason`
- `retrieval_skipped`, `generation_skipped`

### Controlled clarification layer (MVP)

The chat pipeline can now return three typed outcomes instead of only a plain answer:

- `answer`
- `clarification`
- `partial_with_clarification`

Clarification is intentionally narrow. The bot does not ask follow-up questions by default; it does so only when the current request is not sufficiently answerable under the existing pipeline signals and one of these deterministic trigger families is matched:

- ambiguous intent
- missing critical slot
- low retrieval confidence

Current MVP behavior:

- only one clarification question is asked per active clarification flow
- clarification state is stored in `chats.user_context["clarification_state"]`
- a short follow-up reply is first classified as either:
  - continuation of the active clarification flow
  - new unrelated intent
- continuation replies are normalized back into the standard chat pipeline rather than handled by a separate mini-pipeline
- unsupported ambiguity or missing-slot patterns do not trigger speculative clarification; they fall back to the existing best-effort answer path

Public response contracts:

- `POST /chat` now returns canonical `text`, `message_type`, and optional `clarification`
- legacy `answer` remains as a compatibility alias of `text`
- `POST /widget/chat` returns canonical `text`, `message_type`, and optional `clarification`
- legacy `response` remains as a compatibility alias of `text`
- both channels may return the localized default greeting as a normal `answer` when a brand-new empty conversation starts

Clarification payloads can include:

- `reason`
- `type`
- `options`
- `requested_fields`
- `original_user_message`
- `turn_index`

For the website widget:

- clarification options render as quick-reply buttons
- only the latest assistant clarification keeps active quick replies
- button clicks send the visible option label and, when available, a structured `option_id`

### Knowledge dashboard API and UI (Phase 3)

Knowledge now has dedicated profile/FAQ workflows in addition to document sources:

- API endpoints:
  - `GET/PATCH /knowledge/profile`
  - `GET /knowledge/faq`
  - `POST /knowledge/faq/{id}/approve`
  - `POST /knowledge/faq/{id}/reject`
  - `POST /knowledge/faq/approve-all`
  - `PUT/DELETE /knowledge/faq/{id}`
- `tenant_profiles.extraction_status` is exposed to UI (`pending | done | failed`) and used for polling.
- Dashboard `Knowledge` page supports subtabs:
  - `Documents` (existing table/workflow)
  - `Profile` (`?tab=profile`) for extracted profile review/edit and glossary read-only inspection
  - `FAQ` (`?tab=faq`) for FAQ moderation (accept/reject/edit/approve-all, pending counter, filters, optimistic reject UX)

**Trace sampling:** Environment flag `FULL_CAPTURE_MODE` (default `true`) controls whether adaptive client sampling runs. When `true`, all traces are sampled (after the Langfuse no-op gate); when `false`, the backend uses in-process heuristics (`TRACE_*` settings) as before. Materialized traces carry `sampling_mode` in metadata (`full_capture` vs `adaptive`) and a matching `sampling_mode:*` tag. Settings: `backend/core/config.py`; decision logic: `backend/observability/service.py`. Rollout notes: `docs/07-observability-rollout.md`.

### Retrieval reliability contradiction policy

Retrieval reliability keeps contradiction handling in the final capping stage. Contradiction is always recorded in `signals` and `evidence`, but it only changes the final verdict after corroboration:

- `1` effective contradiction fact on `1` overlap pair stays evidence-only
- `2+` effective contradiction facts on the same overlap pair trigger contradiction cap
- contradiction facts across at least `2` distinct overlap pairs also trigger contradiction cap
- exact duplicate contradiction emissions, including reversed-orientation mirrors, do not increase severity
- contradiction cap always maps directly to `score="low"`

`cap_reason` follows cap precedence rather than only the last score mutation:

- if contradiction threshold is reached, `cap_reason="contradiction"`
- otherwise the existing `source_overlap` cap behavior stays unchanged

Policy table:

| Effective contradiction shape | Final score effect | `cap_reason` |
|---|---|---|
| No facts | Existing behavior only | Existing behavior |
| `1` fact on `1` pair | Evidence-only | Not `contradiction` |
| `2+` facts on `1` pair | Cap to `low` | `contradiction` |
| Facts across at least `2` distinct pairs | Cap to `low` | `contradiction` |
| Threshold reached and `base_score` already `low` | Stay `low` | `contradiction` |

Rollout note:

- sample production-like traces before enabling the policy by default
- review the share of single-fact cases, same-pair `2+` fact cases, multi-pair cases, and the rate of outcomes that would flip versus the old behavior
- define an acceptable flip-rate threshold first; if the observed flip rate exceeds it, require product review or gate rollout behind a feature flag

### Retrieval contradiction observability projection

Canonical reliability continues to answer "what the system believes" via `score`, `cap_reason`, `signals`, and `evidence`. Observability-only contradiction metrics now sit alongside that payload in trace/debug projections to answer "how much contradiction evidence was present and of what shape" without parsing nested evidence manually.

Projection invariants:

- the only contradiction source of truth is final canonical `reliability.evidence.contradiction.pairs`
- despite the historical name, `pairs` is a flat list of fact-level canonical contradiction entries
- each entry already passed canonical filtering, mirror-aware dedupe, and threshold policy before projection reads it
- `contradiction_count` counts canonical fact entries, not logical pairs
- `contradiction_pair_count` aggregates those entries by the same orientation-insensitive logical pair identity used by canonical contradiction dedupe: `(chunk_a_id, chunk_b_id)` ignoring order
- `contradiction_basis_types` is a first-seen traversal-order dedup, not a semantic sort

Current derived fields:

- `contradiction_detected`
- `contradiction_count`
- `contradiction_pair_count`
- `contradiction_basis_types`

Governance note:

- these fields are projection-only observability/debug helpers, not part of the canonical product decision contract
- `contradiction_basis_types` is suitable for aggregation only while `basis` remains a small controlled vocabulary and does not include dynamic values

### Contradiction LLM adjudication (optional shadow layer)

After deterministic overlap + metadata contradiction detection, the backend may optionally run a **shadow** LLM pass that classifies each contradiction **fact** (`basis`, `value_a`, `value_b`) as `confirmed` / `rejected` / `inconclusive`. This does **not** change retrieval `score`, `cap`, or `cap_reason`; deterministic contradiction policy remains the only source of truth for product behavior.

**Two separate data surfaces (do not conflate them):**

| Surface | Where it lives | Serialized in `serialize_reliability`? | Purpose |
|--------|----------------|----------------------------------------|---------|
| **Canonical adjudication payload** | `reliability.evidence.contradiction_adjudication` | Yes, when present | Persisted shadow output only after a **non-empty** adjudication batch was sent to the model (`sent_count > 0`): run summary + per-fact items. |
| **Observability-only run** | `RetrievalReliability.contradiction_adjudication_observability` (in-memory on the reliability object) | **No** | Run-level status for **every** retrieval that evaluates the layer: `skipped_no_candidates`, `skipped_global_config`, `skipped_client_setting`, `skipped_missing_client_key`, `skipped_fact_limit`, `completed`, `completed_with_errors`, `failed_open`, etc. |

**Discipline for future work:**

- **Operational metrics** for the shadow layer (whether the layer ran, skipped, how many facts were candidates vs sent, error counts) must come from **observability** and from trace/debug **projection** fields derived from it — not by inferring from canonical `evidence` alone.
- **Canonical `evidence.contradiction_adjudication`** is absent on skip-only paths; do not treat “missing” as “disabled” without reading observability status.
- Conversely, **product decisions** (caps, signals) still come only from deterministic `evidence.contradiction` and policy; do not use adjudication verdicts for scoring until explicitly designed and gated.

Configuration (high level): global env (`CONTRADICTION_ADJUDICATION_*`), per-client `Client.settings.retrieval.contradiction_adjudication.enabled`, and the client's OpenAI key when the layer executes.

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
| Payment card numbers | `[CARD]` |
| Password-like secrets | `[PASSWORD]` |
| Identity documents | `[ID_DOC]` |
| IPv4 addresses | `[IP]` |
| URLs with token-like params | `[URL_TOKEN]` |

Only redacted text crosses the OpenAI boundary. Storage is split into:

- `messages.content_original_encrypted` — encrypted original wording
- `messages.content_redacted` — canonical safe text
- `messages.content` — legacy compatibility field, now kept redacted-safe

Tenant admins can configure optional regex entity types in `Settings → Privacy`. Chat logs and escalations are **safe-first**: redacted text is shown by default, while original text requires privileged access and every original view/delete action is written to `pii_events`.

### Answer validation (FI-034)

After generating an answer, a **second LLM call** (`temperature=0`) checks whether the answer is grounded in the retrieved chunks:

- Returns `is_valid` (bool) and `confidence` (0.0–1.0)
- If `is_valid = false` **and** `confidence < 0.4`, the answer is replaced with a safe fallback: *"I don't have enough information to answer this question."*
- Validation errors (e.g. OpenAI timeout) do not block the response — the original answer is returned with `validation_skipped: true`
- Full validation result is visible in `POST /chat/debug` → `debug.validation`

### Language behavior

The bot now follows one shared language policy for deterministic assistant text:

- before the first real user question, language-only turns use `user_context.locale -> user_context.browser_locale -> English`
- after the first real question, replies should follow the language of that question

This applies not only to generated RAG answers, but also to deterministic system text such as:

- soft rejection messages
- clarification prompts
- escalation fallback text
- the default greeting

Localization of deterministic text is handled by a shared helper in `backend/chat/language.py`.

### Default greeting

When a new conversation starts before the first real user question, Chat9 can return a default assistant greeting instead of `422`:

`I’m the <product_name> assistant and can help with documentation, product setup, integrations, and finding the right information. Ask your question.`

Behavior details:

- the canonical greeting is stored in English
- it is localized using the pre-question locale chain above
- `<product_name>` comes from `TenantProfile.product_name` when available, otherwise from the client name
- the stock widget shows this greeting automatically only for a truly new empty session; resumed sessions within 24 hours do not repeat it
- `Start new chat` creates a fresh session and allows the greeting to appear again
- empty follow-up turns after the conversation has already started still return `422 Question is required`

### Chat channels

| Channel | Auth | Endpoint |
|---------|------|----------|
| Dashboard / API | `X-Api-Key` | `POST /chat` |
| Widget (public) | `botId` in frontend/UI, legacy `clientId` in embed seam (same `public_id`) | `POST /widget/chat` |
| Debug tool | JWT + `bot_id` query param (`Client.public_id`) | `POST /chat/debug?bot_id=ch_...` |

The internal `/debug` and `/review` UI pages resolve the current bot automatically from the authenticated client's `public_id`; users are not expected to edit the URL manually. The explicit `bot_id` URL pattern remains part of the eval flow only (`/eval/chat?bot_id=...`).

### Sessions and history

Each conversation is a **session** (UUID). Messages within a session are stored and passed as history in subsequent turns (last N messages). Sessions are scoped to a client — no cross-client leakage.

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

1. An `EscalationTicket` record is created with a sequential number **ESC-####** (per client, e.g. ESC-0001)
2. The bot sends a GPT-generated handoff message to the user explaining the situation
3. The owner of the client receives an **email notification** (via Brevo) with ticket details
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

Clients can set a client-wide response detail level that controls how the bot phrases answers across all channels (widget + API).

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

By default the widget is **anonymous** — no information about the end user is passed to the bot. Optionally, clients can pass structured user context via a signed identity token.

### How it works

1. The client owner generates a signing secret in the dashboard (Settings → Widget API)
2. On their server, the client creates an `identity_token` — a short-lived HMAC-SHA256 signed JWT containing user metadata: `plan_tier`, `locale`, `audience_tag`
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

Users copy a snippet from the **Dashboard** (and optionally from docs). In product/UI language this is the bot ID. The legacy embed seam still uses `clientId` in the script URL, and that value is the client/bot **`public_id`** (`ch_…`). If the chat app (Next.js) is hosted on a **different origin** than the API that serves `embed.js`, set `window.Chat9Config.widgetUrl` to the app origin so the iframe loads the widget UI from the correct host.

Example (placeholders — the Dashboard fills in your real public bot ID and URLs; the script param remains `clientId` for compatibility):

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://<your-api-host>/embed.js?clientId=ch_xxxxxxxxxxxxxxxx"></script>
```

If the frontend and API share the same origin, you can omit `Chat9Config` and use a single script tag with `?clientId=…` only.

`embed.js` (vanilla JS, served from the API):
- Reads legacy `clientId` from the script URL query string (required compatibility seam)
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

## 10. Internal manual QA (Eval)

**Purpose:** Internal-only flow for human testers to chat with a client bot (same public widget path as production) and record **pass/fail** (and optional error category + comment) per assistant message. It does **not** change dashboard user auth or the public widget contract.

### Auth and isolation

- **Testers** are rows in `testers` (plain password for MVP, internal use). Created via CLI:  
  `PYTHONPATH=. python scripts/create_tester.py --username … --password …`
- **Eval JWT** is signed with **`EVAL_JWT_SECRET`** (required, distinct from `JWT_SECRET`). Claim `typ = eval_tester`. User JWTs use `typ = chat9_user`; tokens with `typ = eval_tester` are **rejected** by `decode_access_token` so eval tokens never authenticate dashboard routes.
- All eval HTTP routes live under **`/eval/*`** and use the eval dependency only.

### API (summary)

| Method | Path | Auth |
|--------|------|------|
| POST | `/eval/login` | Body: `username`, `password` → `access_token` |
| POST | `/eval/sessions` | Eval JWT; body: `bot_id` (public bot ID / client `public_id`) |
| POST | `/eval/sessions/{id}/results` | Eval JWT; snapshot `question`, `bot_answer`, `verdict`, optional `error_category`, `comment` |
| GET | `/eval/sessions/{id}/results` | Eval JWT; list results for **own** sessions only (404 if not owner) |

### `bot_id` = public bot ID (same value as widget `clientId`, not the API key)

The query/body field **`bot_id`** is exactly the public bot ID from the dashboard embed snippet. For compatibility, it is the same value currently carried as **`clientId`** in the script URL, e.g. `embed.js?clientId=ch_xxxxxxxxxxxxxxxx`.

```html
<script src="https://<api-host>/embed.js?clientId=ch_bwf5xpwxgaok3bzqjg"></script>
```

→ use **`/eval/chat?bot_id=ch_bwf5xpwxgaok3bzqjg`** (same string).

Do **not** use the secret **32-character `api_key`** (used for `X-API-Key` / server chat) as `bot_id`.

### Bot eligibility (aligned with widget)

Creating an eval session uses **`backend/clients/widget_chat_gate.py`** — the same rules as **`POST /widget/chat`** and **`POST /widget/escalate`**: client must exist, be **active**, and have a **non-empty** OpenAI API key configured. Otherwise the API returns the same class of errors as the widget (404 / 403 / 400), so testers do not get a “session created” state when the chat cannot run.

### Misconfiguration

If `EVAL_JWT_SECRET` is missing or blank, eval login and protected eval routes return **503**; the server logs **`eval_jwt_misconfigured`** at error level.

### Frontend

- **`/eval/login`** — stores token in `localStorage` (`chat9_eval_access_token`); supports `?next=` to return to `/eval/chat?bot_id=…`
- **`/eval/chat?bot_id=ch_…`** — bootstraps eval session once (deduped in dev under React Strict Mode), reuses **`ChatWidget`** + rating panel under each assistant bubble
- **Escalation handoff** messages are still assistant bubbles and remain rateable in MVP; if you aggregate scores as “model quality”, filter by turn type later to reduce noise.

### Tests

Regression coverage: `tests/test_eval.py`.

---

## 11. Dashboard

The web dashboard at `getchat9.live` is a Next.js 14 app. Authenticated pages use a **left sidebar** for navigation (main items, **SETTINGS**, and **Admin** for `is_admin` users); the top bar shows brand, email, and logout.

| Page / route | What it shows |
|--------------|---------------|
| **Dashboard** (`/dashboard`) | **API key** (server-to-server `X-Api-Key`), **embed code** snippet (`public_id` / `ch_…`); banner linking to Agents if OpenAI key is missing |
| **Knowledge** (`/knowledge`) | Upload files, add URL sources, trigger embeddings/crawls, health indicators, delete; unified indexed sources table (replaces legacy `/documents`) |
| **Agents** (`/settings`) | Per-client **OpenAI API key** (encrypted), save/update/remove |
| **Logs** (`/logs`) | Full chat history across sessions; thumbs up/down feedback |
| **Review** (`/review`) | Bad answers (thumbs down) with ideal answer input |
| **Escalations** (`/escalations`) | L2 ticket inbox; resolve tickets |
| **Debug** (`/debug`) | Run RAG debug; answer + retrieval table with chunk previews and scores (code blocks use inline copy) |
| **Response controls** (`/settings/disclosure`) | Disclosure level (Detailed / Standard / Corporate) |
| **Widget API** (`/settings/widget`) | Generate / rotate KYC signing secret; Node.js token example |
| **Admin** (`/admin/metrics`, admins only) | Platform-wide metrics |

---

## 12. Security

| Area | Implementation |
|------|---------------|
| Authentication | JWT (HS256), bcrypt passwords; user access tokens include `typ=chat9_user` |
| Internal eval | Separate `EVAL_JWT_SECRET`, `typ=eval_tester`, `/eval/*` only |
| Data isolation | All queries scoped by `client_id`; no cross-client access possible |
| API key storage | AES-GCM encrypted at rest |
| KYC secret storage | AES-GCM encrypted at rest |
| PII protection | Regex redaction before all OpenAI calls |
| Rate limiting | slowapi (per-IP): chat 30/min, search 30/min, validate 20/min, widget 20/min |
| CORS | Production allowlist; widget served via iframe (same-origin for widget API calls) |

---

## 13. Infrastructure

```
getchat9.live (Vercel, Next.js 14)
  ↕  HTTPS
api.getchat9.live (Railway, FastAPI + Uvicorn)
  ↕  SQLAlchemy
PostgreSQL 15 + pgvector extension (Railway managed DB)
  ↕
OpenAI API  (client's own key)
Brevo       (transactional email: verification, password reset, escalation notifications)
```

**Git branching:**
- `main` — development; no auto-deploy
- `deploy` — production; Vercel and Railway listen to this branch

---

*For the chronological development history, see [`PROGRESS.md`](./PROGRESS.md).*  
*For the feature registry with code pointers, see [`IMPLEMENTED_FEATURES.md`](./IMPLEMENTED_FEATURES.md).*  
*For the tech stack details, see [`03-tech-stack.md`](./03-tech-stack.md).*
