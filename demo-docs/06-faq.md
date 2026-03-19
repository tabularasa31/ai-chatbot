# Frequently Asked Questions

## General

**What is Chat9?**
Chat9 is an AI-powered chat widget platform. You upload your product documentation, and Chat9 creates a smart bot that answers your customers' questions based on that documentation.

**Do I need an OpenAI API key?**
Yes. Chat9 requires your own OpenAI API key to generate embeddings and answers. You connect it in the Dashboard → Settings.

**Is Chat9 free?**
Yes, Chat9 is currently free during early access. You only pay OpenAI directly for the AI usage (embeddings and chat).

**What languages does Chat9 support?**
Chat9 responds in the language the user writes in. Best results when your documentation and user questions are in the same language. Cross-lingual support (e.g. English questions against Russian docs) is in progress.

---

## Setup

**How long does it take to set up?**
Typically 5–10 minutes: create account, verify email, add OpenAI key, upload docs, embed widget.

**What document formats are supported?**
PDF, Markdown (.md), plain text (.txt), and Swagger/OpenAPI files (.json, .yaml).

**How many documents can I upload?**
Up to 20 documents per account.

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

---

## Quality and accuracy

**What if the bot doesn't know the answer?**
If the answer is not in your documentation, the bot will say it doesn't have that information and suggest contacting support.

**How can I improve answer quality?**
- Upload more complete documentation.
- Use the 👍/👎 feedback in Chat Logs to identify weak answers.
- Use the "Review bad answers" page to write ideal answers for training.
- Use the Debug page to see which document chunks are being retrieved.

**Can the bot answer questions outside my documentation?**
No. The bot only answers based on the documents you upload. It will not use general AI knowledge.

---

## Security and privacy

**Is my data safe?**
Yes. Your documents and conversations are stored securely and are completely isolated from other customers' data.

**Is my OpenAI API key secure?**
Yes. Your API key is encrypted at rest.

**Can other customers see my data?**
No. Chat9 enforces strict tenant isolation — each customer can only access their own data.

---

## Contact

**How do I contact support?**
Email: support@getchat9.live
