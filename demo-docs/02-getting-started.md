# Getting Started with Chat9

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
3. Supported file formats: PDF, Markdown (.md), Swagger/OpenAPI (`.json`, `.yaml`, `.yml`). Maximum file size: 50 MB.
4. Swagger/OpenAPI files are processed semantically: Chat9 indexes API operations and schema detail instead of embedding raw JSON/YAML text.
4. URL sources crawl same-domain documentation pages in the background and show crawl/index status in the same Knowledge hub.
5. Wait until the document or source status returns to "Ready" — this may take a few seconds to a minute depending on size.

## Step 4: Get your embed code

1. Go to Dashboard → Settings (or the main dashboard page).
2. Click **Copy** on the embed snippet (it includes your `public_id`, e.g. `ch_…`).
3. Paste the code before the closing `</body>` tag on your site.

Example shape (use the Dashboard copy — URLs and your public bot ID are filled in for you; the script param remains `clientId` for compatibility):

```html
<script>window.Chat9Config={widgetUrl:"https://getchat9.live"};</script>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js?clientId=ch_YOUR_PUBLIC_ID"></script>
```

## Step 5: Test it

Open your website and click the chat button in the bottom-right corner. Ask a question about your product — the bot will answer based on your uploaded documents.
