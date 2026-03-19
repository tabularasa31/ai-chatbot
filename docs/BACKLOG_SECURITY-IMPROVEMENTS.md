# BACKLOG: Security & Observability Improvements

**Based on feedback from Cursor code reviews (March 2026)**

---

## High Priority

### FI-SECURITY-CLIENT-ID-VECTORDB: Filter by client_id at Vector DB Level

**Problem:** Vector search could theoretically return docs from other clients if filtering only at response level.

**Solution:** Make client_id a mandatory filter in vector DB queries:

```python
# Current (risky):
similar_chunks = index.query(query_embedding, top_k=5)  # Returns top 5 from ALL clients
results = [c for c in similar_chunks if c.client_id == current_client]

# Better:
similar_chunks = index.query(
    query_embedding,
    top_k=5,
    filter={"client_id": current_client}  # Filter at DB level
)
```

**Benefits:**
- Data isolation guaranteed at DB level (not app level)
- Faster queries (less data returned)
- Defense in depth (filter at multiple layers)

**Effort:** 0.5 days

**Files:**
- `backend/rag/search.py` (update vector queries)
- `backend/services/chat.py` (ensure client_id in filters)

---

### FI-SECURITY-RATE-LIMIT-MIDDLEWARE: Global Rate Limiting (slowapi)

**Problem:** Some endpoints unprotected from brute force / DDoS.

**Solution:** Add slowapi middleware with per-endpoint limits:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# Per-endpoint limits
@limiter.limit("100/minute")
@app.post("/api/documents/upload")
def upload_doc():
    pass

@limiter.limit("500/minute")
@app.post("/widget/chat")
def widget_chat():
    pass
```

**Endpoints to protect:**
- `/auth/*` — 5 attempts/minute
- `/clients/validate/*` — 20/minute
- `/search` — 100/minute
- `/widget/chat` — 500/minute
- `/chat` — 100/minute

**Effort:** 1 day

**Files:**
- `backend/core/limiter.py` (create if not exists)
- `backend/main.py` (setup middleware)
- All route files (add @limiter decorators)

---

### FI-SECURITY-LANGSMITH-TRACING: Add LangSmith/Langfuse Tracing

**Problem:** Hard to debug LLM issues, no visibility into prompt/response chains.

**Solution:** Add tracing with Langfuse (free tier supports 1M tokens/month):

```python
from langfuse.openai import OpenAI

client = OpenAI(api_key=OPENAI_KEY)

response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[...],
    metadata={"client_id": client_id, "session": session_id}
)
# Automatically logged to Langfuse
```

**Benefits:**
- See all LLM calls with inputs/outputs
- Performance metrics
- Cost tracking per client
- Debug production issues

**Effort:** 0.5 days

**Cost:** Free tier (1M tokens/month)

**Files:**
- `backend/config.py` (add LANGFUSE_KEY)
- `backend/services/chat.py` (replace openai import)

---

## Medium Priority

### FI-SECURITY-FIELD-ENCRYPTION: Encrypt Sensitive Fields

**Problem:** api_key, openai_api_key stored as plain text in DB.

**Solution:** Encrypt at rest using Fernet or KMS:

```python
from cryptography.fernet import Fernet

class Client(Base):
    api_key: Mapped[str] = mapped_column(
        String,
        default=lambda: Fernet(ENCRYPTION_KEY).encrypt(generate_api_key())
    )
```

**Effort:** 1-2 days (includes migration for existing keys)

**Security uplift:** High (protects against DB compromise)

---

### FI-SECURITY-AUDIT-LOG: API Audit Logging

**Problem:** No record of who accessed what when.

**Solution:** Log all API calls to AuditLog table:

```python
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    
    AuditLog.create(
        user_id=request.user.id if authenticated else None,
        action=f"{request.method} {request.url.path}",
        status=response.status_code,
        duration=time.time() - start,
        ip=request.client.host,
    )
    return response
```

**Effort:** 1 day

---

### FI-SECURITY-JWT-REFRESH: JWT Refresh Token Rotation

**Problem:** Long-lived JWTs can be compromised.

**Solution:** Use short-lived access tokens + refresh tokens:

```python
# Access token: 15 min
# Refresh token: 7 days

@app.post("/auth/refresh")
def refresh_token(refresh_token: str):
    # Validate, rotate, return new access token
    pass
```

**Effort:** 1 day

---

## Lower Priority

### FI-SECURITY-CORS-ADVANCED: Advanced CORS for Enterprise

**Problem:** Enterprise customers want domain restrictions.

**Solution:** Store embed_allowed_origins per client, validate on request.

**Status:** Already in FI-EMBED Phase 2 backlog

---

### FI-SECURITY-RATE-LIMIT-TIER: Tiered Rate Limits

**Problem:** Enterprise customers need higher limits than free tier.

**Solution:** Rate limits based on subscription tier:

```python
RATE_LIMITS = {
    "free": 100,
    "pro": 1000,
    "enterprise": None,  # unlimited
}

@app.post("/widget/chat")
def chat(client_id: str, db: Session):
    client = db.query(Client).filter_by(public_id=client_id).first()
    limit = RATE_LIMITS[client.plan]
    # Apply limit
```

**Effort:** 0.5 days

**Dependency:** Subscription tier system (FI-PRICING)

---

### FI-SECURITY-CSP-HEADERS: Content Security Policy Headers

**Problem:** XSS attacks possible on widget page.

**Solution:** Strict CSP headers on /widget endpoint:

```python
@app.get("/widget")
def widget_page():
    headers = {
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    }
    return HTMLResponse(html, headers=headers)
```

**Effort:** 0.5 days

---

### FI-SECURITY-DEPENDENCY-AUDIT: Automated Dependency Scanning

**Problem:** Vulnerable dependencies in production.

**Solution:** 
- Use `pip-audit` in CI/CD
- Enable Dependabot on GitHub
- Weekly security scans

**Effort:** 0.5 days

---

### FI-SECURITY-SECRETS-ROTATION: Secrets Rotation Policy

**Problem:** API keys/secrets never rotated.

**Solution:**
- Document rotation schedule (monthly for API keys)
- Add `key_created_at`, `key_rotated_at` fields
- Dashboard warning when key is old

**Effort:** 1 day

---

## Recommendation Order

1. **Immediately (Week 1):**
   - client_id filter at vector DB level (data isolation)
   - slowapi rate limiting (prevents abuse)
   - LangSmith tracing (debugging)

2. **Soon (Week 2-3):**
   - JWT refresh tokens (security best practice)
   - Audit logging (compliance)
   - Encrypt api_key field (defense in depth)

3. **Later (Month 2+):**
   - CSP headers
   - Tiered rate limits
   - Dependency scanning
   - Secrets rotation

---

## RICE Scoring

| Feature | Reach | Impact | Confidence | Effort | Score |
|---------|-------|--------|------------|--------|-------|
| Vector DB client_id filter | High | Critical | High | Low | 30 |
| Rate Limiting (slowapi) | High | High | High | Medium | 18 |
| LangSmith Tracing | Medium | High | High | Low | 12 |
| JWT Refresh Tokens | High | Medium | High | Medium | 9 |
| Audit Logging | Medium | Medium | Medium | Medium | 4 |
| Field Encryption | High | Medium | Medium | High | 6 |

**Top 3:** Vector DB filter > Rate limiting > LangSmith

---

_Last updated: 2026-03-19 from code review feedback_
