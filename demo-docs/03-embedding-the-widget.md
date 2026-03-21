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
2. Copy the embed block from the dashboard (same as Settings / main page).

## CORS

The widget can be embedded on any domain — CORS is configured to allow all origins (`*`).

## Session continuity

Each user gets a `session_id` stored in their browser. This allows the bot to maintain context across multiple questions in the same conversation.

## Security

The **public id** (`ch_…`) in the script URL identifies which bot to load; it is intended to appear in page HTML. Keep your **API key** (for authenticated dashboard/API use) secret — do not commit it to public repos.
