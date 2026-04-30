---
title: Widget API reference
description: Public widget endpoints — what to call, what comes back, how it's rate-limited.
---

The chat widget on your site talks to Chat9 over a small set of public
HTTP endpoints. You don't normally call them yourself — `embed.js`
handles everything — but they're documented here in case you need to
build a custom integration, debug behaviour, or sign identified
session tokens from your own backend.

> **Live spec:** the canonical machine-readable OpenAPI definition is
> served at **<https://api.getchat9.live/docs>** (interactive Swagger
> UI) and **<https://api.getchat9.live/openapi.json>** (raw JSON).
> When the two ever disagree, the live spec wins — this page is the
> human-friendly walk-through.

All public widget endpoints are **unauthenticated** in the
HTTP-headers sense: a request is identified by the bot's `public_id`
(the string in your `data-bot-id`, starts with `ch_`). Each endpoint
has its own per-IP rate limit listed below.

The base URL is `https://api.getchat9.live`. All paths in this page
are appended to that base.

## `POST /widget/session/init`

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

## `POST /widget/chat`

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
  session id (so you can store it), and a flag that's `true` when the
  bot decided to close the chat (e.g. after a manual escalation).

If something goes wrong server-side you may instead get one
`{"type":"error","code":...,"message":"..."}` event before the stream
closes.

**Errors before the stream starts:** `404` (bot not found), `422`
(invalid `session_id` or empty message).

**Rate limit:** 30 requests per minute per visitor IP.

## `GET /widget/history`

Reload the messages from an existing session — useful when the user
refreshes the page and you want to rehydrate the conversation.

**Query parameters:** `bot_id`, `session_id` (both required).

**Response:** JSON list of messages with `role`, `content`,
`created_at`.

**Rate limit:** 30 requests per minute per IP.

## `POST /widget/escalate`

Open a support ticket from the widget, e.g. when the user clicks
"Talk to a human". Returns the ticket number (`ESC-42` style) which
you can show as confirmation.

**Query parameters:** `bot_id`, `session_id` (both required).

**Request body:**

```json
{ "reason": "Optional free-form text from the user" }
```

**Rate limit:** 20 requests per minute per IP.

## `GET /embed.js`

The widget loader script. Embed it on your site with:

```html
<script
  src="https://<your-chat9-host>/embed.js"
  data-bot-id="ch_…">
</script>
```

You don't normally fetch this directly — the script tag does it.

## `GET /widget/config`

Returns widget configuration the loader uses (link-safety labels,
allowed embed domains, etc.). Takes `bot_id` as a query parameter.
You don't usually call it from your code.

## A note on chat answer models

The `/widget/chat` endpoint generates answers with `gpt-5-mini` and
runs lightweight guard / validation checks with `gpt-4o-mini` by
default. See [Pricing and limits](/docs/pricing-and-limits) for cost
guidance.

## Need help?

Email **support@getchat9.live** if you hit something this page
doesn't cover.
