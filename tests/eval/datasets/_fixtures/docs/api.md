---
title: API
description: Chat9 HTTP API — public widget endpoints documented inline, full dashboard / management surface linked to the live Swagger UI.
---

Chat9 exposes its HTTP API at **`https://api.getchat9.live`**. There
are two surfaces:

- **Public widget endpoints** (`/widget/*`, `/embed.js`,
  `/widget/config`) — unauthenticated, identified by your bot's
  `public_id`. This is what the embedded chat widget on your site
  calls. Documented in detail below.
- **Authenticated dashboard / management endpoints** — JWT-bearer
  authenticated. Sign-up, tenant settings, bot CRUD, document upload,
  chat logs, gap analyzer, escalations, FAQ workflow, etc. Catalogued
  below with one-line summaries; for request/response shapes use the
  **canonical live spec**:

  - Interactive: [api.getchat9.live/docs](https://api.getchat9.live/docs)
  - OpenAPI JSON: [api.getchat9.live/openapi.json](https://api.getchat9.live/openapi.json)

When this page and the live spec ever disagree, the live spec wins.

## Public widget endpoints

The widget on your site talks to Chat9 over a small set of public
HTTP endpoints. You don't normally call them yourself — `embed.js`
handles everything — but they're documented here for custom
integrations, debugging, or signing identified-session tokens from
your own backend.

All public widget endpoints are **unauthenticated** in the
HTTP-headers sense: a request is identified by the bot's `public_id`
(starts with `ch_`, same value as your widget `data-bot-id`). Each
endpoint has its own per-IP rate limit listed below. Paths are
appended to `https://api.getchat9.live`.

### `POST /widget/session/init`

Start a new widget chat session for a visitor. Returns a `session_id`
you'll use in subsequent calls.

**Request body** (JSON):

| Field | Required | What it is |
|-------|----------|------------|
| `bot_id` | yes | Bot `public_id` (same value as your widget `data-bot-id`). |
| `identity_token` | optional | HMAC-SHA256 token signed by your server to enable an [identified session](/docs/embedding-the-widget). |
| `locale` | optional | Browser locale hint, e.g. `ru-RU`. |

**Response** (JSON):

```json
{
  "session_id": "5f4a3b1c-...-uuid",
  "mode": "anonymous"
}
```

`mode` is `identified` when a valid `identity_token` was provided,
`anonymous` otherwise.

**Errors:** `404` (bot not found), `403` (tenant inactive), `400`
(OpenAI API key missing for the bot).

**Rate limit:** 10 requests per minute per visitor IP.

### `POST /widget/chat`

Send a user message and receive the bot's answer as a
**Server-Sent Events** stream.

**Query parameters:**

| Name | Required | What it is |
|------|----------|------------|
| `bot_id` | yes | Bot `public_id`. |
| `session_id` | optional | UUID returned by `/widget/session/init`. Omit it to start a fresh session in this call. |
| `locale` | optional | Browser locale hint. |

**Request body** (JSON):

```json
{ "message": "What is Chat9?" }
```

The `message` field is **capped at 1000 characters** — longer
messages are rejected before reaching the bot.

**Response:** the response uses `Content-Type: text/event-stream`. The
server emits two kinds of events while it works:

- `data: {"type":"chunk","text":"..."}` — partial reply text, emitted as
  the model streams. Concatenate `text` from successive chunks to
  display the answer as it arrives.
- `data: {"type":"done","text":"<full reply>","session_id":"...","chat_ended":false}` —
  emitted exactly once at the end with the complete reply, the
  `session_id` (so you can store it), and a `chat_ended` flag that's
  `true` when the bot decided to close the chat (e.g. after a manual
  escalation).

If something goes wrong server-side you may instead get one
`{"type":"error","code":...,"message":"..."}` event before the stream
closes.

**Errors before the stream starts:** `404` (bot not found), `422`
(invalid `session_id` or empty message).

**Rate limit:** 30 requests per minute per visitor IP.

### `GET /widget/history`

Reload the messages from an existing session — useful when the user
refreshes the page and you want to rehydrate the conversation.

**Query parameters:** `bot_id`, `session_id` (both required).

**Response:** JSON list of messages with `role`, `content`,
`created_at`.

**Rate limit:** 30 requests per minute per IP.

### `POST /widget/escalate`

Open a support ticket from the widget, e.g. when the user clicks
"Talk to a human". Returns the ticket number (`ESC-42` style) which
you can show as confirmation.

**Query parameters:** `bot_id`, `session_id` (both required).

**Request body:**

```json
{ "reason": "Optional free-form text from the user" }
```

**Rate limit:** 20 requests per minute per IP.

### `GET /embed.js`

The widget loader script. Embed it on your site with:

```html
<script
  src="https://widget.getchat9.live/widget.js"
  data-bot-id="ch_...">
</script>
```

You don't normally fetch this directly — the script tag does it.

### `GET /widget/config`

Returns widget configuration the loader uses (link-safety labels,
allowed embed domains, etc.). Takes `bot_id` as a query parameter.
You don't usually call it from your code.

## Authenticated dashboard API

The endpoints below are everything the dashboard at
[getchat9.live](https://getchat9.live) talks to. They're the same
surface you'd hit from a custom integration that runs server-side.

**Authenticate once, then use the bearer token:**

1. `POST /auth/login` with `{ "email": "...", "password": "..." }`
   returns `{ "access_token": "..." }`.
2. Send subsequent requests with header
   `Authorization: Bearer <access_token>`.

The bot's widget `ck_...` API key (issued in
[Settings → API keys](/docs/api-keys)) is a **separate** credential
used for direct programmatic widget access; it does not authenticate
dashboard routes.

For request/response shapes, payload validation, and example curl
commands, jump into the live spec —
[api.getchat9.live/docs](https://api.getchat9.live/docs) groups the
endpoints below by tag.

### Auth

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/auth/register` | Create a new user account. Sends a verification email; no token is returned until the email is verified. |
| POST | `/auth/login` | Exchange `email + password` for a bearer `access_token`. |
| POST | `/auth/logout` | Invalidate the current access token. |
| POST | `/auth/verify-email` | Confirm an email-verification token sent during registration. |
| POST | `/auth/forgot-password` | Request a password-reset email. |
| POST | `/auth/reset-password` | Confirm a password-reset token and set a new password. |
| GET  | `/auth/me` | Return the current user. |
| GET  | `/auth/me/widget-token` | Return a server-signed identity token your backend can pass to `/widget/session/init` for [identified sessions](/docs/embedding-the-widget). |

### Tenants

| Method | Path | What it does |
|--------|------|--------------|
| GET    | `/tenants/me` | Get the current user's tenant — settings, OpenAI key status, KYC config, retention. |
| PATCH  | `/tenants/me` | Update tenant settings (OpenAI API key, support email, etc.). |
| GET    | `/tenants/me/kyc/status` | Check whether widget identity-token signing is enabled for this tenant. |
| POST   | `/tenants/me/kyc/secret` | Generate a new HMAC-SHA256 signing secret for identified sessions. |
| POST   | `/tenants/me/kyc/rotate` | Rotate the existing identity-token signing secret. |
| GET    | `/tenants/me/privacy` | Read the tenant's privacy / data-retention configuration. |
| PUT    | `/tenants/me/privacy` | Update privacy / retention settings. |

### Bots

| Method | Path | What it does |
|--------|------|--------------|
| GET    | `/bots` | List bots in the current tenant. |
| POST   | `/bots` | Create a new bot. |
| GET    | `/bots/{bot_id}` | Get one bot by `public_id`. |
| PATCH  | `/bots/{bot_id}` | Update name, agent instructions, or link-safety config. |
| DELETE | `/bots/{bot_id}` | Delete a bot. |
| GET    | `/bots/{bot_id}/disclosure` | Get the bot's response-disclosure configuration. |

### Documents and knowledge sources

| Method | Path | What it does |
|--------|------|--------------|
| POST   | `/documents` | Upload a document (multipart, `pdf`/`md`/`mdx`/`docx`/`doc`/`txt`/`json`/`yaml`/`yml`). |
| GET    | `/documents` | List uploaded documents. |
| GET    | `/documents/{document_id}` | Get one document's metadata. |
| DELETE | `/documents/{document_id}` | Delete a document. |
| GET    | `/documents/{document_id}/health` | Inspect ingestion-health warnings (low information density, parse issues). |
| POST   | `/documents/{document_id}/health/run` | Re-run the health check for a document. |
| GET    | `/documents/sources` | List URL knowledge sources. |
| POST   | `/documents/sources/url` | Register a new URL source (same-domain crawler). |
| GET    | `/documents/sources/{source_id}` | Get one URL source. |
| PATCH  | `/documents/sources/{source_id}` | Update a URL source (e.g. base URL). |
| POST   | `/documents/sources/{source_id}/refresh` | Trigger a re-crawl. |
| DELETE | `/documents/sources/{source_id}` | Remove a URL source. |
| DELETE | `/documents/sources/{source_id}/pages/{document_id}` | Remove one indexed page from a URL source. |

### Chat (dashboard and debugging)

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/chat` | Server-side chat using the tenant's `ck_...` widget API key — same answer pipeline as the widget, intended for headless clients. |
| POST | `/chat/debug?bot_id=...` | Authenticated chat debug — returns the same answer plus the retrieved chunks, scores, and intermediate guard verdicts. Used by the **Debug** dashboard page. |
| GET  | `/chat/history/{session_id}` | Server-side history fetch (mirrors `/widget/history`). |
| GET  | `/chat/sessions` | List recent sessions for the current tenant. |
| GET  | `/chat/logs/session/{session_id}` | Full conversation transcript for a session. |
| POST | `/chat/logs/session/{session_id}/delete-original` | Erase the original transcript text after retention compliance review. |
| POST | `/chat/messages/{message_id}/feedback` | Submit thumbs-up / thumbs-down feedback on a single assistant turn. |
| GET  | `/chat/bad-answers` | Listing for the **Review bad answers** dashboard page. |

### Escalations

| Method | Path | What it does |
|--------|------|--------------|
| GET  | `/escalations` | List escalation tickets in the tenant. |
| GET  | `/escalations/{ticket_id}` | Get one ticket (status, transcript, trigger, priority). |
| POST | `/escalations/{ticket_id}/resolve` | Mark a ticket resolved. |
| POST | `/escalations/{ticket_id}/delete-original` | Erase the conversation transcript after retention review. |

### Gap analyzer

| Method | Path | What it does |
|--------|------|--------------|
| GET  | `/gap-analyzer/summary` | Aggregated counts by source (docs gaps, user signals, FAQ candidates). |
| POST | `/gap-analyzer/recalculate` | Trigger a fresh Gap Analyzer pass. |
| POST | `/gap-analyzer/{source}/{gap_id}/dismiss` | Dismiss a gap from the queue. |
| POST | `/gap-analyzer/{source}/{gap_id}/reactivate` | Re-open a previously dismissed gap. |
| POST | `/gap-analyzer/{source}/{gap_id}/draft` | Generate a draft answer / doc snippet for the gap. |

### Knowledge profile and FAQ

| Method | Path | What it does |
|--------|------|--------------|
| GET    | `/knowledge/profile` | Read the auto-extracted topics that summarise your knowledge base. |
| PATCH  | `/knowledge/profile` | Edit the knowledge profile. |
| GET    | `/knowledge/faq` | List FAQ candidates produced by Gap Analyzer / log analysis. |
| PUT    | `/knowledge/faq/{faq_id}` | Edit a FAQ candidate. |
| POST   | `/knowledge/faq/{faq_id}/approve` | Approve a single FAQ candidate. |
| POST   | `/knowledge/faq/approve-all` | Bulk-approve FAQ candidates. |
| POST   | `/knowledge/faq/{faq_id}/reject` | Reject a candidate. |
| DELETE | `/knowledge/faq/{faq_id}` | Delete a FAQ entry. |

### Privacy and audit

| Method | Path | What it does |
|--------|------|--------------|
| GET    | `/tenants/privacy/pii-events` | List PII-redaction audit events. |
| DELETE | `/tenants/privacy/pii-events/retention` | Run retention cleanup on PII events. |

### Health

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/health` | Liveness probe — returns `{"status":"ok"}`. |

## A note on chat answer models

The `/widget/chat` endpoint generates answers with `gpt-5-mini` and
runs lightweight guard / validation checks with `gpt-4o-mini` by
default. See [Pricing and limits](/docs/pricing-and-limits) for cost
guidance.

## Need help?

Email **support@getchat9.live** if you hit something this page
doesn't cover.
