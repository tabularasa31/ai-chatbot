# CURSOR PROMPT: [FI-007] Per-Client System Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

⚠️ **PREREQUISITE: FI-031 (org_config) must be merged first. Confirm `org_config` column exists in `clients` table before starting.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-007-system-prompt
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add `system_prompt` field to `Client`
- `backend/clients/service.py` — add get/update system_prompt logic
- `backend/clients/routes.py` — add GET/PUT `/clients/me/system-prompt` endpoints
- `backend/chat/service.py` — use client's system prompt in `build_rag_prompt()`
- `backend/migrations/versions/` — new Alembic migration
- `frontend/app/dashboard/settings/page.tsx` — add system prompt textarea
- `frontend/lib/api.ts` — add system prompt API calls
- `tests/` — add tests for new endpoints

**Do NOT touch:**
- `backend/auth/`
- `backend/documents/`
- `backend/search/`
- `backend/embeddings/`
- `backend/widget/`
- Any existing tests

---

## CONTEXT

Currently `build_rag_prompt()` uses a hardcoded system prompt for all clients:
```python
system_rules = (
    "You are a technical support agent for the client's product (SaaS, API, docs).\n"
    "Rules:\n"
    "- Answer based ONLY on the provided context..."
    ...
)
```

This means every client's bot sounds the same and can't be customized. FI-007 allows each client to define their own system prompt — who the bot is, how it behaves, what to say when it doesn't know the answer.

**Note:** The client-defined prompt replaces the custom part only. Core rules (answer from context only, answer in question's language) are always injected on top.

---

## WHAT TO DO

### 1. Add `system_prompt` to Client model (`backend/models.py`)

Add after `org_config` column:

```python
system_prompt = Column(
    Text,
    nullable=True,
    default=None,
)
```

### 2. Alembic migration

```python
def upgrade():
    op.add_column(
        "clients",
        sa.Column("system_prompt", sa.Text(), nullable=True),
    )

def downgrade():
    op.drop_column("clients", "system_prompt")
```

### 3. Backend service (`backend/clients/service.py`)

Add two functions:

```python
def get_system_prompt(client_id: uuid.UUID, db: Session) -> str | None:
    """Return custom system_prompt for given client, or None if not set."""

def update_system_prompt(client_id: uuid.UUID, prompt: str | None, db: Session) -> str | None:
    """Update system_prompt. Pass None to reset to default."""
```

### 4. API routes (`backend/clients/routes.py`)

```
GET  /clients/me/system-prompt   → returns {"system_prompt": str | null}
PUT  /clients/me/system-prompt   → accepts {"system_prompt": str | null}, returns updated
```

Pydantic schema for PUT:
```python
class SystemPromptUpdate(BaseModel):
    system_prompt: Optional[str] = None
```

### 5. Update `build_rag_prompt()` (`backend/chat/service.py`)

Update signature:
```python
def build_rag_prompt(
    question: str,
    context_chunks: list[str],
    org_config: dict | None = None,
    system_prompt: str | None = None,
) -> str:
```

**Prompt structure (in this order):**

```
[CORE RULES — always injected, not overridable]
You are a support assistant. Answer ONLY from the provided context.
Answer in the SAME LANGUAGE as the question.
Do not invent information not present in the context.
If the answer is not in the context, say so and direct the user to contact support.

[CLIENT SYSTEM PROMPT — if set, insert here]
<client's custom system_prompt text>

[ORG INFO BLOCK — if org_config has values]
Company info:
- Company: ...
- Support email: ...
...

[CONTEXT]
<retrieved chunks>

Question: <question>

Answer:
```

If `system_prompt` is None or empty, skip that section. The core rules always apply.

Update all callers of `build_rag_prompt()` in `chat/service.py` to load `client.system_prompt` and pass it in.

### 6. Frontend: Settings page (`frontend/app/dashboard/settings/page.tsx`)

Add a "Bot Personality" section with:
- Textarea for custom system prompt (large, ~8 rows)
- Placeholder text showing the 5 key elements to include:
  ```
  Example: "You are Alex, a friendly support agent for Acme Corp.
  When you don't know the answer, say: 'I'm not sure, please email support@acme.com.'
  Keep your answers concise and professional."
  ```
- Save button → PUT `/clients/me/system-prompt`
- "Reset to default" button → PUT with `system_prompt: null`
- Show current prompt on mount via GET

### 7. API client (`frontend/lib/api.ts`)

```typescript
clients: {
  // existing methods...
  getSystemPrompt: () => apiRequest('GET', '/clients/me/system-prompt'),
  updateSystemPrompt: (prompt: string | null) => apiRequest('PUT', '/clients/me/system-prompt', { system_prompt: prompt }),
}
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New tests for GET/PUT `/clients/me/system-prompt`
- [ ] Test that `build_rag_prompt()` with custom system_prompt includes it between core rules and context
- [ ] Test that `build_rag_prompt()` without system_prompt (None) works as before
- [ ] Manual test: set custom prompt in dashboard → ask bot a question → bot uses the custom persona

---

## GIT PUSH

```bash
git add backend/models.py backend/clients/ backend/chat/service.py \
        backend/migrations/versions/ frontend/app/dashboard/settings/ \
        frontend/lib/api.ts tests/
git commit -m "feat: add per-client system prompt to Client model and RAG pipeline (FI-007)"
git push origin feature/fi-007-system-prompt
```

---

## NOTES

- Core rules (answer from context, match language) are ALWAYS injected — client can't override them
- Client prompt goes between core rules and context — this is intentional (rules first, then persona)
- `system_prompt = null` → bot uses default behaviour (core rules only)
- Don't expose prompt versioning in this PR — keep it simple for now

---

## PR DESCRIPTION

```markdown
## Summary
Adds per-client `system_prompt` to the `Client` model, allowing each client to customize how their bot presents itself. Core safety rules (answer from context, match language) are always enforced on top.

## Changes
- `backend/models.py` — add `system_prompt` Text column to `Client`
- `backend/migrations/versions/XXX` — Alembic migration
- `backend/clients/service.py` — get/update system_prompt
- `backend/clients/routes.py` — GET/PUT `/clients/me/system-prompt`
- `backend/chat/service.py` — inject custom prompt into `build_rag_prompt()`
- `frontend/app/dashboard/settings/page.tsx` — system prompt textarea + save/reset
- `frontend/lib/api.ts` — system prompt API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: set custom prompt → verify bot persona changes

## Notes
Requires FI-031 (org_config) to be merged first.
```
