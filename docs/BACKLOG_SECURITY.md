# Security Backlog

Security, isolation, abuse protection.

---

## 🔴 P1

### [FI-043] PII Redaction — Stage 1 (Regex)
**Problem:** User messages go directly to OpenAI (embeddings + completion) with potential PII — emails, phones, API keys. This is a GDPR risk and a blocker for EU B2B clients.

**Solution (MVP — regex only, no NER):**
- Intercept every user message before any external API call
- Replace Tier 1 entities with typed placeholders: `[EMAIL]`, `[PHONE]`, `[API_KEY]`
- Store original text in DB; only redacted text goes to OpenAI
- Regex patterns: email (RFC-compatible), phone (RU + international formats), API key patterns (Bearer tokens, sk-*, hex strings 32+ chars)

**Where to intercept:** In `backend/chat/service.py` — before `retrieve_context()` and before `generate_answer()`.

**What NOT to do (save for v2):**
- No Presidio / spaCy NER — too heavy for MVP
- No tenant config UI — redaction is always on
- No audit log UI — just a simple DB log table
- No document ingestion scanning (FR-7)

**Effort:** 1 day
**Spec reference:** `specs/pii-redaction-spec.docx` (FR-1, FR-2 Tier 1, FR-3.1 Stage 1 only, FR-4)

---

## 🟠 P2

### [FI-022] CORS — split by routes
- `allow_origins=["*"]` only for `/chat` and `/embed.js`.
- Rest — restrict to `FRONTEND_URL`.

### [FI-022 ext] CORS with client domain whitelist
- Client specifies `allowed_origins` in dashboard.
- Backend checks `Origin` against `Client.allowed_origins` on `/chat`.
- Protection against API key use on third-party sites.
- **Effort:** 2 days.

### [FI-023] Rate limit on `GET /clients/validate/{api_key}`
- Public endpoint without rate limit → brute-force possible.
- Add `@limiter.limit("20/minute")`.

### [FI-035] Prompt injection protection
- Sanitize incoming messages.
- Check for role-switch attempts ("ignore previous instructions...").

---

### [FI-044] PII Redaction — Stage 2 (Presidio NER)
**When:** First EU/enterprise client or explicit compliance requirement.

**Extends FI-043 with:**
- Presidio + spaCy models (`en_core_web_lg`, `ru_core_news_sm`)
- Tier 2 entities: full names, addresses, passport numbers, INN
- Language auto-detection per session
- Tenant config: which Tier 2/3 entity types are active
- Audit log UI: Settings → Privacy → Redaction Log (CSV export, 12mo retention)
- Tenant admin: can view original text in dashboard (own users only, access logged)
- GDPR deletion: `content_original` deleted on request, `content_redacted` retained

**Infrastructure cost:** ~500MB spaCy models on Railway, +50–200ms latency per message
**Spec reference:** `specs/pii-redaction-spec.docx` (full spec)

---

## 🟡 P3

### [FI-006] ENCRYPTION_KEY rotation
- Secure update of OpenAI keys encryption master key.
- Procedure: decrypt old → encrypt new → no data loss.
