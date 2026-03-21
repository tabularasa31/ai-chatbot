# CURSOR PROMPT: [FI-KYC] Know Your Customer — User Identification

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-kyc-user-identification
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add UserSession, UserContext models
- `backend/migrations/versions/` — new Alembic migrations
- `backend/core/security.py` — add HMAC token generation/validation
- `backend/clients/routes.py` — add secret key management endpoint
- `backend/clients/service.py` — add secret key logic
- `backend/clients/schemas.py` — add schemas for KYC
- `backend/routes/widget.py` — update widget session init to accept identity token
- `backend/widget/routes.py` — update widget session init
- `backend/chat/service.py` — inject user context into session
- `frontend/app/(app)/settings/` — new Settings → Widget → API Keys page
- `frontend/lib/api.ts` — add KYC API calls
- `tests/` — add tests for token validation

**Do NOT touch:**
- `backend/auth/` — existing auth system
- `backend/documents/`
- `backend/embeddings/`
- `backend/search/`
- `backend/email/`

---

## CONTEXT

Currently the bot operates as a stateless anonymous widget. Every conversation is a new session with no memory of who the user is. This creates four concrete problems:

1. Escalation tickets are anonymous — L2 agent cannot reply to the user
2. Disclosure controls cannot apply audience-based rules
3. No personalization of routing or priority
4. User returning with follow-up next day starts from zero

This feature adds a **secure token-based identity layer**. The tenant's backend generates a short-lived HMAC-signed token containing user context. The widget receives the token at initialization. The platform validates the token server-side and stores the user context for the session.

**Two modes are supported:**
- **Anonymous mode** (default, current behavior) — no token provided. Bot works fully, all features work, no personalization.
- **Identified mode** — valid signed token provided. User context available to all features.

**Why a signed token (not plain JSON)?**
Plain JSON in the embed code can be modified in browser devtools. A user could change their `plan_tier` to "enterprise". HMAC signature prevents this.

---

## WHAT TO DO

### 1. UserContext schema (`backend/models.py`)

Add a new model (not a table — this is a Pydantic schema used in sessions):

```python
# UserContext — passed in the signed token, stored in Redis session
# Fields:
#   user_id: str (required)
#   email: str | None
#   name: str | None
#   plan_tier: str | None  ("free" | "starter" | "growth" | "pro" | "enterprise")
#   audience_tag: str | None  (e.g. "developer", "end-user", "admin")
#   company: str | None
#   locale: str | None  (e.g. "en", "ru")
```

Also add a SQLAlchemy `UserSession` table for cross-session history (identified users only):

```python
class UserSession(Base):
    __tablename__ = "user_sessions"
    
    id: UUID (primary key)
    client_id: UUID (FK → clients.id, index)
    user_id: str (index)  # tenant's user ID
    email: str | None
    name: str | None
    plan_tier: str | None
    audience_tag: str | None
    session_started_at: datetime
    session_ended_at: datetime | None
    conversation_turns: int (default 0)
    created_at: datetime
```

### 2. Alembic migration

Create migration for `user_sessions` table.

Also add `kyc_secret_key` column (encrypted string, nullable) to the `clients` table:

```python
op.add_column("clients", sa.Column("kyc_secret_key", sa.String(512), nullable=True))
op.create_index("ix_user_sessions_client_user", "user_sessions", ["client_id", "user_id"])
```

### 3. HMAC token validation (`backend/core/security.py`)

Add two functions:

```python
def generate_kyc_token(user_context: dict, secret_key: str, ttl_seconds: int = 300) -> str:
    """
    Generate a signed identity token for widget initialization.
    
    Payload structure:
    {
        "user_id": "...",
        "email": "...",
        ...other UserContext fields...,
        "tenant_id": "...",
        "exp": <unix timestamp>,
        "iat": <unix timestamp>
    }
    
    Token format: base64(json_payload).<hmac_sha256_signature>
    """

def validate_kyc_token(token: str, secret_key: str, tenant_id: str) -> dict | None:
    """
    Validate a signed identity token.
    
    Returns UserContext dict if valid, None if invalid (expired / bad signature / missing fields).
    
    Validation steps:
    1. Split token into payload + signature parts
    2. Decode base64 payload → JSON
    3. Check exp claim — reject if expired
    4. Check tenant_id matches
    5. Check user_id is present and non-empty string
    6. Recompute HMAC-SHA256 of payload using secret_key
    7. Compare with provided signature (constant-time comparison)
    8. Return UserContext fields (exclude exp, iat, tenant_id from returned dict)
    
    On any failure: return None (never raise — fallback to anonymous mode)
    """
```

### 4. Secret key management (`backend/clients/routes.py` + `service.py`)

Add endpoints:

```
POST /clients/me/kyc/secret  → generate a new secret key, store encrypted, return once
GET  /clients/me/kyc/status  → return: {has_secret: bool, identified_session_rate_7d: float, last_identified_session: datetime | None}
POST /clients/me/kyc/rotate  → generate new key, old key remains valid for 1 hour (overlap window)
```

Secret key storage:
- Generate 32-byte random secret → hex string (64 chars)
- Encrypt using existing `ENCRYPTION_KEY` (AES-256, same as OpenAI key)
- Store in `clients.kyc_secret_key`
- **Return the raw key once only** (at generation time). After that, only show masked: `••••••••••••••••••••••••••••••••...abcd`

### 5. Widget session init — accept identity token

In `backend/routes/widget.py` (and `backend/widget/routes.py`), update the session initialization endpoint to accept an optional `identity_token` field:

```python
class WidgetSessionInit(BaseModel):
    api_key: str
    identity_token: str | None = None  # signed KYC token, optional

# In the route handler:
# 1. Validate api_key → get client
# 2. If identity_token provided:
#    - Get client.kyc_secret_key (decrypt it)
#    - Call validate_kyc_token(identity_token, secret_key, client.public_id)
#    - If valid: store UserContext in session (Redis or DB), set identified=True
#    - If invalid: log validation failure (type: expired|bad_signature|missing_user_id), fallback to anonymous
# 3. Return session_id + mode ("identified" | "anonymous")
```

For v1 (without Redis): store user context as a JSON column on the `Chat` model instead of Redis.

Add `user_context` JSON column to the `Chat` model (nullable):
```python
user_context = Column(JSON, nullable=True)
# Stores: {"user_id": "...", "email": "...", "plan_tier": "...", ...}
# null = anonymous session
```

### 6. Pass user context through chat pipeline

In `backend/chat/service.py`, update `process_chat_message()` to accept an optional `user_context: dict | None` parameter.

When user_context is present, inject a short context block into the system prompt:
```
[User context: plan_tier=pro, locale=en]
```

Only inject `plan_tier`, `locale`, and `audience_tag` — never inject `email`, `user_id`, `name` or `plan_mrr` into the prompt (privacy rule from spec FR-6.4).

### 7. Frontend: Settings → Widget → API Keys

Create a new settings page: `frontend/app/(app)/settings/widget/page.tsx`

Show:
- Current status: "Identified mode: active / not configured"
- "Generate signing secret" button → shows key once with copy button, warns "Store this securely. It will not be shown again."
- Key rotation button (when key exists)
- Integration health stats: identified session rate (7d), last identified session timestamp
- Code snippet: how to generate a token server-side (Node.js example)

Add link to this page from the main settings nav.

### 8. API client (`frontend/lib/api.ts`)

```typescript
kyc: {
  generateSecret: () => apiRequest('POST', '/clients/me/kyc/secret'),
  getStatus: () => apiRequest('GET', '/clients/me/kyc/status'),
  rotateSecret: () => apiRequest('POST', '/clients/me/kyc/rotate'),
}
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New test: `generate_kyc_token()` produces valid token, `validate_kyc_token()` returns correct UserContext
- [ ] New test: expired token returns None (not an exception)
- [ ] New test: tampered token (modified payload) returns None
- [ ] New test: wrong tenant_id returns None
- [ ] New test: missing user_id returns None
- [ ] New test: widget session init with valid token → response includes `mode: "identified"`
- [ ] New test: widget session init without token → response includes `mode: "anonymous"`
- [ ] New test: widget session init with invalid token → falls back to anonymous, logs validation failure
- [ ] New test: secret key generation endpoint returns key once
- [ ] Manual test: generate token in Python shell → pass to widget → confirm user_context on Chat record

---

## GIT PUSH

```bash
git add backend/models.py backend/core/security.py backend/clients/ \
        backend/routes/ backend/widget/ backend/chat/ \
        backend/migrations/versions/ \
        frontend/app/(app)/settings/ frontend/lib/api.ts \
        tests/
git commit -m "feat: add KYC user identification — signed token + identified session mode (FI-KYC)"
git push origin feature/fi-kyc-user-identification
```

---

## NOTES

- **Never raise exceptions in `validate_kyc_token()`** — return None and fall back to anonymous. A bad token must not break the widget.
- **Never log UserContext fields** (email, user_id, name) in plain text in application logs. Log only: `kyc_validation_failed: {reason}`, `kyc_session_identified: client_id={...}`.
- **Never inject email/user_id/name/plan_mrr into the LLM prompt** — only plan_tier, locale, audience_tag.
- **v1 skips Redis** — use `Chat.user_context` JSON column instead. Redis session layer is a future improvement.
- **v1 skips cross-session history** — `UserSession` table is created but the lookup logic (prior escalations, conversation continuity) is a v2 feature. Focus on secure token validation and user_context storage.
- HMAC uses SHA-256. Use `hmac.compare_digest()` for constant-time comparison (prevents timing attacks).
- Encrypt the secret key at rest using the same `ENCRYPTION_KEY` / `encrypt_api_key()` function already in `backend/core/crypto.py`.

---

## PR DESCRIPTION

```markdown
## Summary
Adds KYC (Know Your Customer) identity layer to the widget. Tenants can now pass a short-lived HMAC-signed token at widget initialization to identify their users. Identified sessions receive user context (plan tier, locale, audience tag) propagated through the chat pipeline, enabling downstream features: personalized disclosure controls, escalation tickets with user contact info, and audience-based routing.

## Changes
- `backend/models.py` — add UserSession table, user_context JSON column on Chat
- `backend/migrations/versions/XXX` — migration for user_sessions + kyc_secret_key
- `backend/core/security.py` — HMAC token generation and validation
- `backend/clients/routes.py` + `service.py` — secret key management endpoints
- `backend/routes/widget.py` + `backend/widget/routes.py` — accept identity_token at session init
- `backend/chat/service.py` — inject plan_tier/locale/audience_tag into system prompt
- `frontend/app/(app)/settings/widget/page.tsx` — API Keys settings page
- `frontend/lib/api.ts` — KYC API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: generate token → widget init → user_context stored on Chat → visible in logs

## Notes
v1: user_context stored on Chat model (no Redis). Cross-session history (prior escalation lookup, conversation continuity) is v2.
Security: tokens are single-use, 5min TTL, HMAC-SHA256, constant-time comparison.
```
