# FI-033: Upgrade to gpt-4o-mini (from gpt-3.5-turbo)

## Setup Instructions (Before You Start)

### 1. Open the Project
```bash
# Clone or open the repository
git clone https://github.com/tabularasa31/ai-chatbot.git
cd ai-chatbot

# Create a working branch
git checkout -b fi-033-gpt4o-mini
```

### 2. Project Structure (Reference)
```
ai-chatbot/
├── backend/
│   ├── config/
│   │   └── settings.py          ← Update OPENAI_MODEL here
│   ├── chat/
│   │   ├── service.py           ← Update model names in API calls
│   │   ├── prompts.py           ← Optimize RAG prompt
│   │   └── routes.py            ← Chat endpoint (check only)
│   └── utils/
│       └── token_counter.py     ← Verify tokenization works
├── frontend/                     ← NO CHANGES
├── docs/
│   └── (various specs)
├── cursor_prompts/              ← You are here
└── README.md                     ← Update docs section
```

### 3. Environment Check
Make sure you have:
- Python 3.11+ with venv activated
- `requirements.txt` installed (`pip install -r requirements.txt`)
- OpenAI API key available
- PostgreSQL running (if needed for local testing)

### 4. Files to Edit
Copy these paths into your editor to jump directly:
1. `backend/config/settings.py` — Find `OPENAI_MODEL`
2. `backend/chat/service.py` — Find `openai.ChatCompletion.create(model=...)`
3. `backend/chat/prompts.py` — Find RAG system prompt
4. `README.md` — Find "RAG pipeline" section

### 5. Before You Code
- [ ] Read this entire prompt
- [ ] Open the 4 files listed above (just to review)
- [ ] Check git status: `git status` (should be clean)
- [ ] Create branch: `git checkout -b fi-033-gpt4o-mini`

---

## Objective
Replace gpt-3.5-turbo with gpt-4o-mini across the Chat9 RAG pipeline.
Better quality, same/lower cost, same API compatibility.

## Context
- **Current model:** gpt-3.5-turbo (OpenAI)
- **New model:** gpt-4o-mini (OpenAI)
- **Cost:** Similar (~0.15 USD per 1M input tokens, 0.6 per 1M output)
- **Quality:** Significantly better for reasoning, nuance, multilingual
- **Compatibility:** Same API, just model name change in most places

## Code Freeze Rules
⚠️ CRITICAL: Only modify files directly related to FI-033
- ✅ Do change: RAG prompts, model names, LLM client configs
- ❌ Do NOT change: Embeddings logic, vector DB, auth, email, UI, unrelated endpoints
- ❌ Do NOT: Refactor working code (e.g., token counting, response formatting)
- ❌ Do NOT: Add new features (even if related) — stick to model swap only

## Locations to Update

### 1. Backend Configuration
File: `backend/config/settings.py`
- Find: `OPENAI_MODEL = "gpt-3.5-turbo"`
- Change to: `OPENAI_MODEL = "gpt-4o-mini"`
- Keep everything else as-is

### 2. RAG Chat Endpoint
File: `backend/chat/service.py` (or similar)
- Find all calls to `openai.ChatCompletion.create()` with `model="gpt-3.5-turbo"`
- Change to `model="gpt-4o-mini"`
- ✅ Keep: token counting logic (Chat.tokens_used), context window logic, message formatting
- ✅ Keep: Feedback loop (👍/👎 storage), debug mode, all response parsing

### 3. System Prompt / RAG Prompt
File: `backend/chat/prompts.py` or embedded in service.py
- Review current system prompt
- Optimize for gpt-4o-mini:
  - Can be more concise (better reasoning = shorter prompts work)
  - Can use more sophisticated instructions (nested logic OK)
  - Keep existing instructions about:
    * Responding in user's language
    * Citing documentation chunks
    * Being helpful for support context
- Test the new prompt against existing test cases

### 4. Token Counting
File: `backend/utils/token_counter.py` or `backend/chat/service.py`
- ⚠️ DO NOT CHANGE the token counting logic
- gpt-4o-mini uses same tokenizer as gpt-4, so counts should be accurate
- Verify: `tiktoken.encoding_for_model("gpt-4o-mini")` still works
- Test: Count tokens for a sample message, verify it's reasonable

### 5. Context Window & Rate Limiting
Files: Look for hardcoded context window sizes
- gpt-3.5-turbo: 4,096 tokens max (or 16k variant)
- gpt-4o-mini: 128,000 tokens max
- ✅ Keep existing logic (don't assume larger window = include more docs)
- ✅ Keep rate limiting (same limits apply)

### 6. README & Docs
File: `README.md`
- Update: "RAG pipeline — OpenAI embeddings (`text-embedding-3-small`) + **gpt-4o-mini**"
- Was: "...+ gpt-3.5-turbo"

---

## Testing Checklist
Before committing:

- [ ] Backend starts without errors
- [ ] Can create a new session + send a test message
- [ ] Response is received and formatted correctly
- [ ] Tokens are counted and stored in DB
- [ ] Feedback loop (👍/👎) still works
- [ ] Debug mode still shows retrieved chunks
- [ ] Sample message: "Hello" → response in correct language
- [ ] Sample multilingual: "¿Hola?" → response in Spanish
- [ ] Token count is reasonable (not 0, not inflated)

---

## What NOT To Do
❌ Don't refactor the RAG pipeline
❌ Don't change embeddings model
❌ Don't modify authentication/email flow
❌ Don't add new error handling (unless absolutely needed for this change)
❌ Don't update frontend (no changes needed)
❌ Don't change token tracking logic
❌ Don't touch database schema

---

## Git Workflow
```bash
git checkout -b fi-033-gpt4o-mini
# Make changes (only files listed above)
git add .
git commit -m "feat: upgrade to gpt-4o-mini from gpt-3.5-turbo (FI-033)"
git push origin fi-033-gpt4o-mini
# Create PR, merge after testing
```

---

## Expected Outcome
- Same API behavior, better response quality
- Slightly faster responses (gpt-4o-mini is optimized)
- Cost roughly same or slightly lower
- Better support for nuanced queries, non-English languages

## Success Metrics
- ✅ Backend deployed
- ✅ No errors in logs
- ✅ Chat works end-to-end
- ✅ Token counting accurate
- ✅ Feedback loop functional
