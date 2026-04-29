# KYC / Widget User Identification

## Purpose

Chat9 supports optional widget-side user identification for tenants that want to attach verified end-user context to a chat session.

In the codebase this feature is called **KYC** or **identified widget sessions**. It is implemented in:

- `backend/widget/routes.py` (`widget_session_init`, `_resolve_widget_identity`)
- `backend/core/security.py` (`validate_kyc_token_detail`, `generate_kyc_token`)
- `backend/auth/routes.py` (`/auth/me/widget-token` — server-issued token for the dashboard's own dogfood widget)
- `backend/tenants/widget_chat_gate.py` (single source of truth for bot/tenant resolution)
- `backend/models.py` (`ContactSession`, `Chat.user_context`)

This document describes the real production flow as of April 29, 2026.

## High-level flow

1. A tenant generates a KYC signing secret in Dashboard -> Settings -> Widget API.
2. The tenant stores that secret on their own backend.
3. When they want to identify an end user, their backend creates a short-lived HMAC token.
4. Their integration calls `POST /widget/session/init` with:
   - `bot_id` (public bot ID, safe to expose in HTML)
   - optional `identity_token`
   - optional `locale`
5. Chat9 validates the token.
6. If valid, Chat9 either resumes an eligible identified chat for the same `user_id` or creates a new one.
7. Chat9 stores the validated user context in `chats.user_context`.
8. Chat9 also maintains an identified-user lifecycle row in `contact_sessions` (table `contact_sessions`, columns `tenant_id`, `contact_id`).
9. The response returns:
   - `session_id`
   - `mode` = `identified` or `anonymous`
10. Later `POST /widget/chat` calls reuse the returned `session_id`.

## Request and response

### `POST /widget/session/init`

Request body:

```json
{
  "bot_id": "ch_your_bot_public_id",
  "identity_token": "optional-base64-json.hmac-signature",
  "locale": "optional-browser-locale"
}
```

Response body:

```json
{
  "session_id": "uuid",
  "mode": "identified"
}
```

The response never echoes user PII back to the tenant integration.

## When KYC is sent

KYC is sent during **session initialization**, not during regular chat turns.

- It does **not** happen automatically just because the user opened the page.
- It does **not** happen on `POST /widget/chat`.
- It happens when `POST /widget/session/init` is called with an `identity_token`.

### How the stock `embed.js` handles KYC

The standard `embed.js` supports KYC via `window.Chat9Config.identityToken`:

```html
<script>
  window.Chat9Config = {
    widgetUrl: "https://getchat9.live",
    identityToken: "{{ server_generated_token_or_null }}"
  };
</script>
<script src="https://.../embed.js" data-bot-id="YOUR_BOT_ID"></script>
```

The token is delivered to the widget iframe via a deterministic two-step
`postMessage` handshake — never via the URL (which would leak signed tokens
into browser history, server access logs and `Referer` headers):

1. `embed.js` creates the iframe, stamping `?parentOrigin=<page origin>` so
   the iframe knows where to send messages back.
2. Inside the iframe, `frontend/app/widget/page.tsx` mounts, registers a
   `message` listener, then posts `{type: "chat9:ready"}` to `window.parent`
   with the explicit `parentOrigin` as `targetOrigin`.
3. `embed.js` receives `chat9:ready`, removes its listener (no leak across
   re-init / HMR cycles), and posts back either:
   - `{type: "chat9:identity", identityToken: "<...>"}` if a token was
     configured, **only** to the known `widgetBase` origin — never `*`; or
   - `{type: "chat9:no-identity"}` if no token was configured — explicit
     anonymous signal so the widget proceeds without waiting.
4. The widget reads the message, sets state, and only then mounts
   `ChatWidget`. If the token was provided it calls `POST /widget/session/init`
   with `bot_id` + `identity_token`.

If the page is opened directly in a tab (`window.parent === window`, no
embedding iframe), the widget skips the handshake and resolves to anonymous
synchronously.

This contract is **internal between `embed.js` and the widget** — customers
never see it. Their integration is just: set `window.Chat9Config.identityToken`
to a server-rendered token (or `null` for anonymous visitors) and load
`embed.js`. A single embed snippet handles both anonymous and identified
visitors on the same page.

## Token format

The token is not a JWT library artifact. It is a custom string built as:

```text
base64url(json_payload).hex_hmac_sha256_signature
```

Payload generation:

- JSON is serialized with sorted keys and compact separators
- `exp` and `iat` are Unix timestamps in seconds
- signature is `HMAC-SHA256(payload_b64, secret_key)`

Reference implementation:

- `generate_kyc_token(...)` in `backend/core/security.py`

## Payload fields

### Required

- `user_id`
  - any non-empty string (after trimming)
- `exp`
  - token expiration time, Unix timestamp in seconds

### Recommended

- `iat`
  - token issued-at time, Unix timestamp in seconds (stored in the signed payload; not used for windowing)

### Optional

- `email`
- `name`
- `plan_tier`
- `audience_tag`
- `company`
- `locale`

The server validates `user_id` (non-empty), `exp` (not in the past), and the HMAC signature. Any other fields are passed through into `UserContext` and `chats.user_context` but are **not** verified against the tenant record. The tenant is resolved from the `bot_id` in the `POST /widget/session/init` request body, not from the token. Unknown extra fields are ignored by the `UserContext` model (`extra="ignore"`).

## Field semantics

### `user_id`

- Required
- Type: string
- Validation: must be a non-empty string after trimming
- No UUID or regex requirement exists in code

### `exp`

- Expiration timestamp
- If current server time is greater than `exp`, the token is rejected as `expired`

### `iat`

- Issued-at timestamp
- Included in the token, but not used for window enforcement beyond being stored in the signed payload
- Removed from the validated context before storage

### `audience_tag`

- Optional free-form audience/segment label
- No controlled enum in code
- Example values: `vip`, `b2b`, `new_user`, `enterprise_admin`

## Validation outcomes

`validate_kyc_token_detail(...)` can return these failure reasons:

- `malformed`
- `expired`
- `missing_user_id`
- `bad_signature`

If validation fails, the widget flow does not hard-fail. Chat9 falls back to anonymous mode.

## `mode` meanings

### `identified`

Returned when:

- `identity_token` is present
- signature is valid under the tenant's signing secret
- token is not expired
- `user_id` is a non-empty string
- payload passes `UserContext` validation

Effects:

- Chat9 resumes an eligible identified chat, or creates one during `session/init`
- validated context is stored in `chats.user_context`
- later chat turns can use the stored context

### `anonymous`

Returned when:

- no token was sent, or
- token validation failed

Effects:

- widget still works
- no KYC context is attached
- if `locale` is supplied during `session/init`, Chat9 may still create a chat row with `user_context.browser_locale`
- otherwise the chat row is typically created only on the first `POST /widget/chat`

## What is stored

On successful identification, Chat9 stores validated payload fields in `chats.user_context`.

Typical stored fields:

- `user_id`
- `email`
- `name`
- `plan_tier`
- `audience_tag`
- `company`
- `locale`
- optional `browser_locale` merged from request `locale`

Internal fields removed before storage:

- `exp`
- `iat`


For identified users, Chat9 also maintains a `contact_sessions` row with:

- `tenant_id` (resolved from the `bot_id`)
- `contact_id` (the payload's `user_id`)
- best-known identity fields (`email`, `name`, `plan_tier`, `audience_tag`)
- `session_started_at`
- optional `session_ended_at`
- `conversation_turns`

Only one active `contact_sessions` row is allowed per `tenant_id + contact_id` (partial unique index `uq_contact_sessions_tenant_contact_active`, where `session_ended_at IS NULL`).

## Session continuity and resume

The widget now has two different continuity mechanisms:

### Anonymous users

- continuity is browser-local only
- the widget stores `session_id` in `localStorage`
- the same browser can continue the same chat for up to 24 hours
- another browser or device gets a new session

### Identified users

- resume is decided on the backend during `POST /widget/session/init`
- matching key: `tenant_id + contact_id` (the token's `user_id`)
- Chat9 resumes the latest eligible chat when:
  - `ended_at is null`
  - the last chat activity is within 24 hours
- otherwise Chat9 creates a new chat and a new active `contact_sessions` row

### Closed chats

- a chat is considered closed when `chats.ended_at` is set
- closed chats are never resumed
- if the widget receives `chat_ended = true`, it clears the stored `session_id`
- the widget shows `Start new chat`, keeps the old transcript visible, and starts a new session below it on the next message

## What affects the LLM

Only a safe subset is injected into the RAG prompt:

- `plan_tier`
- `locale`
- `audience_tag`

PII such as `user_id`, `email`, and `name` are not put into the prompt line.

Reference:

- `_user_context_prompt_line(...)` in `backend/chat/service.py`

## What affects escalations

Escalation flows may read these fields from `user_context`:

- `user_id`
- `email`
- `name`
- `plan_tier`

This means identified sessions produce richer escalation tickets than anonymous sessions.

Reference:

- `backend/escalation/service.py`

## Cross-tenant KYC (Chat9 dogfood support widget)

The KYC secret lives in `tenants.kyc_secret_key` **per tenant** (encrypted
at rest, decrypted via `get_kyc_decrypted_keys_for_validation`). For a
single-tenant flow — a customer embeds *their own* bot on *their own*
site — the same tenant signs and validates the token, so a single
`generate_kyc_token(payload, secret)` on their server is enough.

The dashboard's own embedded support widget is a **cross-tenant** flow:

- A user from tenant **A** (an ACME-tenant employee) is logged into the
  Chat9 dashboard.
- The dashboard's bottom-right support FAB targets the Chat9 support bot,
  which is owned by tenant **B** (Chat9 itself, configured via
  `NEXT_PUBLIC_CHAT9_BOT_ID`).
- The token must be signed with **B's** secret because `widget_session_init`
  resolves the validating tenant from the `bot_id`, not from the caller.

This is handled by `GET /auth/me/widget-token?bot_id=<chat9_bot_public_id>`:

- The endpoint resolves the bot's owner via `get_bot_and_tenant_for_widget_session`
  (`NOT_FOUND` / `INACTIVE` only — no `NO_OPENAI` gate, since session/init
  doesn't need to call the LLM).
- It then signs the payload with **that owner's** `kyc_secret_key`.
- The same secret will validate it on `/widget/session/init` — symmetry
  restored, identified mode unlocked.

Backward compatibility: when `bot_id` is omitted, the legacy "sign with the
caller's own tenant" path still works — that is the right behavior for any
custom server-rendered integration where the user's tenant *is* the bot
owner.

For the cross-tenant flow to work on a fresh deploy, the bot-owner tenant
must have a KYC secret configured (Dashboard → Settings → Widget API →
Generate secret). Without it, `/auth/me/widget-token` returns 404 with
`"No identity secret configured."` — same surface as for any other tenant.

## Secret management

Tenant-facing secret endpoints:

- `POST /tenants/me/kyc/secret`
- `GET /tenants/me/kyc/status`
- `POST /tenants/me/kyc/rotate`

Behavior:

- secret is shown only once when generated or rotated
- stored encrypted at rest
- previous secret remains valid for 1 hour after rotation to support rolling deploys

## Operational notes

- Identified-session metrics in the dashboard are based on chats with non-null `user_context`
- invalid KYC tokens are logged as validation failures without logging raw PII
- rate limit for `POST /widget/session/init` is `20/minute`

## Example integration sequence

1. Tenant backend loads the stored signing secret.
2. Tenant backend builds payload:

```json
{
  "user_id": "cust_12345",
  "email": "user@example.com",
  "plan_tier": "growth",
  "audience_tag": "b2b",
  "locale": "en-US",
  "exp": 1775399700,
  "iat": 1775399400
}
```

3. Tenant backend signs the payload and returns the token to the frontend (e.g. in the login response or server-rendered via `window.Chat9Config`).
4. The widget calls `POST /widget/session/init`:

```json
{
  "bot_id": "ch_your_bot_public_id",
  "identity_token": "signed-token",
  "locale": "en-US"
}
```

5. Chat9 resolves the tenant from `bot_id`, validates the token, and returns:

```json
{
  "session_id": "1c576fd8-cf10-4b58-a4e7-460ea0d19dbe",
  "mode": "identified"
}
```

6. The integration uses that `session_id` for subsequent `POST /widget/chat` calls.
