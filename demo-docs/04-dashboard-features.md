# Dashboard Features

## Documents

Upload and manage your knowledge base documents.

- **Supported formats:** PDF, Markdown, plain text, Swagger/OpenAPI JSON/YAML.
- **Maximum documents:** 20 per account.
- **Maximum file size:** 50 MB per document.
- **Embedding:** After uploading, the document is processed automatically for AI search.
- **Status badges:** Each document shows its status: Uploaded, Embedded, or Error.
- **Delete:** Remove a document at any time. This also removes its embeddings.

## Chat Logs

View all conversations your users have had with your bot.

- **Inbox layout:** Sessions list on the left, full conversation on the right.
- **Session details:** Last question, last answer preview, last activity time.
- **Message view:** User messages appear on the right (blue), bot answers on the left (gray).
- **Feedback:** Click 👍 or 👎 on any bot answer to rate its quality.
- **Ideal answer:** For bad answers, you can write the correct answer for future improvement.

## Review Bad Answers

A dedicated page showing all answers marked with 👎.

- Review each bad answer with the original question.
- See which document chunks were retrieved (retrieval debug).
- Write or edit the ideal answer.
- Use "Open in Logs" to see the full conversation context.

## Debug

Test your bot directly in the dashboard.

- Ask a question and see the full retrieval debug:
  - Which mode was used (vector / keyword / hybrid / none — on production DB with Postgres, retrieval is typically **hybrid**).
  - Which document chunks were retrieved.
  - Scores per chunk (cosine on SQLite-style paths; RRF fusion scores on PostgreSQL hybrid).

## Settings

- **OpenAI API key:** Connect your own key. Required for embeddings and chat.
- **API key:** Your widget API key for embedding the bot on your website.
- **Embed code:** Copy-paste snippet ready to use.
