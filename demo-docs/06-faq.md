# Frequently Asked Questions

## General

**What is Chat9?**
Chat9 is an AI-powered chat widget platform. You upload your product documentation, and Chat9 creates a smart bot that answers your customers' questions based on that documentation.

**Do I need an OpenAI API key?**
Yes. Chat9 requires your own OpenAI API key to generate embeddings and answers. You connect it in the Dashboard → Settings.

**Is Chat9 free?**
Yes, Chat9 is currently free during early access. You only pay OpenAI directly for the AI usage (embeddings and chat).

**What languages does Chat9 support?**
Chat9 responds in the language the user writes in. Best results come when your documentation and user questions are close in language and terminology.

---

## Setup

**How long does it take to set up?**
Typically 5–10 minutes: create account, verify email, add OpenAI key, upload docs, embed widget.

**What document formats are supported?**
PDF, Markdown (.md), and Swagger/OpenAPI files (`.json`, `.yaml`, `.yml`).

Swagger/OpenAPI specs are indexed semantically: Chat9 extracts API operations and schema detail instead of embedding raw JSON/YAML text.

**Can I use a documentation website instead of uploading files?**
Yes. In the Knowledge hub you can add a same-domain documentation URL source, track crawl/index status, and refresh it later.

**How many documents can I upload?**
Up to 100 knowledge items per account. This shared capacity includes uploaded files and indexed URL-source pages.

**What if my document didn't embed correctly?**
Check the document status in the Dashboard. If it shows "Error", try deleting and re-uploading. If the issue persists, contact support.

---

## Widget

**Where does the chat button appear?**
In the bottom-right corner of your website page.

**Can I embed the widget on multiple pages?**
Yes. Add the embed code to any page you want the widget to appear on, or add it to your global layout/template to show on all pages.

**Will the widget work on any website?**
Yes. The widget is plain JavaScript and works on any website — React, Vue, plain HTML, WordPress, Webflow, etc.

**Can users have multi-turn conversations?**
Yes. The widget maintains session continuity — users can ask follow-up questions and the bot will understand the context.

**Can the bot ask follow-up questions?**
Yes. If one critical detail is missing or the request is ambiguous, Chat9 may ask one short, specific clarification question instead of guessing. When possible, the widget can show answer options as quick replies.

---

## Quality and accuracy

**What if the bot doesn't know the answer?**
If the answer is not in your documentation, the bot will either say it doesn't have enough information, ask one targeted clarification question, or suggest contacting support depending on what is safest and most useful for that turn.

**How can I improve answer quality?**
- Upload more complete documentation.
- Add or refresh URL sources if your docs live on a website.
- Use the 👍/👎 feedback in Chat Logs to identify weak answers.
- Use the "Review bad answers" page to write ideal answers for training.
- Use the Debug page to see which document chunks are being retrieved.

**Can the bot answer questions outside my documentation?**
No. The bot only answers based on the documents you upload. It will not use general AI knowledge.

---

## Identified sessions and the SDK

**What is an identified session?**
By default the chat widget is anonymous. An identified session lets your server attach a verified user identity (user id, email, plan, etc.) to a conversation. This information appears in Chat Logs and can be used for routing and personalisation.

**Do I have to use identified sessions?**
No. The basic anonymous embed works without any SDK or server-side code. Identified sessions are optional and intended for teams that want to link chat conversations to their own users.

**Is the token encrypted?**
No. The payload is Base64-encoded, not encrypted — it can be decoded by anyone who intercepts it. Do not include passwords, payment data, or sensitive personal information. The token is signed with HMAC-SHA256, so it cannot be tampered with without knowing your signing secret.

**Where do I get the signing secret?**
In the Dashboard, go to **Settings → Widget API** and click **Generate secret**. Store the value in a server-side environment variable. Never put it in client-side JavaScript.

**What languages does the SDK support?**
Phase 1 ships Python. A PyPI release is coming soon — until then install directly from GitHub:
```bash
pip install git+https://github.com/tabularasa31/chat9-sdks.git#subdirectory=python
```
Node.js, Go, and PHP packages follow the same interface and are in progress. Until they are released you can generate the token manually — see the [token specification](https://github.com/tabularasa31/chat9-sdks/blob/main/docs/token-spec.md).

**What exception does the Python SDK raise?**
`chat9.Chat9Error`. It has a `.code` attribute with a machine-readable error code (e.g. `MISSING_USER_ID`, `INVALID_FIELD`) and a `.message` attribute with a human-readable description.

**What happens if the token is expired or tampered with?**
The widget falls back to an anonymous session. Your users still see the chat — they just won't have an identified identity attached to the conversation.

---

## Security and privacy

**Is my data safe?**
Yes. Your documents and conversations are stored securely and are completely isolated from other customers' data.

**Is my OpenAI API key secure?**
Yes. Your API key is encrypted at rest.

**Can other customers see my data?**
No. Chat9 enforces strict client isolation — each customer can only access their own data.

---

## Contact

**How do I contact support?**
Email: support@getchat9.live
