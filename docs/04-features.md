# Chat9 — Product Features

A complete description of every implemented capability. Written for a technical reader who has no prior context on the codebase.

**Last updated:** 2026-04-08 (Gap Analyzer hardening + knowledge topics terminology)  
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
- public widget flows are not part of this rule unless they explicitly use the dashboard JWT stack

### Forgot password

Full reset flow:
1. User enters their email on `/forgot-password`
2. Backend sends a reset link via Brevo (rate-limited: 3 requests/hour per email)
3. Link contains a one-time token (1-hour TTL)
4. User sets a new password at `/reset-password`

### Roles and team members

A workspace has **exactly one owner: the person who created it.** Everybody
else is an operator. The role is stored as a plain string in `users.role`,
written once when the account is created — by `create_tenant` for a founder,
as `operator` by `invite_member` for everybody else — and never changed after
that. There is no promotion, no demotion, no route that edits a role, and no
role field on an invitation: an invite that could name a role would be an
invite that could mint a second owner.

So this is not a permission model with transitions to reason about. It is one
fact about how an account came into being, and two sets of privileges that
follow from it:

| | `owner` | `operator` |
|---|---|---|
| Inbox: read, reply, close | yes | yes |
| Logs and transcripts | yes | yes |
| Knowledge base | read + edit | read only |
| Gap Analyzer: view, prepare a draft | yes | yes |
| Publishing an FAQ | yes | no |
| Support contacts (the ones the bot hands to visitors) | read + edit | read only |
| Settings, API keys, privacy config, member management, tenant deletion | yes | no |

Publishing is owner-only because an FAQ publish changes the bot's answers for every visitor — a content decision, not an operational one.

Routes enforce this with `require_owner` / `require_member` from `backend/auth/middleware.py`, built by `require_role(*roles)`. A member holding the wrong role gets **403**; a principal belonging to no workspace at all (`users.tenant_id` is nullable) gets **404**, the same answer every tenant-scoped route already gives them. Cross-tenant resources still answer **404**, because the role check only inspects the caller — the tenant-scoped lookup after it is what refuses the row.

Five owner-only routes manage the team: `POST /tenants/members/invite`, `GET /tenants/members`, `DELETE /tenants/members/{id}`, and `PUT` / `DELETE /tenants/members/me/seat` for the owner's own seat.

**Nobody can lock a workspace out of itself.** Nobody may remove themselves — an owner who did would have no way back in — and the owner cannot be removed by anyone. With one owner per workspace those two rules are the same rule seen twice: only an owner may remove, so a target who is an owner is always the caller, and the self-removal check refuses first. The owner check is kept anyway, as one comparison on the row already in hand: it states the invariant where a reader will look for it, and it is what would hold if a second owner ever appeared through a data repair. It replaced a count of verified owners across rows, and the `FOR UPDATE` lock on the workspace that such a count needed went with it — the predicate is now about the single row being deleted, so two concurrent removals cannot conspire.

**The owner's only exit is deleting the workspace.** There is nobody to hand it to and no way to remove yourself, so `DELETE /tenants/{id}` (owner-only, and it deletes the members too) is the whole of leaving. Nothing in the dashboard offers a departure that cannot happen.

**Removing a member hands back the chats they were holding.** A `live` chat with no operator is a visitor typing into nothing: `OperatorHandler` swallows every turn while the chat is live, so without this nobody answers until the sweeper's idle release fires up to an hour later. Removal runs the same `release_to_bot` the release button does, which also closes the dangling `operator_sessions` stretch.

**Deleting a workspace deletes its members**, for the same reason removal deletes the account: a surviving `tenant_id = NULL` row belongs to nothing and permanently burns the address, since both invites and `/auth/register` refuse an e-mail that already exists. No attribution stamping there — everything a label would preserve is tenant-scoped and cascades away with the workspace.

**Removal deletes the account.** Membership and account have the same lifetime — there is no verified user without a workspace — which is what keeps the invite path honest: every invitee is a new account setting a first password from the link, so nobody joins a workspace without an act of their own. Being invited again later means a new account with a new id, and a live JWT stops working the moment the row is gone (`get_current_user` answers 401).

Deleting would erase attribution, because five of the six FKs into `users` are `ON DELETE SET NULL` — every operator message and every `operator_sessions` row would lose its author silently. So the account goes and the signature stays: `messages.operator_label`, `operator_sessions.operator_label`, `gap_dismissals.dismissed_by_label` and `tenant_api_keys.created_by_label` are written with the departing member's e-mail in the same transaction as the delete. They are NULL while the account exists — read the author through the FK, fall back to the label once it is gone. `chats.assigned_operator_id` is live state rather than history and is not stamped; `pii_events.actor_user_id` is never written by anything, so it has nothing to preserve.

**An unaccepted invitation expires and the row goes with it.** An invite creates a real (unverified, unusable) `users` row, so an expired token alone would leave the address occupied forever: the invited person cannot register on their own, and only the inviting owner could free it. `backend/jobs/expired_invitations_purge.py` deletes member rows that are unverified with an expired invite token, hourly. It can never strand a workspace, and now by construction rather than by a filter: every invitee is an operator, so the set it sweeps contains no owners at all. It also has no seat to release — a pending invitee holds none.

**The invite token is the password-reset token** (`reset_password_token` + its expiry, redeemed by `POST /auth/reset-password`) with a 7-day TTL instead of the reset's hour. An invite and a reset are the same act — prove the address, set a password — and nothing needs to distinguish them at rest: the two links differ by path (`/accept-invite` vs `/reset-password`), and "invited but not accepted" is derivable as a member who is not yet verified.

Because they share a column, `POST /auth/forgot-password` re-sends the **invitation** for a pending invitee rather than issuing a reset. A pending invitee cannot log in, so "Forgot password" is precisely what they press — and a reset there would overwrite the invite token and cut its life to an hour, after which the invite link reports "invalid or expired" and the owner starts re-inviting in a loop. The other direction needs no handling: `invite_member` answers 409 for a verified member, so a re-invite can never void a live reset.

**No request body carries a role, and responses report one tolerantly.** Nothing a client can send names a role, so the API cannot be talked into storing one at all. Responses report `users.role` as a plain string, because a closed response type turns the first row holding an unknown role into a 500 on `GET /tenants/me` — which the app shell and the sidebar call on mount — taking out the whole dashboard for that user. That is reachable with no bug at all: deploy a build that adds a third role, let it write one row, roll back. Every consumer tests for `owner` explicitly, so an unrecognised role loses privilege rather than gaining it.

### Operator seats

A seat is the per-person entitlement to **operate**, where the role governs what a person may **administer**. The two are different questions and neither implies the other. Stored as `users.seat_granted_at` (NULL means no seat, a timestamp means when one was granted), on the person because that is what is sold — and because removal deletes the row, so a departing member can never leave a seat behind.

**Seats follow joining.** A colleague is seated when they *accept* their invitation (in `auth.service.reset_password`, which is also the accept-invite endpoint) and unseated when they are removed. Nothing sits in between, so somebody who has joined always holds a seat. A *pending* invitee holds none and costs nothing: they are a placeholder for a person who may never turn up, and an invitation to a typo'd address should not cost a week of a seat. The one account that can be verified, in a workspace, and hold no seat is the owner — who administers without one and takes a seat from the seats screen only if they also mean to answer from the console. Since roles never move, nobody can be put into that state by anybody else.

**What a seat gates today:** `require_seated_member` on the three `/operator/*` routes — take a chat, answer in it, release it. A seatless caller gets **403** whatever their role. `backend/seats/` also carries two read helpers cut as seams for the inbound e-mail lane, `tenant_has_any_seat(tenant_id=…)` and `user_holds_seat(user_id=…)`; **neither has a caller yet**, and every escalation notification still passes `reply_to=ticket.user_email` unconditionally.

**Nothing is charged.** The seats screen prices the workspace at $10 per seat per month and takes no payment: there is no billing system, no card on file, and no invoice. It replaced a `tenants.plan` tier field that modelled the same entitlement per workspace; `operator_seats_v1` dropped that column, seated every member who had already joined, and reduced any workspace holding more than one owner to its founder.

### Admin flag

Users can have `is_admin = true`. Admins see an **Admin** section in the **sidebar** (app shell) and can access platform-wide metrics (`GET /admin/metrics/*`) — total users, sessions, tokens used across all clients.

---

## 2. Tenant (Client) Management

Each registered user has exactly one **Client** record. The client is the unit of isolation — all documents, chats, API keys, and settings belong to a client. This one-to-one rule is enforced in both application logic and the database schema.

### API key

Every client gets a random 32-character `api_key` when the workspace client is provisioned during successful email verification. This key is used for **server-to-server** chat calls (`X-Api-Key` header) and widget authentication. It can be rotated if compromised (delete + recreate client).

### Public ID

Every bot has its own `public_id` which is what customers paste into the embeddable widget snippet as `data-bot-id`. A tenant also has a `public_id`, but **they are not interchangeable** — `/widget/chat` resolves `Bot.public_id`, not `Tenant.public_id`. The bot public ID is safe to expose in public HTML — it only identifies the bot, grants no write access.

### OpenAI API key (per client)

Each client provides their own OpenAI key. It is **encrypted at rest** (AES-GCM via `backend/core/crypto.py`). The platform never uses a shared OpenAI key — no markup, no shared quota. The key is decrypted in memory only when making an OpenAI API call.

### Deleting a workspace

A workspace has one owner, fixed at creation, and ownership cannot be handed over. Deleting the workspace is therefore the owner's only way out, and `DELETE /tenants/{tenant_id}` is a documented route rather than a hidden one — hiding it made the exit harder to find without making it any harder to perform. The dashboard surfaces it at `/settings`, behind a confirmation that requires typing the workspace's name.

**No grace period.** Confirmation, then immediate deletion. No soft delete, no restore window, no support-side recovery. This was decided deliberately rather than inherited: a soft delete is much cheaper to add now than to retrofit onto a schema full of cascades, and the decision to skip it should be revisited if it ever stops being true that an accidental deletion costs a customer nothing they can't rebuild.

**What it destroys.** Every account belonging to the workspace, the owner's included — nobody survives as an account belonging to nothing. Cascades take conversations, messages, documents, embeddings, escalation tickets and API keys. The widget stops answering on the customer's site immediately.

**Data outside Postgres**, decided per system rather than as one policy:

| System | On deletion | Why |
|---|---|---|
| PostHog | **kept** | Behavioural metadata only — identifiers, durations, outcomes. No conversation text, no personal data. Product metrics stay comparable across time rather than being rewritten whenever a workspace leaves. |
| Langfuse | **deleted** | Traces hold question previews and answers — the conversations themselves. Self-hosting makes it our infrastructure, not a third party; it does not make the data ours. No retention window is configured (`07-observability-rollout.md` still lists it under Remaining Gaps), so nothing expires on its own. |
| Brevo | **deleted** | Addresses: members', and the support inbox escalations are routed to. Visitor addresses are in scope too, though today they only ride out as `replyTo`, which Brevo does not turn into a contact. |

Both external purges run in one durable ARQ job (`backend/jobs/workspace_purge.py`) **enqueued before the local delete**, carrying everything it needs in `background_jobs.payload` so it depends on no row the delete is about to destroy. If the job cannot be scheduled, the deletion is refused with `503` and nothing is deleted. Read that module's docstring before changing any of this — the ordering, the `tenant_id=None` on the status row, and the use of `arq.Retry` each exist for a reason that is not obvious from the code alone.

**What it cannot reach.** Copies already taken out of those systems — an exported eval dataset, a downloaded CSV, a screenshot in a ticket. Customer-facing copy therefore says "everything we hold for this workspace" and never "nowhere does it remain", which is a promise nobody can keep.

---

## 3. Document Upload & Processing

### Supported formats

| Format | Extensions | Parser | Notes |
|--------|------------|--------|-------|
| PDF | `.pdf` | pdfplumber (fallback: pypdf) | Layout-aware: two-column detection, tables rendered as markdown tables |
| Markdown | `.md`, `.mdx` | — | CommonMark |
| HTML | `.html`, `.htm` | beautifulsoup4 | Readability-style main-content extraction; nav/footer/aside/script stripped; headings preserved |
| Swagger / OpenAPI | `.json`, `.yaml`, `.yml` | PyYAML / json | JSON/YAML parse, OpenAPI validation, endpoint-aware rendering |
| Word (DOCX) | `.docx` | python-docx | Paragraph-level text extraction |
| Word (legacy DOC) | `.doc` | antiword | Plain-text extraction via system tool |
| Plain text | `.txt` | — | UTF-8; falls back to latin-1 |

Upload endpoint: `POST /documents` (multipart/form-data, max 50 MB).
Supported OpenAPI extensions: `.json`, `.yaml`, `.yml`.

**Duplicate detection:** before saving, the backend computes a SHA-256 hash of the raw file bytes and checks whether the tenant already has a file-upload document with the same hash. If a duplicate is found, the API returns `409 Conflict` with a message identifying the existing document by name. URL-crawled pages are excluded from this check.

### Processing pipeline

1. File is saved and parsed to `parsed_text`
   - PDF / Markdown / DOCX / DOC / TXT become plain text
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

Documents are split into chunks before embedding by a **per-content-type chunker** selected from the registry in `backend/chunkers/registry.py` (keyed by `Document.file_type`). Adding a new format means registering a new chunker — the embedding pipeline core stays untouched. See `backend/chunkers/README.md` for the extension guide.

| Content type | Chunker | Strategy |
|--------------|---------|----------|
| Markdown | `chunk_markdown` | Split on ATX headings; every chunk starts with its heading path (`H1 > H2 > H3`); oversized sections recursively sentence-split; pipe tables become standalone chunks |
| HTML | `chunk_markdown` | HTML is rendered to markdown-ish text at parse time (boilerplate stripped, headings preserved), then chunked like markdown |
| PDF | `chunk_pdf` | Table-aware: tables detected at parse time become standalone chunks (oversized tables split by rows with the header repeated); prose is sentence-split |
| Plain text / DOCX | `chunk_plaintext` | Sentence-boundary chunking (the universal fallback for unknown types) |
| Swagger / OpenAPI | `_build_swagger_chunks` | Operation-aware (special-cased outside the registry — needs per-operation metadata) |

Chunk-size budgets per type (soft limits, sentence boundaries preserved):

| Document type | Chunk size | Overlap |
|---------------|-----------|---------|
| PDF | 1000 chars | 1 sentence |
| Markdown / HTML | 700 chars | 1 sentence |
| Plain text / DOCX / unknown | 700 chars | 1 sentence |
| Swagger / OpenAPI | Operation-aware chunks | No sentence overlap between operations |
| Logs *(planned)* | 300 chars | 0 sentences |
| Code *(planned)* | 600 chars | 1 sentence |

Swagger/OpenAPI chunking rules:

- Primary unit: one API operation (`method + path`)
- No sliding-window overlap between operations
- Rich operations may emit secondary chunks for request schema and response schema detail
- Secondary chunks repeat the endpoint header for retrieval context, but do not duplicate neighbouring operations

Each chunk stores: `chunk_text`, `chunk_index`, `char_offset`, `char_end`, `filename`, `file_type`; markdown/HTML chunks additionally store `heading_path`, and table chunks store `subtype: "table"`.

**Note on re-indexing:** changing a chunking strategy only affects newly embedded documents. Existing tenants keep their old chunks until re-embedded (delete-and-recreate via `POST /embeddings/documents/{id}` or re-crawl). Retrieval-quality evals comparing before/after must bracket the re-index, not the deploy.

### Document health check (FI-032)

After embedding, the system runs a deterministic health lint pass on the document:

- Checks for: missing or very short content, extraction issues, weak structure, incomplete sections, and low information density
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

Quick answers hardening:

- The crawler extracts a small structured quick-answer set from HTML pages, including support email, documentation URL, pricing URL, trial info, status page URL, and support chat provider.
- Support emails are rejected when the local part is clearly non-support (`noreply`, `no-reply`, `donotreply`, `do-not-reply`, `notifications`, `notification`, `mailer-daemon`, `postmaster`, `bounce`, `bounces`, `privacy`, `legal`, `compliance`, `gdpr`, `dpo`, `dmca`, `abuse`, `security`, `press`, `pr`, `media`, `investors`, `ir`, `jobs`, `careers`, `recruiting`, `hr`, `talent`, `marketing`, `newsletter`, `subscribe`, `unsubscribe`, `webmaster`, `admin`, `root`).
- Support emails are also length-gated to practical limits: local part `<= 40`, total address `<= 120`, with malformed dotted forms rejected.
- Trial info is stored only as one user-readable sentence, capped at `240` characters and trimmed at a word boundary with an ellipsis when needed.

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

### Controlled clarification layer

The chat pipeline routes every turn through a single decision engine
(`backend/chat/decision.py::decide`) which returns one of nine
`DecisionKind` outcomes (e.g. `answer_from_faq`, `answer_with_citations`,
`answer_with_caveat`, `answer_with_caveat_and_inline_clarify`, `clarify`,
`escalate`, `reject`). The full block-rules contract is documented in the
**Clarification** subsection below. The chat reply is a JSON object
whose message content lives in a single `text` field (alongside
`session_id`, `chat_ended` and an optional `ticket_number`); there is no
structured `message_type` discriminator and no quick-reply payload in v1.

---

## 5. Gap Analyzer

Gap Analyzer is the dashboard feature that turns both documentation coverage gaps and real user-question pain into a reviewable backlog for the tenant.

It has two coordinated pipelines:

- `Mode A` analyzes the indexed documentation corpus itself to suggest topics that appear missing or under-covered in docs
- `Mode B` clusters real user questions that produced low confidence, fallback, rejection, escalation, or explicit negative feedback signals

### Mode A — docs-side gap discovery

Mode A runs only when the retrieval corpus materially changes. It uses deterministic corpus sampling plus an extraction hash so repeated runs can skip both the LLM call and DB churn when the sampled corpus has not changed.

Important constraints:

- candidates are cross-validated with Gap Analyzer's own coverage score before they are persisted
- dismissals survive re-indexing through a separate dismissal store
- Swagger / OpenAPI sources are intentionally excluded from Mode A and are reserved for a future dedicated analyzer

### Mode B — user-question clustering

Mode B ingests signals from the chat pipeline after the final turn outcome is known. Signals are stored with explicit message correlation, so thumbs-down feedback can reweight the exact underlying question signal rather than guessing from chat history.

Current behavior:

- new unclustered questions are matched into an existing cluster or create a new one
- cluster coverage is re-evaluated against the current corpus through Gap Analyzer's own narrow retrieval seam:
  - semantic vector top-k over the tenant corpus
  - bounded BM25 lexical confirmation over the same tenant corpus
- linked `Mode A` and `Mode B` items dedupe on the active list, with `Mode B` as the primary visible card
- a periodic full reclustering pass now rebuilds recent active/closed cluster history to reduce duplicate or drifted clusters over time
- chat-signal follow-up work and manual recalculation are persisted as durable Gap Analyzer jobs with retryable orchestration state

### Dashboard and API surface

The app includes a dedicated `/gap-analyzer` dashboard page for verified users. It exposes:

- summary stats and sidebar badge
- a lightweight `GET /gap-analyzer/summary` badge contract for navigation reads
- separate Mode A and Mode B sections
- active vs archive views
- linked Mode B cards that can show the related docs-gap context inline
- dismiss / reactivate actions
- draft generation for follow-up documentation work
- orchestration-style recalculation requests via `POST /gap-analyzer/recalculate`

Archive semantics are intentionally source-specific:

- archived Mode A means dismissed Mode A topics
- archived Mode B means closed, dismissed, or older inactive Mode B clusters
- an archived linked Mode B item does not hide an active Mode A item; suppression only happens when the linked Mode B item is active

Clarification is intentionally narrow. The bot does not ask follow-up questions by default; it does so only when the current request is not sufficiently answerable under the existing pipeline signals and one of these deterministic trigger families is matched:

- low retrieval confidence (retrieved chunks exist but score is below the high-confidence threshold)
- multiple conflicting matches (contradiction detected between retrieved sources)

v1 limitation: ambiguous intent and missing critical slot are not yet emitted (no intent classifier). These paths fall back to the best-effort answer.

**Decision engine (`backend/chat/decision.py`)**

Every chat turn is routed by a single authoritative `decide(turn: TurnContext) -> Decision` function. Block rules are evaluated in order; the first match wins:

1. Guard failure → `reject`
2. Explicit human-agent request → `escalate(explicit_human_request)`
3. Session closed → `acknowledge_closed_or_start_new`
4. Active escalation ticket → `forward_to_active_ticket`
5. Clarification budget exhausted (see below) → `answer_with_caveat` or `escalate(clarify_loop_limit)`
6. FAQ direct hit → `answer_from_faq`
7. Medium-confidence KB + partial answer → `answer_with_caveat_and_inline_clarify` (budget-free)

After these: high-confidence KB → `answer_with_citations`; remaining low-confidence → `clarify(blocking)` or `escalate(low_confidence_no_path)`.

**Clarification budget**

- Maximum **1 blocking clarifying question per conversation** (`CLARIFICATION_TURN_LIMIT`, default 1, configurable via env var). The budget resets when conversation rotation opens a new Chat row (see "Sessions, conversations, and history") — i.e. only after a long idle gap (`CONVERSATION_IDLE_TIMEOUT_SECONDS`, 7 days) or `Start new chat`, not on a return within the widget session.
- `chats.clarification_count` tracks how many blocking clarifications have been issued in the conversation.
- Counter increments only on `Decision.clarify(type=blocking)` whose reply actually ends in a question, atomically in the same DB transaction as the assistant message. The decision is made after generation, so a clarify turn the model answered instead of asking costs nothing — otherwise the budget ran out on questions the user was never asked.
- Inline clarifications (`type=inline`, appended after a partial answer) are budget-free and never increment the counter.
- When the budget is exhausted and the turn would otherwise produce a blocking clarify:
  - if medium-confidence chunks are available → `answer_with_caveat` (caveated answer, no follow-up question)
  - otherwise → `escalate(clarify_loop_limit)` (real support ticket created)

**Clarify types**

| type | counted toward budget | description |
|------|----------------------|-------------|
| `blocking` | yes | bot stops and asks one question before answering |
| `inline` | no | bot gives a partial answer and appends a soft follow-up question |
| `safety_confirm` | no | reserved for future safety-sensitive confirmations |

Public response contracts:

- `POST /chat` returns a JSON body with a canonical `text` field (plus `session_id`, `chat_ended`, optional `ticket_number`, and trace fields)
- `POST /widget/chat` streams Server-Sent Events: `status` → `chunk`* → exactly one terminal `done` frame whose payload carries the same `text` / `session_id` / `chat_ended` / optional `ticket_number` / optional `sources`
- both channels may return the localized default greeting as a normal `text` reply when a brand-new empty conversation starts

v1 note: structured `clarification` payload (`message_type`, `options`, `option_id`, quick-reply buttons) is **not implemented**. The bot may embed a clarifying question in plain text as part of the normal answer, but no structured clarification object is returned and the widget does not render quick-reply buttons.

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
  - `Profile` (`?tab=profile`) for extracted profile review/edit, extracted topics review/edit, and glossary read-only inspection
  - `FAQ` (`?tab=faq`) for FAQ moderation (accept/reject/edit/approve-all, pending counter, filters, optimistic reject UX)

Knowledge profile terminology:

- the public/dashboard term is **`topics`**
- `topics` are extracted documentation themes, not guaranteed canonical product module names
- the storage layer and some internal code still use the `modules` field name
- `GET/PATCH /knowledge/profile` exposes `topics` in the public contract

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

#### Adjudication-driven cap suppression

The contradiction cap can be suppressed by the LLM contradiction adjudicator when the `CONTRADICTION_ADJUDICATION_FILTER_CAP_ENABLED` global flag is enabled (default `false`). The flag is intentionally asymmetric: adjudication can only **drop** a deterministic cap, never add a new one. Suppression applies only when **all** of the following hold:

- the global flag is on,
- adjudication ran and finished with status `completed` or `completed_with_errors`,
- `sent_count > 0` (at least one fact was actually adjudicated),
- every adjudicated item carries `verdict == "rejected"`.

Any other state — `confirmed`, `inconclusive`, mixed verdicts, partial coverage with skipped facts, `failed_open`/non-completed runs, missing items — leaves the deterministic cap untouched (fail-open). When suppression applies, `cap` and `cap_reason` reset to `None`; the contradiction signal and evidence remain in the payload for traces.

#### Cap propagation to the decision engine

The contradiction cap (and the existing `source_overlap` cap) reach the clarification decision engine via `_classify_kb_confidence` in `backend/chat/handlers/rag.py`, which floors the raw similarity-based tier by `retrieval.reliability.cap` when a cap is set. A high raw similarity score with a contradiction cap therefore reports `kb_confidence="low"` to `decide()`, and the existing `multiple_conflicting_matches` clarify branch fires when the contradiction cap is the active reason. Flooring is intentionally driven by `cap`, not `reliability.score`: the reliability score uses stricter base thresholds (`high` only at top_score ≥ 0.8) than the classifier (`high` at ≥ 0.45), so flooring by score would silently downgrade uncapped high-confidence queries. Without this floor (the prior behavior), caps lived only in observability and had no user-visible effect.

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

## 6. RAG Chat Pipeline

### How a chat turn works

```
User message
  ↓
PII redaction (regex)
  ↓
Bootstrap greeting early exit  (empty turn on a brand-new session → default greeting, return)
  (No small-talk shortcut: every non-empty turn — including one-word greetings — continues
   below. Short/unclear inputs are answered or get the zero-hits "please rephrase" soft reply.)
  ↓
Parallel guard pool (3 threads, ~2 s budget):
  ├─ injection_check   — semantic + pattern detector
  ├─ relevance_check   — vector similarity pre-filter (threshold 0.22)
  └─ capability_check  — LLM classifier: is user asking what the bot can do?
Lightweight LLM classifiers default to `gpt-4o-mini`: explicit human-request detection uses
`HUMAN_REQUEST_MODEL`, relevance classification uses `RELEVANCE_GUARD_MODEL`, and answer
validation uses `VALIDATION_MODEL`.
  ↓
Guard decisions (in priority order):
  1. injection detected          → guard_reject / injection
  2. capability question         → capability_response  (positive "I can help with…")
  3. FAQ direct match            → faq_direct  (skip retrieval)
  4. relevance score < threshold → guard_reject / low_retrieval
  5. relevance LLM category:
       offtopic                  → guard_reject / not_relevant  (refusal ends with a
                                    support-handoff offer, never a dead end)
       support_complaint         → escalation offer  (pre-confirm, trigger user_complaint)
       social                    → guard_social_reply  (polite localized acknowledgement)
  ↓
Hybrid search → top-k chunks  (vector + BM25 + RRF)
  ↓
Build RAG prompt (system + context + history + question)
  ↓
gpt-5-mini → answer
  ↓
Answer validation (second gpt-4o-mini call by default; rollback via `VALIDATION_MODEL=gpt-4.1-mini`)
  ↓
Store message → return response
```

**Pipeline strategies** — the `strategy` field on every Langfuse trace:

| Strategy | Meaning |
|---|---|
| `greeting` | Empty/greeting turn before first real question |
| `capability_response` | User asked what the bot can do — returns positive description |
| `faq_direct` | High-confidence FAQ match; retrieval skipped |
| `faq_context` | FAQ match used as extra context alongside retrieved chunks |
| `rag_only` | Standard full RAG path |
| `guard_reject` | Blocked by injection / relevance / retrieval guard |

**Guard reject reasons** (`reject_reason` on trace metadata):

| Reason | Trigger |
|---|---|
| `injection` | Prompt injection pattern detected |
| `not_relevant` | Relevance LLM classified the message as `offtopic` |
| `low_retrieval` | Best vector similarity below `RELEVANCE_RETRIEVAL_THRESHOLD` (default 0.22) |
| `insufficient_confidence` | Answer validation confidence below threshold |
| `rephrase` | First strict zero-RAG-hits turn — soft "couldn't find an answer, please rephrase" prompt |
| `social` | Relevance LLM classified the message as a pure social turn (thanks / farewell) — polite acknowledgement, not a refusal |

**Relevance guard: dialog context & message categories.** The relevance guard
(`backend/guards/relevance_checker.py`) does not judge messages in isolation: both call
sites (the pre-retrieval check and the consecutive zero-hits `force_llm_check` pass) hand
it the last 1–2 dialog turns (`build_dialog_context` in `backend/chat/followup.py`), so
anaphoric follow-ups ("what about a cloudflare certificate?", "and for legal entities?")
in an on-topic conversation resolve against context instead of being rejected. The guard's
5-minute verdict cache keys on the dialog tail as well, so a context-dependent verdict is
never replayed for the same text in a different conversation state.

The guard LLM classifies each message into one of four categories in a single call:

| Category | Routing |
|---|---|
| `relevant` | Continue the RAG pipeline |
| `offtopic` | `guard_reject / not_relevant`; the refusal text ends with an offer to forward the request to support |
| `support_complaint` | User complains support hasn't replied / they've been waiting — pre-confirm escalation offer (`user_complaint` trigger), never a refusal |
| `social` | Pure thanks / farewell that slipped past the greeting handler — polite localized acknowledgement |

Language-agnostic by design: classification is a pure LLM verdict (no per-language keyword
lists), and every user-facing reply is a canonical English template routed through the
localization layer. Known trade-off: messages of ≤4 words bypass the guard LLM entirely
(`short_query_bypass`), so a very short complaint ("no one replied") rides the zero-hits
net instead — rephrase prompt first, then the next turn's forced check (which sees the
dialog context) classifies the repeat as `support_complaint` and offers the handoff.

**Cross-lingual note:** Russian / other non-English queries against English docs produce cosine similarities of 0.22–0.27 even when content is perfectly relevant. The default threshold of 0.22 is calibrated for this. Set `RELEVANCE_RETRIEVAL_THRESHOLD` env var to override.

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

Redaction is an **egress** concern, not a storage one. Storage keeps a single column with the original wording — `messages.content` and `escalation_tickets.primary_question` — and every boundary that sends text out of the platform masks it on the way:

- the user question, chat history, and background-job text handed to OpenAI (`redact_for_egress` in `backend/chat/pii.py`);
- the escalation notification email, which additionally keeps `EMAIL` and `IP` visible in user-authored turns so support can actually reply (`_support_email_text`).

Only redacted text crosses the OpenAI boundary. Tenant admins configure optional regex entity types in `Settings → Privacy`; the setting governs what is masked at those egress boundaries. `pii_events` records what was masked on the way out — there is no "view / delete originals" flow any more, because the tenant's own dashboard already shows their conversations as written.

### Answer validation (FI-034)

After generating an answer, a **second LLM call** (`temperature=0`) checks whether the answer is grounded in the retrieved chunks:

- Returns `is_valid` (bool) and `confidence` (0.0–1.0)
- If `is_valid = false`, the answer is replaced with a safe fallback: *"I don't have enough information to answer this question."*
- Validation errors (e.g. OpenAI timeout) are logged and treated as `validation_error`, which triggers the safe fallback instead of returning an unverified answer
- Full validation result is visible in `POST /chat/debug` → `debug.validation`

### Language behavior

The bot now follows one shared language policy for deterministic assistant text:

- before the first real user question, language-only turns use the bootstrap locale chain (see fixed contract below)
- after the first real question, replies should follow the language of that question

This applies not only to generated RAG answers, but also to deterministic system text such as:

- soft rejection messages
- clarification prompts
- escalation fallback text
- the default greeting

Localization of deterministic text is handled by a shared helper in `backend/chat/language.py`.

#### Bootstrap locale chain — fixed contract

For any turn that has no user message to detect language from (the bootstrap greeting and any other pre-question deterministic text), the response language is resolved in this exact order. **This order must not be changed without an explicit product decision** — call sites and tests rely on it.

1. **Prior session language** — the language the visitor actually spoke in an earlier conversation of this same session, carried across a rotation boundary (`rotated_from.last_response_language`). This is the strongest signal because it is an *observed* choice the visitor made, not a passive hint. It is only present when the session was rotated (an idle re-open); a genuinely first bootstrap has no prior language and falls through. Without it, a returning Russian-speaking visitor re-greeted after an idle gap would be greeted in English.
2. **`user_context.locale`** — the locale the tenant explicitly passed for this user (typically via `userHints.locale` in `Chat9Widget.start({...})` or `Chat9Widget.setHints({...})`). The tenant's own system says "this specific user prefers locale X" — usually backed by the user's own account preferences in the tenant's product.
3. **`user_context.browser_locale` / request `Accept-Language`** — the browser-reported locale. Weaker than the explicit hint because it reflects the device's OS/browser default, which can be wrong (e.g. an English-OS laptop used by a Spanish-speaking employee).
4. **English** — final fallback when no signal is available.

Prior session language outranks KYC/browser deliberately: an observed language the visitor typed beats any hint. Below it, the explicit KYC hint outranks browser — reversing *that* would silently override tenants who paid the integration cost of passing `locale` through `userHints`. Implementation: [`_resolve_language_context_inner`](../backend/chat/language.py) in `backend/chat/language.py` — bootstrap branch; the carry across rotation lives in `_ensure_chat_async` in `backend/chat/service.py`.

#### Language locking — fixed contract

Once a chat has settled on a language, it stops re-detecting and stays in that language for the rest of the conversation (the lock resets when conversation rotation opens a new Chat row). Two rules can fire the lock:

1. **First-turn confidence gate (non-English only).** When the user's first real message produces a reliable, high-confidence detection of a *non-English* language (e.g. Cyrillic message, Spanish/German/French sentence with diacritics, langdetect-confirmed Latin-script non-English), the chat locks to that language immediately. English is excluded from this branch because `en` is also the heuristic's fallback for any pure-ASCII multi-token input — locking on it would freeze ambiguous English-ish first turns.
2. **Two consistent reliable turns.** If the lock didn't fire on turn 1 (English, low-conf detection, unreliable), it fires the moment a turn's detected language root matches the previous turn's response language root. This handles English (always waits for the second confirming turn) and any case where the first turn was ambiguous.

After lock, `resolve_language_context` returns the stored `last_response_language` with `response_language_resolution_reason = "locked"` and skips the detector entirely. This is implemented in `_resolve_language_context_inner` (locked fast path) and `_decide_language_lock` in `backend/chat/language.py`. Stored on `Chat.language_locked` (boolean, default False); set once and never reset on existing chats.

Bilingual mid-conversation switches require the user to start a new conversation (`Start new chat`, or returning after the idle timeout). This is a deliberate trade-off: real bilingual switches mid-conversation are rare in B2B support, and locking eliminates flip-flopping when one off-language turn would otherwise change the bot's reply language.

### Default greeting

When a new conversation starts before the first real user question, Chat9 can return a default assistant greeting instead of `422`:

`I’m the <product_name> assistant and can help with documentation, product setup, integrations, and finding the right information. Ask your question.`

Behavior details:

- the canonical greeting is stored in English
- it is localized using the pre-question locale chain above
- `<product_name>` comes from `TenantProfile.product_name` when available, otherwise from the client name
- the stock widget shows this greeting for every **new conversation**: a truly new session, a session resumed after the conversation idle timeout (`CONVERSATION_IDLE_TIMEOUT_SECONDS`, see "Sessions, conversations, and history"), or after `Start new chat`
- resuming within the idle window does not repeat the greeting
- empty follow-up turns inside an active conversation still return `422 Question is required`; an empty message on a rotation-pending session is the bootstrap for the new conversation's greeting

### Chat channels

| Channel | Auth | Endpoint |
|---------|------|----------|
| Dashboard / API | `X-Api-Key` | `POST /chat` |
| Widget (public) | `bot_id` query param (bot `public_id`) | `POST /widget/chat?bot_id=…` |
| Debug tool | JWT + `bot_id` query param (bot `public_id`) | `POST /chat/debug?bot_id=…` |

The internal `/debug` and `/review` UI pages resolve the current bot automatically from the authenticated tenant; users are not expected to edit the URL manually.

### Sessions, conversations, and history

Two distinct notions:

- A **session** (`session_id`, UUID) identifies the *visitor in a browser*. The stock widget stores it in localStorage per bot (and per identified user) with a **sliding 24-hour TTL** — the visitor-identity lifetime. Sessions are scoped to a client — no cross-client leakage.
- A **conversation** (`Chat` row) is one continuous exchange. Messages within a conversation are stored and passed as history in subsequent turns (last N messages, `CHAT_HISTORY_TURNS`).

**Conversation rotation.** A session spans multiple conversations over time. When a message arrives and the session's latest conversation has been idle longer than `CONVERSATION_IDLE_TIMEOUT_SECONDS` (default 604800 = 7 days, measured on the chat's last activity), the backend lazily opens a **new Chat row under the same session_id**. The window is deliberately long — longer than the widget's own 24-hour session TTL — so a visitor returning within their widget session **continues the same conversation and is not re-greeted**; a fresh conversation (with a greeting) starts only after a genuinely long gap or `Start new chat`. The same threshold drives the `chat_session_ended` analytics sweeper for conversations that carry real messages, so behavior and metrics share one definition of an ended conversation; raising it likewise defers `chat_session_ended` for abandoned conversations to the same window.

Message-less mount chats (`/widget/session/init` creates a `Chat` on every widget mount before the visitor types) are reaped on a **separate short window**, `EMPTY_CHAT_IDLE_TIMEOUT_SECONDS` (default 1800 = 30 min). They never emit `chat_session_ended`, so this only controls how quickly they drop out of the `ix_chats_sweeper_pending` partial index — decoupled from the conversation window so raising the latter does not let empty mount chats pile up in that index for days.

What resets when a *new* conversation opens (after the window, or `Start new chat`) — not on an in-window return, which keeps all of it:

- prompt history (the LLM no longer sees yesterday's turns)
- clarification budget (`clarification_count`)
- loop-detection window
- greeting (shown again only when a new conversation actually opens — not on an in-window return)
- language lock

What survives rotation:

- the session itself (`session_id`, widget localStorage, contact/user context)
- previous conversations (archived; shown read-only in the widget above a "new conversation" separator, and listed in the dashboard Logs with per-conversation dividers)
- an **active escalation ticket still collecting the user's email** — one of two cases that *block* rotation: the returning user completes the ticket in the old conversation first. Pending escalation questions with no ticket behind them (pre-confirm offer, "describe your problem" prompt, post-ticket follow-up) do not block rotation and are simply abandoned with the old conversation.
- a **live operator handoff** (`operator_state = live`) — the other blocker. Rotating would open a fresh conversation with the bot answering while a human is mid-conversation on the old one, and the operator's thread would be orphaned. A handoff whose operator has really gone is released back to the bot by the sweeper first, so the block only ever holds a conversation someone is actually in.

A conversation the visitor closed (`ended_at` set — they answered "no" to the post-escalation "anything else?" follow-up) also rotates once idle: a visitor returning past the window starts fresh instead of hitting the "session is closed" reply.

Widget protocol: `GET /widget/history` returns the last two conversations flattened, `boundary_indices` (positions where a newer conversation starts) and `conversation_rotated` (true when the next message will open a new conversation — the widget renders a separator and requests a fresh greeting by POSTing an empty message with the existing `session_id`).

---

## 7. L2 Escalation Tickets (FI-ESC)

When the bot cannot adequately answer, the conversation is **escalated to a human** and a support ticket is created.

### Escalation triggers

| Trigger | What happens |
|---------|-------------|
| Low similarity score | No retrieved chunk is relevant enough |
| No documents | Client has no embedded documents |
| User phrase | The user asks for a person outright ("talk to a human", "connect me to an operator"). A message that merely *states a problem* ("I can't change the settings") does not qualify — see below |
| Support complaint (`user_complaint`) | Relevance guard classified the message as a complaint about support silence ("they haven't replied for two weeks") — pre-confirm offer leads with an apology; ticket priority ranks with an explicit human request |
| Manual escalation | Client calls `POST /chat/{session_id}/escalate` |

### Outright vs. inferred human requests

The human-request classifier (`detect_human_request`) returns three axes:
whether the user wants a human this turn, whether the message carries a
concrete problem support could act on, and whether the handoff was asked for
**outright** rather than inferred.

- Outright ask ("connect me to an operator") → escalates immediately, no
  pre-confirm step; the request is itself the confirmation.
- Outright ask with nothing to forward yet ("connect me to a human") → the bot
  asks the user to describe their question instead of minting an empty ticket,
  then escalates once the detail arrives.
- Inferred ask ("I can't change the settings", "please help me") → not
  escalated at all. The escalation FSM stands down and the RAG pipeline answers
  from the knowledge base; if retrieval finds nothing, the ordinary
  low-similarity / no-documents path offers the handoff and asks for consent
  through the pre-confirm question.

Inferred asks are kept out of the FSM entirely so the elicitation state above
is only ever opened by an outright ask — the reply that fills it in escalates
on content alone, so a state opened by an inferred plea would escalate a
handoff nobody asked for.

### What happens on escalation

All three automatic triggers (T-1, T-2, T-3) go through a **pre-confirm** step before a ticket is created:

1. The bot asks the user in one sentence whether they'd like their request forwarded to the human support team. If the user's email is already known via KYC/user context, the bot does **not** ask for it again.
2. **User confirms (yes):** An `EscalationTicket` record is created with a sequential number **ESC-####** (per client, e.g. ESC-0001). The bot sends a GPT-generated handoff message. The tenant's support inbox receives an **email notification** (via Brevo) with ticket details. If no email is on file, the bot politely asks the user to provide one.
3. **User declines (no):** Pre-confirm state is cleared; the chat continues normally.
4. **Unclear reply:** The bot asks once more for clarification; a second unclear response defaults to "yes".

The chat session is **not** immediately closed after escalation — the user can continue exchanging messages in the same session while the ticket is open.

### Ticket inbox (dashboard)

Tenants see all their tickets at `/escalations`:
- Status: `open` / `in_progress` / `resolved` / `auto_closed`
- Trigger type, session link, creation time
- One-click resolve button → `POST /escalations/{id}/resolve`

`in_progress` is set automatically when an operator takes the conversation
(either entry point — `POST /operator/chats/{id}/take` or simply answering via
`/messages`), so the inbox distinguishes a request someone is already holding
from one nobody has looked at. `open` and `in_progress` are both *active*:
repeat escalations inside one conversation reuse an active ticket instead of
minting a new number, and the new turn is threaded under the original
notification email.

A ticket whose conversation has gone idle past
`CONVERSATION_IDLE_TIMEOUT_SECONDS` is moved to `auto_closed` by the chat
session sweeper, from either active status — distinct from `resolved`, which
only a tenant sets and which means support actually handled the request.

**Abandoned claims bounce back.** An operator who claims a conversation and
never writes a word would otherwise be indistinguishable from one who answered
and let the conversation end: both age out to `auto_closed`. So a ticket that
is `in_progress` with *no operator message at all* in its chat, and whose
claim is older than `OPERATOR_CLAIM_BOUNCE_SECONDS` (12 h), is returned to
`open` and support is re-notified — once per ticket, since it is an outbound
email. This clock is deliberately far longer than
`OPERATOR_RELEASE_IDLE_SECONDS` (15 min, which un-mutes the bot for the
waiting visitor): firing an email on the shorter one would re-notify every
time an operator stepped away to check something. An operator who *did* reply
and then went quiet does not bounce — the visitor got an answer.

### Operator handoff analytics

`chat_session_ended` describes a whole chat, from `created_at`, and is emitted
**at most once**. It cannot also describe the stretch a human served: an
operator reopens a chat that was already reported as ended, so a second
emission would restate the first event with the idle wait folded in — session
counts would double and average duration would inflate. The operator-served
stretch therefore gets its own record.

**`operator_sessions`** — one row per stretch of a conversation served by a
human. Opened when the chat goes `live` (either entry point), stamped with the
first operator message, and closed by whichever path hands the chat back. A
row rather than columns on `chats` because a stretch is repeatable: an
operator releases, the bot answers, an operator takes over again — two
stretches in one chat, each with its own clock and its own handler.

Closing emits **`operator_session_ended`**:

| Property | Type | Description |
|----------|------|-------------|
| `chat_id` / `session_id` | `string` | Conversation and widget session |
| `operator_session_id` | `string` | The stretch — a chat can have several |
| `operator_user_id` | `string \| null` | `null` for an unattributed reply (inbound e-mail matching no tenant user) |
| `duration_ms` | `int` | How long the human held the conversation |
| `first_response_ms` | `int \| null` | From the **escalation ticket's `created_at`** to the first human reply — the clock support teams live by. `null` when the stretch was never answered, or when nobody had escalated |
| `answered` | `bool` | `false` = a claim that produced no reply at all |
| `ended_reason` | `string` | `released` \| `idle_timeout` \| `visitor_returned` \| `reconciled` |

`first_response_ms` is reported **once per ask, not once per stretch**. Nothing
moves a ticket out of `in_progress` on release, so a repeat takeover with no
new escalation would otherwise re-measure from the original `created_at` —
hours earlier, and already answered in minutes — and inflate the team's
first-response average. A stretch anchors only a ticket no earlier stretch has
claimed; a second takeover with no new ask reports no response time at all,
and a genuinely new escalation mints a new ticket that is anchored normally.

`first_response_ms` is deliberately **not** measured from `operator_joined_at`:
taking a chat and answering in it are roughly the same moment, so that clock
would measure nothing.

**The sweeper is the primary closer, not the release button.** A support
conversation ends when it ends and nobody presses "release", so most stretches
are closed by the sweeper's idle-release pass (`ended_reason=idle_timeout`).
Explicit release (`released`) and the lazy release on the visitor's next turn
(`visitor_returned`) are the two definite triggers. A fifth sweeper pass closes
a row whose chat is already back in `bot` — the gap the bulk-UPDATE release
cannot reach: a chat that went `live` before the table existed, or a release
whose close never landed. It stamps the chat's own `operator_released_at`
rather than sweep time, and reports `reconciled`. That pass writes to
`operator_sessions` only, so it cannot disturb the ticket auto-close above.

Each release closes its own stretch **in the same transaction**, so the chat is
never published as back-with-the-bot while its stretch is still open — an
operator answering in that instant starts a new stretch instead of having it
merged into the one being closed. "At most one open stretch per chat" is
enforced by a unique partial index, so two colleagues answering simultaneously
cannot produce two rows, and hence two events, for one human-served stretch.

### Widget UX

- A ticket banner shows the ticket number
- Input stays enabled: escalation does not close the chat. The visitor keeps talking to the bot while the ticket is open, and `ended_at` is set only when they confirm they need nothing further (see § 6 above)
- The widget offers an explicit escalate button only on an `llm_unavailable` failure bubble (gated by `can_escalate`) — not as a standing "talk to support" control
- `POST /widget/escalate` is a public endpoint (no auth required) — the widget can escalate without a JWT

### Escalation analytics

Two PostHog events are emitted on every escalation:

**`chat_escalated`** — fires once per escalation, immediately after ticket creation.

| Property | Type | Description |
|----------|------|-------------|
| `escalation_reason` | `string` | Why the bot escalated (see table below) |
| `escalation_trigger` | `string` | Low-level trigger enum value (e.g. `low_similarity`) |
| `chat_id` | `string` | Chat session UUID |

**`chat.turn`** — fires for every turn; escalated turns include:

| Property | Type | Description |
|----------|------|-------------|
| `escalated` | `bool` | `true` when this turn triggered escalation |
| `escalation_reason` | `string \| null` | Reason string; null only on non-escalated turns |
| `escalation_trigger` | `string \| null` | Trigger enum value; null on non-escalated turns |

#### Expected `escalation_reason` values

| Value | Trigger path |
|-------|-------------|
| `explicit_human_request` | User explicitly asked for a human (T-3) |
| `low_confidence_no_path` | RAG confidence too low and no clarification path available |
| `clarify_loop_limit` | Max clarification rounds reached without resolution |
| `guard_reject` | Guard pipeline rejected the turn and escalation was forced |
| `low_similarity` | No retrieved chunk met the similarity threshold (T-1), on a **second consecutive** weak turn — see below |
| `no_docs` | Tenant has no embedded documents (T-2) |

#### Second-attempt rule for weak retrieval

`low_similarity` means retrieval returned chunks but scored them below the handoff floor — the generated answer may still be useful, and the user may only need to rephrase. The first such turn therefore keeps its answer and offers nothing; it only records `chats.last_reply_was_low_confidence`. A second *consecutive* weak turn is treated as evidence the user is stuck and escalates through the pre-confirm gate as before. Any reply that did not come from weak retrieval resets the tracker, and it is treated as stale once the inactivity sweeper has reported the session ended.

`no_documents` is unaffected: retrieval found nothing at all, so there is no answer to preserve, and that path already asks the user to rephrase once before it escalates.

#### Escalation rate alert

A global sliding-window counter fires a `logger.warning` (captured by Sentry) and a PostHog `escalation.rate_exceeded` event when more than `ESCALATION_ALERT_THRESHOLD` (default: **10**) escalations occur within `ESCALATION_ALERT_WINDOW_SECONDS` (default: **3600 s**).

Both thresholds are configurable via environment variables. To tune for a specific tenant volume, set these in Railway:

```
ESCALATION_ALERT_THRESHOLD=15
ESCALATION_ALERT_WINDOW_SECONDS=3600
```

**Baseline:** With a healthy bot covering its knowledge base well, expect ≤ 5% escalation rate (≤ 5 per 100 turns). Rates consistently above 10% indicate a knowledge-base gap or a guard miscalibration and warrant investigation via the Gap Analyzer.

---

## 8. Response Controls / Disclosure (FI-DISC)

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

## 9. Widget personalization via `userHints`

By default the widget is **anonymous** — no information about the end user is passed to the bot. The tenant frontend may optionally supply plain-JSON `userHints` to `Chat9Widget.start({...})` (or push them later via `Chat9Widget.setHints({...})`) to personalize the session.

### How it works

1. The tenant page calls `Chat9Widget.start({ userHints: { name, email, locale, plan_tier, audience_tag } })`. Hints can also be updated mid-session via `Chat9Widget.setHints({...})` — useful for SPA login/logout flows.
2. The loader forwards the hints object to the widget iframe via a `chat9:hints` postMessage handshake (no URL-leakage). Re-calling `setHints` posts a fresh `chat9:hints` to the running iframe.
3. The widget calls `POST /widget/session/init` with `{ bot_id, user_hints }`.
4. Backend `sanitize_user_hints` (see `backend/widget/service.py`) whitelists allowed keys, caps lengths, and validates `email`/`locale`. The result is patched into `chats.user_context` via `apply_identity_context_patch`.
5. If hints carry `user_id` or `email`, a `ContactSession` row is created (synthesized `user_id="hint:<email>"` when only email is supplied) so cross-session history works.
6. In the RAG prompt only safe fields (`plan_tier`, `locale`, `audience_tag`) are included — no raw PII — plus two booleans derived from the identity: `identified=yes` when hints carried a `user_id` or `email`, and `contact_email_on_file=yes` when an email is on file. The values themselves never reach the prompt; the flags stop the bot from telling an already-signed-in user to sign in and write to support, or from asking for contact details support already has.
7. Escalation email metadata pulls `email`, `name`, `locale`, `user_id` from the same `Chat.user_context`.

### Session modes

| Mode | Description |
|------|-------------|
| `hints` | At least one hint field survived sanitization; personalization context is attached. |
| `anonymous` | No hints supplied (or all dropped during sanitization). |

### Trust boundary

Hints are **untrusted**. They come straight from the browser; anyone inspecting the page can change them. They are used only for:

- greeting personalization, locale selection, and other text personalization;
- escalation email metadata so the support team sees `Email:` / `Name:`;
- escalation priority heuristic (`plan_tier` → ticket priority).

Do **not** use hints for access control, paid-tier gating, audit logs that imply verified-identity semantics, or routing where impersonation matters. A future verified-identity path will be designed as Stage 2 (separate task) — it is intentionally out of scope today.

---

## 10. Embeddable Widget

### How embedding works

Users copy a snippet from the **Dashboard**. The snippet has two parts: a `<script>` that loads `widget.js` and registers `window.Chat9Widget`, then a second `<script>` that calls `Chat9Widget.start(config?)` to mount the chat. The bot is identified by the `data-bot-id` attribute on the loader script tag. The loader and widget UI are served from a separate Vercel project (`chat9-widget`) at `widget.getchat9.live` — the dashboard project doesn't serve any widget assets.

Example (placeholders — the Dashboard fills in your real bot public ID):

```html
<script src="https://widget.getchat9.live/widget.js" data-bot-id="YOUR_BOT_PUBLIC_ID"></script>
<script>
  Chat9Widget.start();
</script>
```

The widget supports two modes (passed in the `start({...})` config):
- **Bubble** (default): floating chat button in the bottom-right corner.
- **Inline**: renders inside an existing container. Pass `mode: "inline"` and `target: "<elementId>"`, and put an empty `<div id="<elementId>"></div>` where you want the widget.

Loader runtime (`frontend/apps/widget-loader/src/index.ts`, IIFE bundle, ~3 KB gzip):
- Reads the bot `public_id` from `data-bot-id` on the script tag (the only data-attribute it consumes).
- Loading the script does **not** mount any UI on its own — only `Chat9Widget.start(config?)` does.
- Lifecycle methods on `window.Chat9Widget`:
  - `start(config?)` mounts the FAB + iframe; no-op (with warning) if already started.
  - `stop()` unmounts the DOM and listeners; the script and `Chat9Widget` stay registered, ready for another `start()`.
  - `setHints(hints | null)` updates identity in a running iframe (or remembers it for the next `start()`); the widget iframe automatically remounts the chat when identity changes so per-conversation state resets cleanly.
  - `isStarted()` returns `true` while mounted.
  - `destroy()` is terminal — `stop()` + `delete window.Chat9Widget`. Most consumers want `stop()`.
- Tenant-facing `start({...})` options: `mode`, `color`, `position`, `target`, `topClearance`, `userHints`. There are no `data-*` fallbacks.
- Internal-only options on the same call (not advertised in customer docs): `apiBase`, `widgetBase`. Both are **auto-inferred from the loader's script origin** — `widget.getchat9.live` resolves to `https://getchat9.live` for the API and `https://widget.getchat9.live/v1/` for the widget bundle; `widget-ru.getchat9.live` resolves to `https://api-ru.getchat9.live` and `https://widget-ru.getchat9.live/v1/`. Tenants switch to the RU edge by changing `<script src>`, not by passing `apiBase`. The override is only used in our own staging/dev configs.
- Injects an `<iframe>` pointing to `${widgetBase}?botId=…&locale=<userHints.locale|navigator.language>&apiBase=…&parentOrigin=<page origin>`.
- `userHints` (if any) are **not** put in the iframe URL — they are delivered by a `postMessage` handshake (widget posts `chat9:ready`, the loader replies with `chat9:hints` or `chat9:no-hints`) so the values never leak into browser history, server logs, or `Referer` headers. The `targetOrigin` for the reply is the widget origin (never `*`). The current hints state persists across `stop`/`start` cycles inside the loader closure (the message listener itself is bound per-mount and removed in `stop()`).
- The iframe renders the full `widget-app` Preact bundle (`frontend/apps/widget-app/`).

The iframe isolation means the widget has **no access to the host page DOM** — clean CORS boundary, no XSS risk. Cross-origin calls from the iframe to the dashboard API go through the CORS middleware in `frontend/middleware.ts` with the allowlist in `WIDGET_ALLOWED_ORIGINS`.

### Widget features

- Streaming-style message display
- Session continuity within a page load
- Escalation button → triggers `POST /widget/escalate`
- Ticket banner after escalation
- Locale passed automatically (`navigator.language`)
- "Powered by Chat9 →" footer (links to getchat9.live)

### Rate limits (source of truth)

The backend uses `slowapi` limits from route decorators (see `backend/*/routes.py`).
`POST /widget/chat` has two stacked limits: one per-bot and one per-client.

| Endpoint | Limit | Key scope |
|---|---|---|
| `POST /auth/register` | `5/hour` | IP (default slowapi key) |
| `POST /auth/login` | `10/minute` | IP (default slowapi key) |
| `POST /auth/forgot-password` | `3/hour` | IP (default slowapi key) |
| `POST /auth/reset-password` | `5/hour` | IP (default slowapi key) |
| `POST /tenants/me/api-keys/rotate` | `10/hour` | Owner JWT subject (`owner:<user_id>`) |
| `DELETE /tenants/me/api-keys/{key_id}` | `20/hour` | Owner JWT subject (`owner:<user_id>`) |
| `POST /documents` | `20/hour` | IP (default slowapi key) |
| `POST /chat` | `30/minute` | IP (default slowapi key) |
| `POST /chat/{session_id}/escalate` | `30/minute` | IP (default slowapi key) |
| `POST /search` | `30/minute` | IP (default slowapi key) |
| `GET /widget/config` | `30/minute` | `bot_id + IP` |
| `POST /widget/session/init` | `10/minute` | IP (widget-specific key func) |
| `POST /widget/chat` | `WIDGET_CHAT_PER_CLIENT_RATE` (or `120/minute` by default, `1000/minute` in development when unset) | per `bot_id` |
| `POST /widget/chat` | `30/minute` | `bot_id + IP` |
| `GET /widget/history` | `30/minute` | `bot_id + IP` |
| `POST /widget/escalate` | `20/minute` | `bot_id + IP` |

---

## 11. Dashboard

The web dashboard at `getchat9.live` is a Next.js 14 app. Authenticated pages use a **left sidebar** for navigation (main items, **SETTINGS**, and **Admin** for `is_admin` users); the top bar shows brand, email, and logout.

| Page / route | What it shows |
|--------------|---------------|
| **Dashboard** (`/dashboard`) | **Your Bot ID** (bot's `public_id`, used as `data-bot-id`), **API key** (server-to-server `X-Api-Key`), **embed code** snippet; banner linking to Agents if OpenAI key is missing |
| **Knowledge** (`/knowledge`) | Upload files, add URL sources, trigger embeddings/crawls, health indicators, delete; unified indexed sources table (replaces legacy `/documents`) |
| **Agents** (`/settings`) | Per-client **OpenAI API key** (encrypted), save/update/remove |
| **Logs** (`/logs`) | Full chat history across sessions; thumbs up/down feedback |
| **Review** (`/review`) | Bad answers (thumbs down) with ideal answer input |
| **Escalations** (`/escalations`) | L2 ticket inbox; resolve tickets |
| **Debug** (`/debug`) | Run RAG debug; answer + retrieval table with chunk previews and scores (code blocks use inline copy) |
| **Response controls** (`/settings/disclosure`) | Disclosure level (Detailed / Standard / Corporate) |
| **Widget settings** (`/widget-settings`) | Bot ID copy + Link Safety configuration (allowed domains for external link guard) |
| **Admin** (`/admin/metrics`, admins only) | Platform-wide metrics |

---

## 12. Security

| Area | Implementation |
|------|---------------|
| Authentication | JWT (HS256), bcrypt passwords; user access tokens include `typ=chat9_user` |
| Data isolation | All queries scoped by `tenant_id`; no cross-tenant access possible |
| API key storage | AES-GCM encrypted at rest |
| PII protection | Regex redaction before all OpenAI calls |
| Rate limiting | `slowapi` with Redis-backed shared counters in production; route-level limits are listed in the "Rate limits (source of truth)" section above |
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
- `main` — default branch and current production branch; Vercel and Railway auto-deploy from `main`
- feature/fix branches — open PRs into `main`

---

*For the chronological development history, see [`PROGRESS.md`](./PROGRESS.md).*  
*For the feature registry with code pointers, see [`IMPLEMENTED_FEATURES.md`](./IMPLEMENTED_FEATURES.md).*  
*For the tech stack details, see [`03-tech-stack.md`](./03-tech-stack.md).*
