# FI-EMBED: Public Script Widget (Zero-Config Embedding)

**Status:** Specification (Design Review)  
**Priority:** P1 (core product feature, blocks customer adoption)  
**Complexity:** Medium (3–4 days)  
**Date:** 2026-03-19

---

## Executive Summary

**Current problem:** Customers want to embed Chat9 widget on their websites. Current approach requires:
- Manual domain registration
- Database configuration
- Admin involvement per customer
- Friction, support burden, poor UX

**Solution:** Industry-standard "public script" model (used by Intercom, Drift, Chatbase, DocsBot):
- Customer gets unique **public script link** with embedded client ID
- Paste script anywhere → widget works immediately
- **Zero domain configuration** needed
- Works on any domain automatically
- Scales infinitely

**Result:** Self-service embedding, customer-friendly, industry-standard.

---

## Problem Statement

### Current State

```javascript
// Customer wants to embed Chat9 on their website
// Today: Doesn't work without admin setup

<script>
  // ❌ Error: CORS blocks request
  // ❌ Widget doesn't load
  // ❌ Customer emails support
  // ❌ Admin adds domain to env var
  // ❌ Redeploy required
  // ❌ 24-48h later: finally works
</script>
```

### Why This Matters

1. **Customer friction:** Multi-step process, support tickets
2. **Scalability:** N customers = N env var updates = N redeployments
3. **Support burden:** "Why doesn't my widget work?" issues
4. **Competitive disadvantage:** DocsBot/Chatbase are plug-and-play
5. **Time to value:** Days instead of seconds

### Industry Baseline

Competitors (Intercom, Drift, Chatbase, DocsBot) all use the "public script" model:
```javascript
// Paste once, works everywhere
<script src="https://service.com/embed.js?clientId=xyz"></script>
```

No domain configuration. No admin involved. Works immediately.

---

## Solution Overview

### How It Works

```
┌─────────────────────────────────────────────────────────┐
│ Customer: Creates Bot in Chat9 Dashboard                │
├─────────────────────────────────────────────────────────┤
│ Dashboard shows:                                         │
│  "Embed Code:"                                           │
│  <script src="https://chat9.live/embed.js?              │
│           clientId=ch_abc123xyz"></script>              │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ Customer: Pastes on their website                       │
│  (example.com, staging.example.com, any domain)         │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ Browser: Loads embed.js from chat9.live (public)        │
│  - No CORS issues (script tag always works)             │
│  - Script extracts clientId=ch_abc123xyz                │
│  - Creates iframe pointing to widget page               │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ Backend: Receives POST /chat                            │
│  - clientId: ch_abc123xyz (public)                       │
│  - Query Client table: SELECT * WHERE public_id = ...   │
│  - Return: docs, system_prompt, etc.                    │
│  - NO origin checking needed!                           │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│ Result: ✅ Widget works on example.com                   │
│         ✅ Zero configuration                            │
│         ✅ Instant (no redeploy)                         │
│         ✅ Works on any domain                           │
└─────────────────────────────────────────────────────────┘
```

### Key Concept: Public vs Private IDs

```
api_key (PRIVATE):
  - User creates private API key for server-to-server
  - Used in backend /api endpoints
  - Secret, not exposed to browser
  - Example: sk_live_abc123xyz789

client_id (PUBLIC):
  - Generated when bot is created
  - Visible in embed script (intentional)
  - Used by widget to identify which bot
  - Example: ch_abc123xyz (prefix: ch_)
  - Safe to expose (only identifies bot, no sensitive data)
```

**Security:** public_id is just an identifier, like username. Not secret. API key stays secret.

---

## Architecture

### System Components

```
┌────────────────────────────────────────────────────────┐
│  Customer Website                                      │
│  ┌──────────────────────────────────────────────────┐  │
│  │ <script src="chat9.live/embed.js?clientId=...">  │  │
│  │ Loads publicly, any domain ✅                     │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
                      ↓
┌────────────────────────────────────────────────────────┐
│ Backend (chat9.live)                                   │
│ ┌────────────────────────────────────────────────────┐ │
│ │ GET /embed.js (public script)                       │ │
│ │   → Returns JavaScript                             │ │
│ │   → Creates iframe on customer domain              │ │
│ │   → Passes clientId to iframe                      │ │
│ └────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │ GET/POST /widget (iframe page)                     │ │
│ │   → Renders chat UI                                │ │
│ │   → Shows chat interface                           │ │
│ │   → Sends queries to /chat endpoint                │ │
│ └────────────────────────────────────────────────────┘ │
│ ┌────────────────────────────────────────────────────┐ │
│ │ POST /chat (API endpoint)                          │ │
│ │   → Query: clientId (public)                       │ │
│ │   → Look up client docs, system prompt             │ │
│ │   → Process and return response                    │ │
│ │   → NO origin checking (clientId = permission)    │ │
│ └────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────┘
```

### CORS Configuration (Simplified)

```python
# backend/main.py

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://getchat9.live",           # Our own domain
        "https://embed.getchat9.live",     # Widget iframe (if separate)
        "http://localhost:3000",           # Dev
    ],
    # Note: NO per-client origins!
    # Widget requests come from iframe on chat9.live (our domain)
    # So CORS is not even involved in the request path
)
```

**Key insight:** Iframe is hosted on our domain → requests come from our domain → CORS not needed!

---

## Implementation Details

### 1. Database Schema Changes

**Add to Client model:**

```python
# backend/models.py

class Client(Base):
    __tablename__ = "client"
    
    # ... existing columns ...
    
    public_id: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True,
        doc="Public identifier for embed widget (e.g., ch_abc123xyz)"
    )
    # Generated automatically, format: "ch_" + 18-char random
    
    # Optional: advanced CORS (Phase 2)
    embed_allowed_origins: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Optional: comma-separated allowed domains (leave empty for any domain)"
    )
```

**Migration:**

```python
# backend/migrations/versions/XXXXX_add_public_id_to_client.py

def upgrade():
    op.add_column('client', sa.Column('public_id', sa.String(20), nullable=True))
    op.create_unique_constraint('uq_client_public_id', 'client', ['public_id'])
    op.create_index('ix_client_public_id', 'client', ['public_id'])
    
    # Backfill existing clients
    connection = op.get_bind()
    from backend.models import Client
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=connection)
    session = Session()
    
    clients = session.query(Client).filter(Client.public_id.is_(None)).all()
    for client in clients:
        client.public_id = generate_public_id()  # ch_xyz123...
    session.commit()

def downgrade():
    op.drop_constraint('uq_client_public_id', 'client')
    op.drop_index('ix_client_public_id', 'client')
    op.drop_column('client', 'public_id')
```

### 2. Public ID Generation

**Utility function:**

```python
# backend/core/utils.py

import secrets
import string

def generate_public_id(prefix: str = "ch_") -> str:
    """
    Generate public client ID.
    Format: ch_<18-char random alphanumeric>
    Example: ch_a1b2c3d4e5f6g7h8i9
    """
    chars = string.ascii_lowercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(18))
    return f"{prefix}{random_part}"
```

### 3. Public Script Endpoint

**File: backend/routes/public.py** (new)

```python
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pathlib import Path

public_router = APIRouter(prefix="", tags=["public"])

@public_router.get("/embed.js")
async def get_embed_script():
    """
    Public script for embedding Chat9 widget.
    Accessible from any domain (no CORS issues).
    
    Usage:
    <script src="https://chat9.live/embed.js?clientId=ch_abc123"></script>
    """
    
    # Serve embed.js with no cache headers (always fresh)
    script_path = Path(__file__).parent.parent / "static" / "embed.js"
    
    return FileResponse(
        path=script_path,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Content-Type-Options": "nosniff",
        }
    )
```

**File: backend/static/embed.js** (new)

```javascript
(function() {
  // Get script parameters
  const scripts = document.querySelectorAll('script');
  let clientId = null;
  let chatBaseUrl = 'https://chat9.live'; // Configurable
  
  for (let script of scripts) {
    const src = script.src || '';
    if (src.includes('embed.js')) {
      const url = new URL(src);
      clientId = url.searchParams.get('clientId');
      const baseParam = url.searchParams.get('baseUrl');
      if (baseParam) chatBaseUrl = baseParam; // Allow override (testing)
      break;
    }
  }
  
  if (!clientId) {
    console.warn('Chat9 Widget: clientId not found in script tag');
    return;
  }
  
  // Create container
  const container = document.createElement('div');
  container.id = 'chat9-widget-container';
  container.style.cssText = `
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 9999;
  `;
  document.body.appendChild(container);
  
  // Create iframe
  const iframe = document.createElement('iframe');
  iframe.src = \`\${chatBaseUrl}/widget?clientId=\${clientId}\`;
  iframe.id = 'chat9-widget-iframe';
  iframe.style.cssText = \`
    width: 400px;
    height: 600px;
    border: none;
    border-radius: 12px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  \`;
  iframe.allow = "microphone; camera";
  
  container.appendChild(iframe);
  
  // Log for debugging
  console.log('Chat9 Widget loaded', { clientId });
})();
```

### 4. Widget Iframe Page

**File: frontend/app/widget/page.tsx** (new)

```typescript
'use client';

import { useSearchParams } from 'next/navigation';
import { ChatWidget } from '@/components/ChatWidget';
import { useEffect, useState } from 'react';

export default function WidgetPage() {
  const searchParams = useSearchParams();
  const clientId = searchParams.get('clientId');
  const [isValid, setIsValid] = useState(false);
  
  useEffect(() => {
    if (!clientId) {
      console.error('Missing clientId');
      return;
    }
    setIsValid(true);
  }, [clientId]);
  
  if (!isValid) {
    return <div>Invalid client ID</div>;
  }
  
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <ChatWidget clientId={clientId!} />
    </div>
  );
}
```

### 5. Backend /chat Endpoint (Updated)

**File: backend/chat/routes.py**

```python
from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

@chat_router.post("/chat")
def chat(
    message: str,
    client_id: str = Query(..., description="Public client ID (ch_xxx)"),
    session_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    # Remove: current_user (widget doesn't have auth)
):
    """
    Process chat message for embedded widget.
    
    Parameters:
    - client_id: public identifier (from embed script)
    - message: user query
    - session_id: optional, for conversation continuity
    
    No authentication needed (clientId = permission).
    """
    
    # Validate client exists
    client = db.query(Client).filter(Client.public_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # Validate client is active (not deleted/suspended)
    if not client.is_active:
        raise HTTPException(status_code=403, detail="This client is not active")
    
    # Rest of existing chat logic...
    # (same as before, but no per-origin checking)
    
    return {
        "response": response_text,
        "used_chunks": chunks,
        "session_id": session_id or new_session_id,
    }
```

**Key change:** Remove `current_user` dependency. Use `client_id` for identification.

### 6. Dashboard: Embed Code Display

**File: frontend/app/dashboard/settings/embed.tsx** (new)

```typescript
import { useMutation, useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';

export function EmbedSettings({ clientId }: { clientId: string }) {
  const embedUrl = `https://chat9.live/embed.js?clientId=${clientId}`;
  
  const handleCopy = () => {
    navigator.clipboard.writeText(
      `<script src="${embedUrl}"></script>`
    );
    toast.success('Embed code copied to clipboard');
  };
  
  return (
    <Card>
      <h2>Embed on Your Website</h2>
      <p>Copy and paste this code on your website:</p>
      
      <pre style={{ background: '#f5f5f5', padding: '12px', overflow: 'auto' }}>
        {`<script src="${embedUrl}"></script>`}
      </pre>
      
      <Button onClick={handleCopy}>Copy Code</Button>
      
      <div style={{ marginTop: '20px' }}>
        <h3>How it works</h3>
        <ol>
          <li>Copy the code above</li>
          <li>Paste it before closing &lt;/body&gt; tag on your website</li>
          <li>The widget will appear automatically (bottom-right)</li>
          <li>Works on any domain (staging, production, anywhere)</li>
        </ol>
      </div>
    </Card>
  );
}
```

---

## User Onboarding Flow

### Before (Current - Manual)
```
1. Customer creates bot
2. Gets API key
3. Contact support: "How do I embed?"
4. Support asks: "What's your domain?"
5. Support adds domain to env var
6. Engineer redeploys backend
7. Wait 15-30 minutes
8. Test widget on domain
9. Sometimes doesn't work → debug → more time
```
**Time to value: 1-2 hours** (with support involvement)

### After (New - Self-Service)
```
1. Customer creates bot
2. Dashboard shows: "Embed Code"
3. Customer copies 1 line:
   <script src="https://chat9.live/embed.js?clientId=ch_xyz"></script>
4. Pastes on their website (any domain)
5. Refreshes page
6. Widget works ✅
```
**Time to value: 30 seconds** (self-service)

---

## Security Considerations

### 1. Public ID Design

- **Public:** `client_id` (ch_abc123xyz) is intentionally visible
- **Private:** `api_key` (sk_live_xyz) stays secret, never sent to browser
- **Principle:** clientId identifies which bot, not permission level

### 2. Rate Limiting

```python
# Rate limit per clientId (not origin)
@limiter.limit("100/minute")  # per clientId
def chat(message: str, client_id: str):
    pass
```

### 3. CSRF Protection (Not needed)

- POST comes from same-origin iframe (on chat9.live)
- No cross-origin form submission
- CSRF tokens not needed

### 4. XSS Prevention

```javascript
// embed.js sanitization
// Only inject trusted script (our own domain)
const src = `${chatBaseUrl}/widget?clientId=${clientId}`;
// clientId validated server-side, so no injection risk
```

### 5. Private Data Isolation

```python
# Each client can only see their own docs/messages
def chat(message: str, client_id: str, db: Session):
    client = db.query(Client).filter(Client.public_id == client_id).first()
    
    # Query docs only for this client
    docs = db.query(Document).filter(
        Document.client_id == client.id  # ← Scoped to client
    ).all()
```

### 6. Optional: Advanced CORS (Phase 2)

If customer requests domain restrictions:

```python
def is_embed_allowed(origin: str, client: Client, db: Session) -> bool:
    # If client has no restrictions, allow all
    if not client.embed_allowed_origins:
        return True
    
    # Otherwise check whitelist
    allowed = [o.strip() for o in client.embed_allowed_origins.split(',')]
    return origin in allowed
```

**Not used by default.** Only if customer explicitly wants it.

---

## Database Queries & Performance

### Query: Get client by public_id

```python
# Fast (indexed lookup)
client = db.query(Client)\
    .filter(Client.public_id == "ch_abc123xyz")\
    .first()
```

**Index:** `CREATE INDEX ix_client_public_id ON client(public_id)`

### Query: Get docs for client

```python
docs = db.query(Document)\
    .filter(Document.client_id == client.id)\
    .all()
# Scoped to single client, fast
```

---

## Testing

### Unit Tests

```python
# test_public_id.py

def test_generate_public_id():
    id = generate_public_id()
    assert id.startswith('ch_')
    assert len(id) == 23  # ch_ + 20 chars
    
def test_public_id_uniqueness():
    ids = [generate_public_id() for _ in range(1000)]
    assert len(set(ids)) == 1000  # All unique

# test_chat_endpoint.py

def test_chat_with_valid_client_id(db):
    client = create_test_client(db, public_id="ch_test123")
    response = client.post("/chat", json={
        "message": "hello",
        "client_id": "ch_test123"
    })
    assert response.status_code == 200

def test_chat_with_invalid_client_id(db):
    response = client.post("/chat", json={
        "message": "hello",
        "client_id": "ch_nonexistent"
    })
    assert response.status_code == 404
```

### Integration Tests

```bash
# Test embed script loads
curl -I https://chat9.live/embed.js
# → 200 OK, Content-Type: application/javascript

# Test widget page loads
curl https://chat9.live/widget?clientId=ch_test123
# → 200 OK, HTML page

# Test chat endpoint accepts clientId
curl -X POST https://chat9.live/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "client_id": "ch_test123"}'
# → 200 OK + response
```

### Manual Testing

1. Create test client with `public_id = "ch_test123"`
2. Paste script on test HTML file:
   ```html
   <html>
   <body>
   <script src="http://localhost:8000/embed.js?clientId=ch_test123"></script>
   </body>
   </html>
   ```
3. Open in browser
4. Verify iframe loads and chat works

---

## Acceptance Criteria

- [ ] Client model has `public_id` column (unique, indexed)
- [ ] Migration runs successfully on production
- [ ] Existing clients backfilled with public_ids
- [ ] `/embed.js` endpoint returns JavaScript
- [ ] embed.js works on multiple test domains (no CORS issues)
- [ ] `/widget?clientId=...` page renders chat UI
- [ ] `/chat` endpoint accepts `client_id` query param
- [ ] Requests from embed script are handled correctly
- [ ] Dashboard shows embed code for each client
- [ ] Customers can copy & paste embed code
- [ ] Widget works on customer domains immediately (no setup)
- [ ] Rate limiting works per clientId
- [ ] All existing auth flows still work (for authenticated endpoints)
- [ ] Tests pass (unit + integration + manual)

---

## File Changes Summary

### New Files
- `backend/routes/public.py` — Public endpoints
- `backend/static/embed.js` — Embed script
- `frontend/app/widget/page.tsx` — Widget iframe page
- `frontend/components/ChatWidget.tsx` — Chat UI component
- `frontend/app/dashboard/settings/embed.tsx` — Embed settings page
- `tests/test_embed.py` — Tests
- `backend/migrations/versions/XXXXX_add_public_id.py` — Migration

### Modified Files
- `backend/models.py` — Add public_id column
- `backend/chat/routes.py` — Update /chat to accept client_id
- `backend/main.py` — Register public_router (no CORS changes)
- `frontend/app/dashboard/settings/page.tsx` — Add embed settings tab
- `frontend/app/layout.tsx` — Include embed settings in navigation

---

## Phasing

### Phase 1: Core Embedding (This Spec, 3-4 days)
- [x] Database: add public_id
- [x] Backend: /embed.js endpoint + script
- [x] Backend: /widget iframe page
- [x] Backend: /chat accepts client_id
- [x] Frontend: Widget UI component
- [x] Frontend: Embed code display in dashboard
- [x] Tests & manual verification

### Phase 2: Advanced Features (Future, FI-EMBED-2)
- Optional: embed_allowed_origins column
- Optional: Widget customization (colors, position)
- Optional: Analytics dashboard

### Phase 3: Self-Service (Future, FI-EMBED-3)
- Dashboard form to edit embed_allowed_origins
- Widget preview in dashboard
- Usage analytics

---

## Success Metrics

- **Time to embed:** < 1 minute (copy & paste)
- **Support tickets:** Reduce "how do I embed?" by 80%
- **Customer satisfaction:** Self-service > admin support
- **Scalability:** N customers without N config changes
- **Competitive parity:** Match DocsBot/Chatbase UX

---

## Notes & Decisions

### Decision: Why public_id vs api_key?

**api_key (current):**
- Secret, for server-to-server API calls
- Never exposed to browser
- Example: sk_live_abc123xyz

**public_id (new):**
- Public, for client-side widget identification
- Visible in embed script (intentional, like GitHub username)
- Example: ch_abc123xyz
- Not sensitive, just identifies which bot

**Why separate?**
- Security: api_key stays secret, widget doesn't need it
- Clarity: api_key = authentication, public_id = identification
- Flexibility: customers can rotate api_key without touching embed code

### Decision: Why iframe instead of direct DOM injection?

**Options:**
1. Inject chat UI directly into customer DOM (risky XSS)
2. Use iframe (sandbox, isolated, safe)

**Choice:** iframe  
**Reason:** Sandbox security, style isolation, no conflicts with customer CSS

### Decision: Static CORS vs Dynamic CORS

**Dynamic CORS (previous spec):**
- Check origin on each request
- Per-client domain whitelist
- Adds complexity

**Static CORS (this spec):**
- Widget iframe hosted on our domain
- Requests come from our domain (no cross-origin)
- CORS not even involved
- Simpler, faster, more scalable

**Choice:** Static CORS  
**Reason:** Industry standard, simpler, no per-client config

---

## References

### Similar Products (Best Practices)
- **Intercom:** `<script id="intercom-frame-id">` with app_id
- **Drift:** `<script src="drift.com/embed.js?orgId=..."></script>`
- **Chatbase:** `<script src="cdn.chatbase.co/widget.js?chatbotId=..."></script>`
- **DocsBot:** `<script src="docsbot.com/chat.js?projectId=..."></script>`

All use: public script + public ID, zero domain configuration.

---

## Questions for Review

1. Should `public_id` have a different prefix? (ch_ vs cb_ vs something else)
2. Should embed script support customization (colors, position)? (Phase 2)
3. Should we track widget usage/analytics? (Phase 2)
4. Any security concerns with the design?
5. Should authentication still work for some endpoints?

---

**Status:** Ready for implementation  
**Owner:** [To be assigned]  
**Timeline:** 3-4 days (Phase 1)
