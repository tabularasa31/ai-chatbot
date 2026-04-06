# Embedding the Chat Widget

## Basic embed

Copy the snippet from the Dashboard and paste it before the closing `</body>` tag. It loads a small script from your API host and points the iframe UI at the Chat9 app.

Example (placeholders — the Dashboard fills in your real public bot ID and may adjust URLs; the script param remains `clientId` for compatibility):

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js?clientId=ch_YOUR_PUBLIC_ID"></script>
```

`clientId` is the legacy embed parameter that currently carries your public bot ID (`ch_…`), not the secret API key.

## What the widget does

- A floating chat iframe appears in the bottom-right corner of your page (about 400×600px).
- Users type questions and receive answers from your documentation.
- Session continuity works across messages in the same visit.
- The widget works in any language.

## Where to get the embed code

1. Log in to your Dashboard at https://getchat9.live.
2. Copy the embed block from the **Dashboard** home (`/dashboard`). The widget loads your public bot ID (`public_id`, `ch_…`); in the legacy embed snippet that value currently appears as **`clientId`**.

## CORS

The widget is designed to work on arbitrary sites because the public loader injects an iframe hosted by Chat9. You do not need to expose your private API key in page HTML.

## Session continuity

The widget keeps a `session_id` so the bot can maintain context across follow-up questions.

Current behavior:

- anonymous sessions are stored in the browser and can continue in the same browser for up to 24 hours
- identified sessions can also be resumed by Chat9 on `POST /widget/session/init` for the same `user_id` if the previous chat is still open and was active within 24 hours
- closed chats are not resumed

If a chat is explicitly closed by support/escalation flow, the widget shows `Start new chat`. The old transcript stays visible, the widget marks the next section as a new conversation, and the next message starts a fresh session below the old history.

## Security

The public bot ID (`ch_…`) in the script URL identifies which bot to load; it is intended to appear in page HTML. Keep your **API key** (for authenticated dashboard/API use) secret — do not commit it to public repos.

---

## Identified sessions (optional)

By default the standard widget embed is anonymous. Chat9 knows which bot should answer, but does not know who the end user is.

**Identified sessions** are an advanced integration path that lets you attach verified user context to a widget session.

### How it works

1. In **Dashboard -> Settings -> Widget API**, generate a signing secret.
2. Store that secret on your server.
3. Your server generates a short-lived signed `identity_token`.
4. Your integration calls `POST /widget/session/init` with:
   - your private `api_key`
   - optional `identity_token`
   - optional `locale`
5. Chat9 validates the token and returns:
   - `session_id`
   - `mode` = `identified` or `anonymous`
6. Reuse that `session_id` in later `POST /widget/chat` requests.

If the same identified user returns later:

- Chat9 may return the same `session_id` again if the previous identified chat is still open and recent
- otherwise Chat9 returns a new `session_id`

Important: the current stock `embed.js` snippet does not automatically send `api_key` or `identity_token`. If you need identified sessions today, use a custom integration or custom bootstrap flow around the public widget APIs.

The token is HMAC-signed. Any tampering is detected. The payload is encoded, not encrypted, so do not put passwords, card data, or other secrets inside it.

### Step 1 — Get a signing secret

Go to **Dashboard → Settings → Widget API** and click **Generate secret**. Copy the value and store it in your server environment (e.g. `CHAT9_SIGNING_SECRET`). Never expose it in client-side code or commit it to a repository.

### Step 2 — Generate a token server-side

Use the same signing approach as Chat9's backend.

```js
const crypto = require("crypto");

function makeWidgetIdentityToken({
  secretHex,
  botPublicId,
  userId,
  extras = {},
  ttlSeconds = 300,
}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    user_id: userId,
    tenant_id: botPublicId,
    exp: now + ttlSeconds,
    iat: now,
    ...extras,
  };

  const sorted = {};
  for (const key of Object.keys(payload).sort()) {
    sorted[key] = payload[key];
  }

  const json = JSON.stringify(sorted);
  const b64 = Buffer.from(json, "utf8")
    .toString("base64")
    .replace(/[+]/g, "-")
    .replace(/[/]/g, "_")
    .replace(/=+$/, "");

  const sig = crypto
    .createHmac("sha256", Buffer.from(secretHex, "utf8"))
    .update(b64)
    .digest("hex");

  return `${b64}.${sig}`;
}
```

Required payload fields:

- `tenant_id` — your bot public ID, for example `ch_...`
- `user_id` — any non-empty string
- `exp` — expiry time, Unix timestamp in seconds
- `iat` — issued-at time, Unix timestamp in seconds

Optional payload fields currently supported by Chat9:

- `email`
- `name`
- `plan_tier`
- `audience_tag`
- `company`
- `locale`

Example payload:

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

### Step 3 — Initialize the session

Call Chat9 before sending chat turns:

```bash
curl -X POST "https://YOUR_API_HOST/widget/session/init" \
  -H "Content-Type: application/json" \
  -d '{
    "api_key": "YOUR_PRIVATE_API_KEY",
    "identity_token": "YOUR_SIGNED_TOKEN",
    "locale": "en-US"
  }'
```

Successful response:

```json
{
  "session_id": "1c576fd8-cf10-4b58-a4e7-460ea0d19dbe",
  "mode": "identified"
}
```

If the token is missing or invalid, Chat9 still returns a session but with:

```json
{
  "session_id": "1c576fd8-cf10-4b58-a4e7-460ea0d19dbe",
  "mode": "anonymous"
}
```

### Step 4 — Send chat messages with the returned session

Use the `session_id` from `session/init` in later chat requests:

```bash
curl -X POST "https://YOUR_API_HOST/widget/chat?client_id=ch_YOUR_PUBLIC_ID&session_id=1c576fd8-cf10-4b58-a4e7-460ea0d19dbe&message=Hello"
```

### Token field reference

| Field | Required | Type | Constraints |
|---|---|---|---|
| `tenant_id` | ✅ | string | must equal your bot public ID (`ch_...`) |
| `user_id` | ✅ | string | non-empty, non-whitespace |
| `exp` | ✅ | integer | Unix timestamp in seconds |
| `iat` | ✅ | integer | Unix timestamp in seconds |
| `email` | — | string | optional |
| `name` | — | string | optional |
| `plan_tier` | — | string | optional |
| `audience_tag` | — | string | optional free-form segment label |
| `company` | — | string | optional |
| `locale` | — | string | optional, e.g. `en`, `en-US`, `ru-RU` |

### What `mode` means

| `mode` | Meaning |
|---|---|
| `identified` | The token was valid and Chat9 attached the user context to the session |
| `anonymous` | No token was provided, or validation failed, so the widget continues without KYC context |

### What Chat9 uses from KYC

Stored in chat context:

- `user_id`
- `email`
- `name`
- `plan_tier`
- `audience_tag`
- `company`
- `locale`

Used in the LLM prompt:

- `plan_tier`
- `locale`
- `audience_tag`

Used for richer escalation metadata:

- `user_id`
- `email`
- `name`
- `plan_tier`

### Security checklist

- ✅ Generate the token **on your server**, never in browser JavaScript
- ✅ Store the signing secret in an environment variable, not in source code
- ✅ Use a short TTL (300 s is a reasonable default)
- ✅ Rotate the secret from the Dashboard if it is ever exposed
- ✅ Treat invalid tokens as a fallback to anonymous mode, not as a widget outage
- ❌ Do not put passwords, card numbers, or other secrets in the token payload
- ❌ Do not assume the stock `embed.js` snippet enables KYC by itself
