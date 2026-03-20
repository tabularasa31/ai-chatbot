# FI-CORS: Dynamic CORS per Client Domain

**Status:** Specification (not implemented)  
**Priority:** P1 (blocks embed functionality)  
**Effort:** 2–3 hours  
**Date:** 2026-03-19

---

## Problem

Current CORS implementation uses static whitelist `CORS_ALLOWED_ORIGINS` from env var. This works for `getchat9.live` and `embed.getchat9.live`, but **fails for each client's custom domain** where they embed the widget.

**Example failure:**
- Client domain: `example.com`
- Widget tries POST to `/chat` from `example.com`
- CORS whitelist: `https://getchat9.live,https://embed.getchat9.live`
- Result: 🚫 Preflight fails, browser blocks request → "Failed to fetch"

**Current workaround:** Add each client domain to env var manually → scales poorly for N clients.

---

## Solution: Dynamic CORS per API Key

Instead of static whitelist, resolve allowed domains **from the API key** at request time:

1. Client passes API key (in header or query param)
2. Backend looks up Client record with that API key
3. Check `allowed_origins` column in Client table
4. Return CORS headers matching that client's domains
5. Request proceeds

**Benefits:**
- ✅ Scales to any number of clients
- ✅ No env var updates needed for new clients
- ✅ Admin can manage domains per client via dashboard
- ✅ Secure (only allows configured domains for that key)

---

## Architecture

### Database Schema Change

Add column to `Client` table:

```sql
ALTER TABLE client ADD COLUMN allowed_origins TEXT DEFAULT NULL;
-- Stores comma-separated domains or JSON array
-- Example: "https://example.com,https://www.example.com"
-- If NULL, use default (getchat9.live only, not client's custom domain)
```

### CORS Middleware Flow

```
Request comes in: POST /chat from example.com

1. Extract API key from header (Authorization: Bearer <key>) or query param
2. Query Client table: SELECT allowed_origins FROM client WHERE api_key = <key>
3. If not found → use DEFAULT_ALLOWED_ORIGINS (getchat9.live, embed.getchat9.live)
4. Parse allowed_origins (comma-separated or JSON)
5. Get origin from request header
6. If origin in allowed_origins → set CORS headers ✅
7. Else → don't set CORS headers (browser blocks) 🚫
```

### Implementation

**File: backend/core/cors.py** (new file)

```python
from fastapi import Request
from sqlalchemy.orm import Session
from backend.models import Client
from typing import List

def get_allowed_origins_for_key(api_key: str, db: Session) -> List[str]:
    """Get allowed CORS origins for an API key."""
    
    # Default origins (always allow own domain)
    DEFAULT = [
        "https://getchat9.live",
        "https://embed.getchat9.live",
        "http://localhost:3000",  # dev
    ]
    
    if not api_key:
        return DEFAULT
    
    client = db.query(Client).filter(Client.api_key == api_key).first()
    if not client or not client.allowed_origins:
        return DEFAULT
    
    # Parse allowed_origins (comma-separated)
    origins = [o.strip() for o in client.allowed_origins.split(",") if o.strip()]
    
    # Always include defaults
    origins.extend(DEFAULT)
    
    return list(set(origins))  # Remove duplicates


def is_origin_allowed(origin: str, allowed_origins: List[str]) -> bool:
    """Check if request origin is in whitelist."""
    return origin in allowed_origins
```

**File: backend/main.py** (update middleware)

Current middleware is static. Replace with dynamic version:

```python
from fastapi.middleware.cors import CORSMiddleware
from backend.core.cors import get_allowed_origins_for_key, is_origin_allowed
from starlette.middleware.base import BaseHTTPMiddleware

class DynamicCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract API key from request
        api_key = None
        
        # Try Authorization header first (Bearer token)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]  # Remove "Bearer "
        
        # Or check query params
        if not api_key:
            api_key = request.query_params.get("api_key")
        
        # Get allowed origins for this key
        db = next(get_db())
        allowed_origins = get_allowed_origins_for_key(api_key, db)
        
        origin = request.headers.get("origin", "")
        
        # Handle preflight (OPTIONS)
        if request.method == "OPTIONS":
            if is_origin_allowed(origin, allowed_origins):
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Authorization",
                        "Access-Control-Allow-Credentials": "false",
                    },
                )
            else:
                return Response(status_code=403)
        
        # Regular request
        response = await call_next(request)
        
        if is_origin_allowed(origin, allowed_origins):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Credentials"] = "false"
        
        return response

# In app initialization:
# Remove old CORSMiddleware
# app.add_middleware(DynamicCORSMiddleware)
```

---

## Database Migration

**File: backend/migrations/versions/XXXXX_add_allowed_origins_to_client.py**

```python
def upgrade():
    op.add_column('client', sa.Column('allowed_origins', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('client', 'allowed_origins')
```

---

## Client Model Update

**File: backend/models.py**

```python
class Client(Base):
    __tablename__ = "client"
    
    # ... existing columns ...
    
    allowed_origins: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Comma-separated list of allowed CORS origins for widget embedding"
    )
```

---

## Dashboard Feature (FI-042, future)

Add form in Client dashboard to edit `allowed_origins`:

```
Client Settings → Widget Embedding
[Input] Allowed Domains
  https://example.com
  https://www.example.com
  https://subdomain.example.com
  
[Tip] Widget will work on these domains only. Separate with commas.
```

---

## Testing

### Unit Tests

```python
# test_cors.py

def test_get_allowed_origins_for_unknown_key(db):
    origins = get_allowed_origins_for_key("unknown_key_12345", db)
    assert "https://getchat9.live" in origins
    assert "http://localhost:3000" in origins

def test_get_allowed_origins_for_client_with_custom_domains(db):
    client = Client(
        name="Example Inc",
        api_key="test_key_xyz",
        allowed_origins="https://example.com,https://www.example.com"
    )
    db.add(client)
    db.commit()
    
    origins = get_allowed_origins_for_key("test_key_xyz", db)
    assert "https://example.com" in origins
    assert "https://www.example.com" in origins
    assert "https://getchat9.live" in origins  # default still included

def test_is_origin_allowed():
    allowed = ["https://example.com", "https://getchat9.live"]
    assert is_origin_allowed("https://example.com", allowed)
    assert not is_origin_allowed("https://evil.com", allowed)
```

### Integration Tests

```bash
# Test preflight from allowed origin
curl -X OPTIONS https://api.getchat9.live/chat \
  -H "Origin: https://example.com" \
  -H "Authorization: Bearer test_key_xyz" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: Content-Type" \
  -v

# Should return 200 + Access-Control-Allow-Origin: https://example.com

# Test from disallowed origin
curl -X OPTIONS https://api.getchat9.live/chat \
  -H "Origin: https://evil.com" \
  -H "Authorization: Bearer test_key_xyz" \
  -v

# Should return 403 (no CORS headers)
```

---

## Edge Cases & Security

### Edge Case 1: No API key in request
→ Use DEFAULT origins (only getchat9.live, embed.getchat9.live)  
→ Custom client domains not allowed (client must identify itself)

### Edge Case 2: Malformed allowed_origins in DB
→ Strip whitespace, validate URLs, skip invalid ones  
→ Log warning but don't fail request

### Edge Case 3: Multiple API keys per client
→ Each key can have different allowed_origins  
→ Useful for staging vs production keys

### Security: API key leakage
- API key visible in browser (client-side widget)
- This is by design (client must identify to backend)
- Secure because allowed_origins restricted to specific domains only
- Attacker knowing API key can't use it from arbitrary domain

---

## Migration Path

### Phase 1: Deploy (current)
1. Add migration (allowed_origins column)
2. Deploy new middleware
3. Set allowed_origins=NULL for all existing clients (uses default)
4. Test with self (getchat9.live)

### Phase 2: Rollout (next week)
1. Ask each client for their domain
2. Admin manually updates allowed_origins via database
3. Client tests widget on their domain

### Phase 3: Self-service (FI-042, future)
1. Add form in Client dashboard
2. Clients can update allowed_origins themselves
3. Real-time effect (no redeploy needed)

---

## Rollback Plan

If dynamic CORS breaks:
1. Revert middleware to static CORSMiddleware
2. Add wildcard or known-bad client domains to CORS_ALLOWED_ORIGINS
3. Keep column in DB (safe to leave)

---

## Metrics & Monitoring

Track (for future dashboard):
- `cors_preflight_allowed` — preflight requests that passed CORS check
- `cors_preflight_denied` — preflight blocked (wrong origin)
- `clients_with_custom_origins` — how many clients have custom allowed_origins set

---

## Questions for Elle

1. **Should we also validate that allowed_origins are valid URLs?**
   - YES → add URL validation, log errors
   - NO → trust admin input

2. **Should we allow wildcards?** (e.g., `https://*.example.com`)
   - YES → more flexible for subdomains
   - NO → simpler, more secure

3. **Should widget also send API key in query param?** (for cookie-less requests)
   - Current: uses Authorization header (Bearer)
   - Alternative: ?api_key=xyz in URL (visible to users)
   - Recommend: Keep Bearer, add query param fallback

---

## Files to Modify

- [ ] `backend/models.py` — add allowed_origins column
- [ ] `backend/core/cors.py` — new file with logic
- [ ] `backend/main.py` — replace CORSMiddleware with DynamicCORSMiddleware
- [ ] `backend/migrations/versions/*.py` — migration
- [ ] `tests/test_cors.py` — unit tests

---

## Acceptance Criteria

- [ ] Client with custom allowed_origins can POST to /chat from their domain
- [ ] Preflight (OPTIONS) returns 200 + correct CORS headers
- [ ] Request from disallowed origin is blocked (no CORS headers)
- [ ] Default origins still work (getchat9.live, embed.getchat9.live)
- [ ] Unknown API key uses default origins
- [ ] Tests pass (unit + integration)
- [ ] No performance regression (query per request is acceptable)
