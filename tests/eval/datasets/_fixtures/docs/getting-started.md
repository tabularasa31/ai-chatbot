---
title: Getting Started
description: From signup to an embedded widget in about 5–10 minutes.
---

## Step 1: Create an account

Go to https://getchat9.live and click "Start for free".

Sign up with your email address. You will receive a verification email — click the link to verify your account before proceeding.

## Step 2: Connect your OpenAI API key

Chat9 requires your own OpenAI API key to generate answers and embeddings.

1. Go to your Dashboard → Settings.
2. Enter your OpenAI API key in the "OpenAI API Key" field.
3. Save.

You can get an OpenAI API key at https://platform.openai.com/api-keys.

## Step 3: Add your knowledge

1. Go to Dashboard → Knowledge.
2. Either click "Upload document" or add a URL source.
3. Supported file formats: PDF, Markdown (`.md`, `.mdx`), Swagger/OpenAPI (`.json`, `.yaml`, `.yml`), Word (`.docx`, `.doc`), plain text (`.txt`). Maximum file size: 50 MB.
4. Swagger/OpenAPI files are processed semantically: Chat9 indexes API operations and schema detail instead of embedding raw JSON/YAML text.
5. URL sources crawl same-domain documentation pages in the background and show crawl/index status in the same Knowledge hub.
6. Wait until the document or source status returns to "Ready" — this may take a few seconds to a minute depending on size.

## Step 4: Get your embed code

1. Go to Dashboard → Settings (or the main dashboard page).
2. Click **Copy** on the embed snippet (it includes your bot's `public_id` in the `data-bot-id` attribute).
3. Paste the code before the closing `</body>` tag on your site.

Example shape (the Dashboard fills in your real values):

```html
<script
  src="https://widget.getchat9.live/widget.js"
  data-bot-id="YOUR_BOT_PUBLIC_ID">
</script>
```

## Step 5: Test it

Open your website and click the chat button in the bottom-right corner. Ask a question about your product — the bot will answer based on your uploaded documents.

What to expect:

- if the answer is clear from your docs, the bot replies immediately
- if one critical detail is missing, the bot may ask one short, specific follow-up question (embedded in the bot's text reply, not as separate buttons)

## Step 6: Review gaps and recurring questions

Once you have a few real or test conversations, open **Dashboard → Gap Analyzer**.

There you can review:

- docs-side topics that look under-covered in your knowledge base
- repeated real-user question clusters that suggest missing documentation or confusing product areas
- draft content ideas your team can turn into docs, help-center pages, or FAQ updates
