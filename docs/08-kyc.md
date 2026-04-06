# KYC / Widget User Identification

## Purpose

Chat9 supports optional widget-side user identification for tenants that want to attach verified end-user context to a chat session.

In the codebase this feature is called **KYC** or **identified widget sessions**. It is implemented in:

- [backend/routes/widget.py](/Users/tabularasa/Projects/ai-chatbot/backend/routes/widget.py)
- [backend/core/security.py](/Users/tabularasa/Projects/ai-chatbot/backend/core/security.py)
- [backend/models.py](/Users/tabularasa/Projects/ai-chatbot/backend/models.py)

This document describes the real production flow as of April 6, 2026.

## High-level flow

1. A tenant generates a KYC signing secret in Dashboard -> Settings -> Widget API.
2. The tenant stores that secret on their own backend.
3. When they want to identify an end user, their backend creates a short-lived HMAC token.
4. Their integration calls `POST /widget/session/init` with:
   - `api_key`
   - optional `identity_token`
   - optional `locale`
5. Chat9 validates the token.
6. If valid, Chat9 either resumes an eligible identified chat for the same `user_id` or creates a new one.
7. Chat9 stores the validated user context in `chats.user_context`.
8. Chat9 also maintains an identified-user lifecycle row in `user_sessions`.
9. The response returns:
   - `session_id`
   - `mode` = `identified` or `anonymous`
10. Later `POST /widget/chat` calls reuse the returned `session_id`.

## Request and response

### `POST /widget/session/init`

Request body:

```json
{
  "api_key": "tenant_private_api_key",
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
- It happens only when the integration explicitly calls `POST /widget/session/init` and includes `identity_token`.

Important implementation detail:

- the current stock `embed.js` loader opens the hosted widget UI and passes only the public bot ID and browser locale
- it does **not** currently inject `api_key` or `identity_token`
- so KYC today is available for custom or advanced integrations that perform the session bootstrap themselves

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

- `generate_kyc_token(...)` in [backend/core/security.py](/Users/tabularasa/Projects/ai-chatbot/backend/core/security.py#L72)

## Payload fields

### Required

- `tenant_id`
  - must equal the tenant's public bot ID, for example `ch_abc123`
- `user_id`
  - any non-empty string
- `exp`
  - token expiration time, Unix timestamp in seconds
- `iat`
  - token issued-at time, Unix timestamp in seconds

### Optional

- `email`
- `name`
- `plan_tier`
- `audience_tag`
- `company`
- `locale`

The server validates `tenant_id`, `user_id`, `exp`, and the HMAC signature. Unknown extra fields are ignored by the `UserContext` model.

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
- `wrong_tenant`
- `missing_user_id`
- `bad_signature`

If validation fails, the widget flow does not hard-fail. Chat9 falls back to anonymous mode.

## `mode` meanings

### `identified`

Returned when:

- `identity_token` is present
- signature is valid
- tenant matches
- token is not expired
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

- `tenant_id`
- `exp`
- `iat`

For identified users, Chat9 also maintains a `user_sessions` row with:

- `client_id`
- `user_id`
- best-known identity fields (`email`, `name`, `plan_tier`, `audience_tag`)
- `session_started_at`
- optional `session_ended_at`
- `conversation_turns`

Only one active `user_sessions` row is allowed per `client_id + user_id`.

## Session continuity and resume

The widget now has two different continuity mechanisms:

### Anonymous users

- continuity is browser-local only
- the widget stores `session_id` in `localStorage`
- the same browser can continue the same chat for up to 24 hours
- another browser or device gets a new session

### Identified users

- resume is decided on the backend during `POST /widget/session/init`
- matching key: `client_id + user_id`
- Chat9 resumes the latest eligible chat when:
  - `ended_at is null`
  - the last chat activity is within 24 hours
- otherwise Chat9 creates a new chat and a new active `user_sessions` row

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

- `_user_context_prompt_line(...)` in [backend/chat/service.py](/Users/tabularasa/Projects/ai-chatbot/backend/chat/service.py#L573)

## What affects escalations

Escalation flows may read these fields from `user_context`:

- `user_id`
- `email`
- `name`
- `plan_tier`

This means identified sessions produce richer escalation tickets than anonymous sessions.

Reference:

- [backend/escalation/service.py](/Users/tabularasa/Projects/ai-chatbot/backend/escalation/service.py#L228)

## Secret management

Tenant-facing secret endpoints:

- `POST /clients/me/kyc/secret`
- `GET /clients/me/kyc/status`
- `POST /clients/me/kyc/rotate`

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
  "tenant_id": "ch_demo123",
  "email": "user@example.com",
  "plan_tier": "growth",
  "audience_tag": "b2b",
  "locale": "en-US",
  "exp": 1775399700,
  "iat": 1775399400
}
```

3. Tenant backend signs the payload and sends `POST /widget/session/init`.
4. Chat9 returns:

```json
{
  "session_id": "1c576fd8-cf10-4b58-a4e7-460ea0d19dbe",
  "mode": "identified"
}
```

5. The integration uses that `session_id` for subsequent `POST /widget/chat` calls.
