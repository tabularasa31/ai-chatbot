# Embedding the Chat Widget

## Basic embed

Add these two lines anywhere in your HTML, before the closing `</body>` tag:

```html
<div id="ai-chat-widget" data-api-key="YOUR_API_KEY"></div>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js"></script>
```

Replace `YOUR_API_KEY` with your API key from the Dashboard.

## What the widget does

- A floating blue chat button appears in the bottom-right corner of your page.
- When clicked, a chat window opens (380×500px).
- Users can type questions and receive answers from your documentation.
- The widget maintains session continuity — follow-up questions work correctly.
- The widget works in any language.

## Where to get your API key

1. Log in to your Dashboard at https://getchat9.live.
2. Your API key is displayed on the main Dashboard page.

## CORS

The widget can be embedded on any domain — CORS is configured to allow all origins (`*`).

## Session continuity

Each user gets a `session_id` stored in their browser. This allows the bot to maintain context across multiple questions in the same conversation.

## Security

Your API key is used to identify which bot (and which documents) to use for answering questions. Keep it safe — do not share it publicly in open-source repositories. However, it is safe to include it in your website's HTML, as it only grants access to the chat endpoint.
