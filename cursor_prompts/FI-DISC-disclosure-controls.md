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

Some clients need control over **how much** the bot reveals (tone and depth), not a different factual base.

This is NOT about making the bot lie or hide problems. It's about **audience control** — the same information that's appropriate for a developer is inappropriate for an enterprise end-user.

**v1 scope (this prompt):**
- **One disclosure level per tenant** (Detailed / Standard / Corporate) — same for every chat user and every channel (widget, X-API-Key, etc.)
- Applies in `build_rag_prompt()` as additional system prompt instructions
- Settings UI: single level selector only (no topic blocklist, no simulation, no audience segments)
- No real-time data sources in v1 (Sentry/Status Page integration = v2)

**Explicitly out of v1 (do not implement):**
- Blocked / restricted topics and per-topic redirect messages
- Preview, simulation, or “ask a test question” flows (no `POST .../preview`)
- Per-user or per-segment levels, `audience_tag`, KYC-based overrides, or `audience_levels` in config (future phase if needed)

---

## WHAT TO DO

### 1. Disclosure config schema (`backend/models.py`)

Add `disclosure_config` JSON column to the `Client` model:

```python
disclosure_config = Column(JSON, nullable=True, default=None)
```

Config structure (single field):

```json
{
  "level": "standard"
}
```

**`level` values:** `"detailed"` | `"standard"` | `"corporate"`

(If you prefer backward-compatible naming, you may accept `default_level` as an alias when reading, but persist and document `level` only.)

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
```

In `service.py`:

```python
def get_disclosure_config(client_id: UUID, db: Session) -> dict:
    """Return client's disclosure_config, or default if not set."""
    # Default: {"level": "standard"}

def update_disclosure_config(client_id: UUID, config: dict, db: Session) -> dict:
    """Validate and store disclosure config."""
    # Validation:
    # - `level` must be "detailed" | "standard" | "corporate"
    # - reject extra keys or ignore them (keep stored JSON minimal)
```

### 4. Apply disclosure rules in chat pipeline (`backend/chat/service.py`)

Update `build_rag_prompt()` to accept optional `disclosure_config: dict | None`. **Do not** branch on `audience_tag` or KYC — one level for the whole tenant.

**Logic:**

1. Resolve level (tenant-wide):
   ```python
   level = "standard"
   if disclosure_config:
       level = disclosure_config.get("level") or disclosure_config.get("default_level") or "standard"
   ```

2. Build level instruction:
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

3. Inject into system prompt (before context):
   ```
   [System rules]
   ...existing rules...
   
   [Response level: {level}]
   {level_instruction}
   ```

4. Update `process_chat_message()` and `run_debug()` to load and pass `disclosure_config` from the client record.

**Hard limits (inject unconditionally — cannot be overridden):**
- Never reveal another user's identity or data in any response
- Never confirm or deny specific internal investigation details about security incidents
- Never state that a problem has been resolved unless resolution is confirmed in the source data

These go in as unconditional system rules, before the disclosure level template.

### 5. Frontend: Settings → Response Controls

Create `frontend/app/(app)/settings/disclosure/page.tsx`

**Sections:**

1. **Response detail level (tenant-wide)** — radio buttons: Detailed / Standard / Corporate
   - Each option shows a short description of what is shown/hidden
   - Copy should state clearly that this applies to **all** end-users of this tenant

Add link to this page from the main settings nav as "Response Controls".

### 6. API client (`frontend/lib/api.ts`)

```typescript
disclosure: {
  get: () => apiRequest('GET', '/clients/me/disclosure'),
  update: (config: DisclosureConfig) => apiRequest('PUT', '/clients/me/disclosure', config),
}
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New test: `get_disclosure_config()` returns default when not configured
- [ ] New test: `update_disclosure_config()` validates level values, rejects invalid
- [ ] New test: `build_rag_prompt()` with `{"level": "corporate"}` — corporate level instruction present in prompt
- [ ] New test: disclosure_config=None → defaults to standard (no change to current behavior)
- [ ] New test: GET /clients/me/disclosure returns 200 even with no config (returns defaults)
- [ ] Manual test: set level "corporate" → ask technical question → response is plain language

---

## GIT PUSH

```bash
git add backend/models.py backend/clients/ backend/chat/ \
        backend/migrations/versions/ \
        frontend/app/(app)/settings/disclosure/ frontend/lib/api.ts \
        tests/
git commit -m "feat: add disclosure controls — tenant-wide response detail level (FI-DISC)"
git push origin feature/fi-disc-disclosure-controls
```

---

## NOTES

- **Default is Standard level** — no behavior change for clients who don't configure anything
- **Hard limits are unconditional** — inject them before the level template, not inside it
- **No real-time data sources in v1** — the spec mentions Sentry/Status Page integration; that's v2. For v1, disclosure controls apply only to documentation-based answers.
- **KYC / `audience_tag` is irrelevant to v1 disclosure** — level is tenant-wide only; optional future work could add per-segment overrides.
- Keep the `build_rag_prompt()` signature backward-compatible — `disclosure_config` is optional; omit any `audience_tag` parameter for this feature in v1.

---

## PR DESCRIPTION

```markdown
## Summary
Adds disclosure controls: each tenant sets **one** response detail level (Detailed / Standard / Corporate) for **all** users and channels. Rules are injected as system prompt instructions at chat time. No topic blocklist, no simulation/preview API, no per-audience overrides in v1.

## Changes
- `backend/models.py` — disclosure_config JSON column on Client
- `backend/migrations/versions/XXX` — migration
- `backend/clients/routes.py` + `service.py` — GET/PUT disclosure config
- `backend/chat/service.py` — inject disclosure level into system prompt
- `frontend/app/(app)/settings/disclosure/page.tsx` — Response Controls settings page
- `frontend/lib/api.ts` — disclosure API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: set corporate level → technical question → plain language response

## Notes
Tenant-wide level only; KYC does not affect disclosure in v1.
v1: documentation-based answers only. Sentry/Status Page disclosure = v2.
```
