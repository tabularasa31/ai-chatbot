# Dashboard Features

The app uses a **left sidebar** for navigation (main items, **SETTINGS**, and **Admin** for platform admins). The top bar shows the Chat9 brand, your email, and **Logout**.

## Dashboard (`/dashboard`)

- **API key** — your secret `X-Api-Key` for server-to-server API calls (`POST /chat`, etc.). Copy with one click.
- **Embed code** — HTML snippet with your **`public_id`** (`ch_…`) in the script URL. Copy the block; code areas use an inline **copy** icon.
- If the **OpenAI API key** is not set, an amber banner links to **Agents** (`/settings`) to configure it.

## Knowledge hub (`/knowledge`)

Formerly `/documents` — **that route is removed**; use **Knowledge**.

- **Supported formats:** PDF, Markdown, plain text, Swagger/OpenAPI JSON/YAML.
- **Limits:** e.g. max file size 50 MB (see product limits); embedding runs asynchronously after upload/trigger.
- **Status:** Documents move through `ready` → `embedding` → `ready` or `error`; the UI polls the API until embedding finishes.
- **Health:** After embedding, health indicators and re-check actions (see FI-032).
- **Delete:** Removes the document and its embeddings.
- **External sources:** Cards for future connectors (e.g. GitHub) plus a unified table of indexed sources.

## Agents (`/settings`)

- **OpenAI API key** — per-tenant key, encrypted at rest; required for embeddings, chat, and document health checks.
- Save, update, or remove the key from this page.

## Chat Logs (`/logs`)

View all conversations your users have had with your bot.

- **Inbox layout:** Sessions list on the left, full conversation on the right.
- **Session details:** Last question, last answer preview, last activity time.
- **Message view:** User messages and bot answers in a thread layout.
- **Feedback:** Click 👍 or 👎 on any bot answer to rate its quality.

## Review Bad Answers (`/review`)

A dedicated page showing answers marked with 👎.

- Review each bad answer with the original question.
- See which document chunks were retrieved (retrieval debug).
- Write or edit the ideal answer.
- Use "Open in Logs" to see the full conversation context.

## Debug (`/debug`)

Test your bot directly in the dashboard.

- Ask a question and see retrieval debug (mode, chunks, scores on Postgres).
- **Answer** is shown in a code-style block with an inline **copy** icon.

## Response controls (`/settings/disclosure`)

Set the tenant-wide **response detail level** (Detailed / Standard / Corporate) for FI-DISC v1.

## Widget API (`/settings/widget`)

- Manage **KYC signing secret** (generate, rotate) for optional **identified** widget sessions.
- Server-side token example (Node.js) with copy-to-clipboard on the snippet block.

## Escalations (`/escalations`)

- L2 ticket inbox (FI-ESC): open/resolved, triggers, link to session.
- Resolve tickets from the UI.

## Admin (`/admin/metrics`)

- Visible only to users with **`is_admin`**. Platform-wide usage metrics.
