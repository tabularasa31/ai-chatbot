# Embedding the Chat Widget

## Basic embed

Copy the snippet from the Dashboard and paste it before the closing `</body>` tag. It loads a small script from your API host and points the iframe UI at the Chat9 app.

Example (placeholders — the Dashboard fills in your real `clientId` and may adjust URLs):

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js?clientId=ch_YOUR_PUBLIC_ID"></script>
```

`clientId` is your client **public id** (`ch_…`), not the secret API key.

## What the widget does

- A floating chat iframe appears in the bottom-right corner of your page (about 400×600px).
- Users type questions and receive answers from your documentation.
- Session continuity works across messages in the same visit.
- The widget works in any language.

## Where to get the embed code

1. Log in to your Dashboard at https://getchat9.live.
2. Copy the embed block from the **Dashboard** home (`/dashboard`).

## CORS

The widget is designed to work on arbitrary sites because the public loader injects an iframe hosted by Chat9. You do not need to expose your private API key in page HTML.

## Session continuity

Each user gets a `session_id` stored in their browser. This allows the bot to maintain context across multiple questions in the same conversation.

## Security

The **public id** (`ch_…`) in the script URL identifies which bot to load; it is intended to appear in page HTML. Keep your **API key** (for authenticated dashboard/API use) secret — do not commit it to public repos.

---

## Identified sessions (optional)

By default the widget is anonymous — Chat9 does not know who is talking. **Identified sessions** let you pass verified user information (user id, email, plan, etc.) to the widget so it appears in conversation logs and can be used for routing or personalisation.

### How it works

1. Your server generates a short-lived signed token using your **signing secret**.
2. The token is passed to the widget on page load.
3. Chat9 verifies the token during `POST /widget/session/init` and attaches the user identity to the session.

The token is HMAC-signed — any tampering is detected. The payload is **encoded, not encrypted**, so do not put passwords, payment data, or other secrets inside it.

### Step 1 — Get a signing secret

Go to **Dashboard → Settings → Widget API** and click **Generate secret**. Copy the value and store it in your server environment (e.g. `CHAT9_SIGNING_SECRET`). Never expose it in client-side code or commit it to a repository.

### Step 2 — Generate a token server-side

Install the SDK for your backend language:

**Python**
```bash
# PyPI release coming soon — install directly from GitHub in the meantime:
pip install git+https://github.com/tabularasa31/chat9-sdks.git#subdirectory=python
```

```python
from chat9 import generateToken, Chat9Error

try:
    token = generateToken({
        "secret": os.environ["CHAT9_SIGNING_SECRET"],
        "user": {
            "user_id": current_user.id,          # required, non-empty string
            "email":   current_user.email,        # optional
            "locale":  "en-US",                  # optional, e.g. "en", "de-AT"
            "timezone": "Europe/Berlin",          # optional, IANA tz name
            "custom_attrs": {                     # optional, up to 20 keys
                "plan": "growth",
            },
        },
        "options": {
            "ttl": 300,   # seconds until expiry, 60–3600, default 300
        },
    })
except Chat9Error as e:
    # e.code is one of: MISSING_SECRET, MISSING_USER_ID, INVALID_FIELD,
    #                   INVALID_TTL, CUSTOM_ATTRS_OVERFLOW
    logger.error("Token generation failed: %s – %s", e.code, e.message)
    raise
```

**Node.js** *(coming in Phase 1)*
```js
const { generateToken } = require('@chat9/sdk');

const token = generateToken({
  secret: process.env.CHAT9_SIGNING_SECRET,
  user: { user_id: req.user.id, email: req.user.email },
  options: { ttl: 300 },
});
```

**Go / PHP** — coming in Phase 1. Until then, use the Python SDK or generate the token manually (see the [token spec](https://github.com/tabularasa31/chat9-sdks/blob/main/docs/token-spec.md)).

### Step 3 — Pass the token to the widget

Render the token into your page alongside the embed snippet:

```html
<script>
  window.Chat9Config = {
    widgetUrl: "https://getchat9.live",
    userToken: "{{ chat9_token }}",   <!-- server-rendered token -->
  };
</script>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js?clientId=ch_YOUR_PUBLIC_ID"></script>
```

The widget picks up `userToken` automatically. Do not store the token in `localStorage` or pass it via URL parameters.

### Token field reference

| Field | Required | Type | Constraints |
|---|---|---|---|
| `user_id` | ✅ | string | non-empty, non-whitespace |
| `email` | — | string | basic format, e.g. `user@example.com` |
| `locale` | — | string | BCP 47, e.g. `en`, `en-US`, `zh-Hant-TW` |
| `timezone` | — | string | IANA name, e.g. `Europe/Berlin` |
| `custom_attrs` | — | object | max 20 keys, each value ≤ 256 chars |

### Error codes

| Code | When |
|---|---|
| `MISSING_SECRET` | Signing secret not provided |
| `MISSING_USER_ID` | `user.user_id` missing or blank |
| `INVALID_FIELD` | Field value does not match the required format |
| `INVALID_TTL` | `ttl` is outside the 60–3600 range |
| `CUSTOM_ATTRS_OVERFLOW` | `custom_attrs` has more than 20 keys |

### Security checklist

- ✅ Generate the token **on your server**, never in browser JavaScript
- ✅ Store the signing secret in an environment variable, not in source code
- ✅ Use a short TTL (300 s is a reasonable default)
- ✅ Rotate the secret from the Dashboard if it is ever exposed
- ❌ Do not put passwords, card numbers, or PII beyond `email` in the token payload
