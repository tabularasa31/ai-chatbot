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

## Step 3: Upload your documents

1. Go to Dashboard → Documents.
2. Click "Upload document".
3. Supported formats: PDF, Markdown (.md), plain text (.txt), Swagger/OpenAPI (.json, .yaml).
4. After upload, click "Create embeddings" to process the document.
5. Wait until the status shows "Embedded" — this may take a few seconds to a minute depending on document size.

## Step 4: Get your embed code

1. Go to Dashboard → Settings (or the main dashboard page).
2. Copy your API key.
3. Add the following code to your website:

```html
<div id="ai-chat-widget" data-api-key="YOUR_API_KEY"></div>
<script src="https://ai-chatbot-production-6531.up.railway.app/embed.js"></script>
```

Replace `YOUR_API_KEY` with your actual API key from the dashboard.

## Step 5: Test it

Open your website and click the chat button in the bottom-right corner. Ask a question about your product — the bot will answer based on your uploaded documents.
