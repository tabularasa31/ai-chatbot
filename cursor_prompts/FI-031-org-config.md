# CURSOR PROMPT: [FI-031] Org Config Layer

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-031-org-config
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse old branches
- Work on any branch other than the newly created feature branch

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add `org_config` field to `Client`
- `backend/clients/service.py` — add get/update org_config logic
- `backend/clients/routes.py` — add GET/PUT `/clients/me/org-config` endpoints
- `backend/chat/service.py` — inject org_config into `build_rag_prompt()`
- `backend/migrations/versions/` — new Alembic migration
- `frontend/app/dashboard/settings/page.tsx` — add org config form
- `frontend/lib/api.ts` — add org config API calls
- `tests/` — add tests for new endpoints

**Do NOT touch:**
- `backend/auth/`
- `backend/documents/`
- `backend/search/`
- `backend/embeddings/`
- `backend/widget/`
- Any existing tests (only add new ones)

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

The bot currently gives generic answers that don't know who the client is — no support email, no account manager name, no product name in fallback replies. This makes the bot look broken when users ask "how do I contact support?" or "what's your trial period?".

`org_config` is a JSON field on the `Client` model that stores structured metadata about the client's product. This metadata is injected into the RAG prompt before context, so the bot can always answer contact/policy questions correctly.

**This must be done BEFORE FI-007 (per-client system prompt)** — the system prompt needs org_config to be complete.

---

## WHAT TO DO

### 1. Add `org_config` to Client model (`backend/models.py`)

Add after `settings` column:

```python
org_config = Column(
    JSON,
    nullable=False,
    default=dict,
    server_default="{}",
)
```

`org_config` schema (document this in a comment):
```json
{
  "company_name": "Acme Corp",
  "support_email": "support@acme.com",
  "account_manager": "John Smith",
  "trial_period": "14 days",
  "support_url": "https://acme.com/help",
  "product_description": "Project management tool for teams"
}
```

All fields are optional strings. Any field can be absent.

### 2. Alembic migration

Create a new migration that adds `org_config` column to the `clients` table:

```python
def upgrade():
    op.add_column(
        "clients",
        sa.Column("org_config", sa.JSON(), nullable=False, server_default="{}"),
    )

def downgrade():
    op.drop_column("clients", "org_config")
```

### 3. Backend service (`backend/clients/service.py`)

Add two functions:

```python
def get_org_config(client_id: uuid.UUID, db: Session) -> dict:
    """Return org_config dict for given client."""

def update_org_config(client_id: uuid.UUID, config: dict, db: Session) -> dict:
    """Update org_config for given client. Merge (not replace) with existing values."""
```

Merge strategy for update: `existing_config.update(new_values)` — so partial updates work.

### 4. API routes (`backend/clients/routes.py`)

Add two endpoints (require auth — same pattern as existing client routes):

```
GET  /clients/me/org-config   → returns org_config dict
PUT  /clients/me/org-config   → accepts partial dict, merges, returns updated
```

Request body schema for PUT (Pydantic):
```python
class OrgConfigUpdate(BaseModel):
    company_name: Optional[str] = None
    support_email: Optional[str] = None
    account_manager: Optional[str] = None
    trial_period: Optional[str] = None
    support_url: Optional[str] = None
    product_description: Optional[str] = None
```

Only non-None fields are merged into the stored config.

### 5. Inject org_config into RAG prompt (`backend/chat/service.py`)

Update `build_rag_prompt()` signature to accept optional `org_config`:

```python
def build_rag_prompt(
    question: str,
    context_chunks: list[str],
    org_config: dict | None = None,
) -> str:
```

If `org_config` has any non-empty values, prepend an **org info block** before the context:

```
Company info:
- Company: Acme Corp
- Support email: support@acme.com
- Account manager: John Smith
- Trial period: 14 days
- Support URL: https://acme.com/help
```

Only include lines where the value is set (non-empty string).

Update all callers of `build_rag_prompt()` in `chat/service.py` to load client's `org_config` from DB and pass it in. The client is already available via `client_id` — just load `client.org_config`.

### 6. Frontend: Settings page (`frontend/app/dashboard/settings/page.tsx`)

Add an "About Your Product" section/card to the settings page (or create the page if it doesn't exist) with:
- Form fields: Company Name, Support Email, Account Manager, Trial Period, Support URL, Product Description
- Save button → PUT `/clients/me/org-config`
- Show success/error toast on save
- Load current values on mount via GET `/clients/me/org-config`

Use existing form/input components and styling patterns from the dashboard.

### 7. API client (`frontend/lib/api.ts`)

Add:
```typescript
clients: {
  // existing methods...
  getOrgConfig: () => apiRequest('GET', '/clients/me/org-config'),
  updateOrgConfig: (config: Partial<OrgConfig>) => apiRequest('PUT', '/clients/me/org-config', config),
}
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New tests added for GET/PUT `/clients/me/org-config`
- [ ] Test that `build_rag_prompt()` with org_config includes company info block
- [ ] Test that `build_rag_prompt()` without org_config (or empty dict) works as before (no regression)
- [ ] Manual test: save org config in dashboard → ask bot "how do I contact support?" → bot mentions the support email

---

## GIT PUSH

```bash
git add backend/models.py backend/clients/ backend/chat/service.py \
        backend/migrations/versions/ frontend/app/dashboard/settings/ \
        frontend/lib/api.ts tests/
git commit -m "feat: add org_config layer to Client model and RAG prompt (FI-031)"
git push origin feature/fi-031-org-config
```

---

## NOTES

- `org_config` is intentionally flat JSON (not nested) — easier to form-bind in UI
- Merge-on-update (not replace) means the frontend can send partial updates safely
- All org_config fields are optional — bot still works for clients who haven't filled it in
- This is a prerequisite for FI-007 (per-client system prompt)

---

## PR DESCRIPTION

```markdown
## Summary
Adds `org_config` JSON field to the `Client` model, allowing clients to store product metadata (support email, company name, etc.) that is injected into the RAG prompt for every chat.

## Changes
- `backend/models.py` — add `org_config` column to `Client`
- `backend/migrations/versions/XXX` — Alembic migration
- `backend/clients/service.py` — get/update org_config logic
- `backend/clients/routes.py` — GET/PUT `/clients/me/org-config`
- `backend/chat/service.py` — inject org_config block into `build_rag_prompt()`
- `frontend/app/dashboard/settings/page.tsx` — org config form
- `frontend/lib/api.ts` — org config API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: save org config → bot uses it in answers

## Notes
Prerequisite for FI-007 (per-client system prompt).
```
