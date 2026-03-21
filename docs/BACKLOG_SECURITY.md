# Security Backlog

Security, isolation, abuse protection.

---

## ✅ Shipped (security-relevant)

### ~~[FI-043] PII Redaction — Stage 1 (Regex)~~ ✅ Done (2026-03-21)
**Problem (was):** User messages went directly to OpenAI (embeddings + completion + validation) with potential PII.

**Implemented:**
- `backend/chat/pii.py` — pure-regex redaction: `[EMAIL]`, `[PHONE]`, `[API_KEY]`, `[CREDIT_CARD]` (Tier 1A).
- `backend/chat/service.py` — `redact()` before `retrieve_context()`, `generate_answer()`, and `validate_answer()` in `process_chat_message()` and `run_debug()`.
- Original question remains in `Message.content` for tenant admin context; redacted text only crosses the OpenAI boundary.
- Tests: `tests/chat/test_pii.py`.

**Still out of scope (v2 / FI-044):** Presidio NER, tenant toggles, ingestion scanning, GDPR deletion flows — see FI-044 below.

**Spec reference:** `specs/pii-redaction-spec.docx` (FR-1, FR-2 Tier 1, FR-3.1 Stage 1 only, FR-4)

---

## 🔴 P1

*(нет открытых P1 в этом файле; следующий шаг по PII — FI-044 при требовании enterprise/EU.)*

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

### ~~[FI-023] Rate limit on `GET /clients/validate/{api_key}`~~ ✅ Done
- **Shipped:** `@limiter.limit("20/minute")` on `validate_api_key` in `backend/clients/routes.py`.

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
