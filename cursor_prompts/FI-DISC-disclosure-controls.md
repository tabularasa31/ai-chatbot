# CURSOR PROMPT: [FI-DISC] Disclosure Controls

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-disc-disclosure-controls
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add disclosure_config JSON field to Client model
- `backend/migrations/versions/` — new Alembic migration
- `backend/clients/routes.py` — add disclosure config endpoints
- `backend/clients/service.py` — add disclosure config logic
- `backend/clients/schemas.py` — add disclosure schemas
- `backend/chat/service.py` — apply disclosure rules in `build_rag_prompt()`
- `frontend/app/(app)/settings/disclosure/page.tsx` — new Settings → Response Controls page
- `frontend/lib/api.ts` — add disclosure API calls
- `tests/` — add tests for disclosure logic

**Do NOT touch:**
- `backend/auth/`
- `backend/documents/`
- `backend/embeddings/`
- `backend/search/`
- `backend/escalation/`

---

## CONTEXT

Currently the bot answers any question from any user with the same level of detail.

Some clients need control over what the bot reveals. Examples:
- "Don't discuss pricing — redirect to sales"
- "Don't mention competitor names"
- "Don't discuss contract terms — direct to legal@company.com"

This is NOT about making the bot lie or hide problems. It's about **audience control** — the same information that's appropriate for a developer is inappropriate for an enterprise end-user.

**v1 scope (this prompt):**
- Topic blocklist: client defines topics the bot must NOT discuss
- Per-topic redirect: configure what the bot says instead ("Please contact sales@company.com")
- Audience-based disclosure level (from KYC audience_tag): Detailed / Standard / Corporate
- Applies in `build_rag_prompt()` as additional system prompt instructions
- Settings UI for managing rules
- No real-time data sources in v1 (Sentry/Status Page integration = v2)

---

## WHAT TO DO

### 1. Disclosure config schema (`backend/models.py`)

Add `disclosure_config` JSON column to the `Client` model:

```python
disclosure_config = Column(JSON, nullable=True, default=None)
```

Config structure:

```json
{
  "default_level": "standard",
  "audience_levels": {
    "developer": "detailed",
    "end-user": "corporate",
    "admin": "standard"
  },
  "blocked_topics": [
    {
      "topic": "pricing",
      "redirect_message": "For pricing information, please contact sales@yourcompany.com"
    },
    {
      "topic": "competitors",
      "redirect_message": "I'm only able to help with questions about our product."
    },
    {
      "topic": "legal",
      "redirect_message": "For contract or legal questions, please reach out to legal@yourcompany.com"
    }
  ]
}
```

**Level values:** `"detailed"` | `"standard"` | `"corporate"`

**Level definitions (used in prompt injection):**
- `detailed` — full technical detail, all information available
- `standard` (default) — avoid internal technical details (file paths, error IDs, stack traces, vendor names, affected user counts); use plain language
- `corporate` — acknowledge issues exist and are being addressed; no ETAs, no technical details, always offer a support contact

### 2. Alembic migration

```python
def upgrade():
    op.add_column("clients", sa.Column("disclosure_config", sa.JSON(), nullable=True))

def downgrade():
    op.drop_column("clients", "disclosure_config")
```

### 3. Disclosure config endpoints (`backend/clients/routes.py`)

```
GET  /clients/me/disclosure        → return current disclosure_config (or defaults)
PUT  /clients/me/disclosure        → update disclosure_config
POST /clients/me/disclosure/preview → preview: given a simulated question + context, return what the bot would say at each level
```

In `service.py`:

```python
def get_disclosure_config(client_id: UUID, db: Session) -> dict:
    """Return client's disclosure_config, or default if not set."""
    # Default: {"default_level": "standard", "audience_levels": {}, "blocked_topics": []}

def update_disclosure_config(client_id: UUID, config: dict, db: Session) -> dict:
    """Validate and store disclosure config."""
    # Validation:
    # - default_level must be "detailed" | "standard" | "corporate"
    # - audience_levels values must be valid levels
    # - blocked_topics: topic is string (1-100 chars), redirect_message is string (1-500 chars)
    # - max 20 blocked topics
```

### 4. Apply disclosure rules in chat pipeline (`backend/chat/service.py`)

Update `build_rag_prompt()` to accept optional `disclosure_config: dict | None` and `audience_tag: str | None`.

**Logic:**

1. Determine effective disclosure level:
   ```python
   level = "standard"  # default
   if disclosure_config:
       level = disclosure_config.get("default_level", "standard")
       if audience_tag and audience_tag in disclosure_config.get("audience_levels", {}):
           level = disclosure_config["audience_levels"][audience_tag]
   ```

2. Build blocked topics instruction (if any):
   ```python
   blocked = disclosure_config.get("blocked_topics", []) if disclosure_config else []
   if blocked:
       topics_block = "\n".join([
           f'- If asked about "{t["topic"]}": respond ONLY with: "{t["redirect_message"]}" — do not answer from context.'
           for t in blocked
       ])
       blocked_instruction = f"RESTRICTED TOPICS — follow these rules exactly:\n{topics_block}"
   ```

3. Build level instruction:
   ```python
   LEVEL_INSTRUCTIONS = {
       "detailed": "Answer with full technical detail. Include all relevant information.",
       "standard": (
           "Answer in plain language. Do NOT include: internal file paths, stack trace details, "
           "error tracking system names (e.g. Sentry), number of affected users, "
           "internal team or developer names, or version regression details. "
           "Link to public documentation or status pages, not internal tools."
       ),
       "corporate": (
           "Answer in polished, non-technical language suitable for a business audience. "
           "Acknowledge issues exist and are being addressed, but do NOT include: ETAs, "
           "technical details, status page links, or internal system information. "
           "If an issue is ongoing, offer to connect the user with the support team."
       ),
   }
   level_instruction = LEVEL_INSTRUCTIONS.get(level, LEVEL_INSTRUCTIONS["standard"])
   ```

4. Inject into system prompt (before context):
   ```
   [System rules]
   ...existing rules...
   
   [Response level: {level}]
   {level_instruction}
   
   {blocked_instruction if blocked_topics else ""}
   ```

5. Update `process_chat_message()` and `run_debug()` to load and pass `disclosure_config` from the client record.

**Hard limits (inject unconditionally — cannot be overridden):**
- Never reveal another user's identity or data in any response
- Never confirm or deny specific internal investigation details about security incidents
- Never state that a problem has been resolved unless resolution is confirmed in the source data

These go in as unconditional system rules, before the disclosure level template.

### 5. Frontend: Settings → Response Controls

Create `frontend/app/(app)/settings/disclosure/page.tsx`

**Sections:**

1. **Default Detail Level** — radio buttons: Detailed / Standard / Corporate
   - Each option shows a short description of what is shown/hidden
   - "Preview" button → opens preview modal with simulated incident

2. **Audience Segments** — mapping table (only shown if KYC is configured)
   - `audience_tag` → level selector
   - "Add segment" button

3. **Restricted Topics** — list of blocked topic rules
   - Each row: topic name + redirect message (editable)
   - Add / delete buttons
   - Max 20 topics, shown with count

4. **Preview mode**: "Simulate a question" → shows bot response at each level side by side

Add link to this page from the main settings nav as "Response Controls".

### 6. API client (`frontend/lib/api.ts`)

```typescript
disclosure: {
  get: () => apiRequest('GET', '/clients/me/disclosure'),
  update: (config: DisclosureConfig) => apiRequest('PUT', '/clients/me/disclosure', config),
  preview: (question: string, context: string) => apiRequest('POST', '/clients/me/disclosure/preview', {question, context}),
}
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New test: `get_disclosure_config()` returns default when not configured
- [ ] New test: `update_disclosure_config()` validates level values, rejects invalid
- [ ] New test: `update_disclosure_config()` rejects > 20 blocked topics
- [ ] New test: `build_rag_prompt()` with "corporate" level — level instruction present in prompt
- [ ] New test: `build_rag_prompt()` with blocked topic — redirect instruction present in prompt
- [ ] New test: audience_tag "developer" → overrides default level to "detailed"
- [ ] New test: disclosure_config=None → defaults to standard (no change to current behavior)
- [ ] New test: GET /clients/me/disclosure returns 200 even with no config (returns defaults)
- [ ] Manual test: set blocked topic "pricing" → ask about pricing → bot returns redirect message
- [ ] Manual test: set level "corporate" → ask technical question → response is plain language

---

## GIT PUSH

```bash
git add backend/models.py backend/clients/ backend/chat/ \
        backend/migrations/versions/ \
        frontend/app/(app)/settings/disclosure/ frontend/lib/api.ts \
        tests/
git commit -m "feat: add disclosure controls — topic blocklist + audience-based detail levels (FI-DISC)"
git push origin feature/fi-disc-disclosure-controls
```

---

## NOTES

- **Default is Standard level** — no behavior change for clients who don't configure anything
- **Hard limits are unconditional** — inject them before the level template, not inside it
- **Blocked topic detection is instruction-based** (prompt injection), not keyword filtering in the pipeline. This is intentional: the LLM handles nuanced phrasings better than a keyword list.
- **No real-time data sources in v1** — the spec mentions Sentry/Status Page integration; that's v2. For v1, disclosure controls apply only to documentation-based answers.
- **Audience tags come from KYC (FI-KYC)** — if KYC is not configured, audience_tag is always None and default_level applies to everyone. FI-DISC works without FI-KYC.
- Keep the `build_rag_prompt()` signature backward-compatible — new params are optional with defaults.

---

## PR DESCRIPTION

```markdown
## Summary
Adds disclosure controls: tenants can configure which topics the bot must redirect (not answer), and set a detail level (Detailed / Standard / Corporate) for bot responses. Detail level can vary per user audience segment (from KYC). Rules are injected as system prompt instructions at chat time.

## Changes
- `backend/models.py` — disclosure_config JSON column on Client
- `backend/migrations/versions/XXX` — migration
- `backend/clients/routes.py` + `service.py` — GET/PUT disclosure config + preview
- `backend/chat/service.py` — inject disclosure rules into system prompt
- `frontend/app/(app)/settings/disclosure/page.tsx` — Response Controls settings page
- `frontend/lib/api.ts` — disclosure API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: configure blocked topic → ask about it → bot redirects correctly
- [ ] Manual: set corporate level → technical question → plain language response

## Notes
Works without FI-KYC (audience_tag will be None, default level applies).
v1: documentation-based answers only. Sentry/Status Page disclosure = v2.
```
