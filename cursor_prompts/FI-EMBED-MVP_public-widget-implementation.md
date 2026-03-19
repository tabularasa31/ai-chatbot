# FI-EMBED-MVP: Implement Public Script Widget (2-3 Days)

⚠️ **CRITICAL: Follow SETUP exactly. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/embed-mvp-public-script
```

**MUST DO (in exact order):**
1. `git checkout main` — switch to main
2. `git pull origin main` — fetch latest (do NOT skip!)
3. `git checkout -b feature/embed-mvp-public-script` — create NEW branch

**DO NOT reuse old branches or skip the pull step.**

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add public_id column to Client
- `backend/core/utils.py` — add generate_public_id function
- `backend/routes/public.py` — create (new file)
- `backend/routes/widget.py` — create (new file)
- `backend/static/embed.js` — create (new file)
- `backend/main.py` — register public_router and widget_router
- `frontend/app/widget/page.tsx` — create (new file)
- `frontend/components/ChatWidget.tsx` — create (new file)
- `frontend/app/dashboard/[clientId]/settings/page.tsx` — add EmbedSettings component
- `scripts/backfill_public_ids.py` — create (one-time backfill script)

**Do NOT touch:**
- Authentication/auth middleware
- Existing /chat endpoint (do NOT modify)
- Database migrations (manually edit after, don't use Alembic yet)
- Other models or routes
- Existing dashboard pages (only add embed section)

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** Customers can't embed Chat9 widget on their sites because of CORS. They have to contact support, provide domain, wait for manual setup.

**Solution:** Public script model (like Intercom, Drift, Chatbase):
1. Customer gets public_id (e.g., ch_abc123xyz)
2. Copy 1 line: `<script src="https://chat9.live/embed.js?clientId=ch_abc123xyz"></script>`
3. Paste on website (any domain)
4. Widget works immediately (no CORS needed)

**How it works:**
- embed.js is public script (loads from any domain)
- embed.js creates iframe pointing to /widget page on our domain
- iframe makes requests to /widget/chat from our domain (same-origin, no CORS)
- Result: works on any customer domain without setup

**Current state:**
- No public_id on Client model
- No /embed.js endpoint
- No /widget page
- No /widget/chat API endpoint
- No embed code in dashboard

**Why this fixes CORS:**
- Widget doesn't call API from customer domain (that would need CORS)
- Instead, iframe calls API from our domain (same-origin, no CORS needed)
- Simple, scalable, zero config for customers

---

## WHAT TO DO

### Step 1: Add public_id to Client Model

**File: `backend/models.py`**

Find the Client class definition and add this column:

```python
class Client(Base):
    __tablename__ = "client"
    
    # ... existing columns ...
    
    public_id: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: generate_public_id(),
    )
```

Import at top:
```python
from backend.core.utils import generate_public_id
```

---

### Step 2: Add generate_public_id Function

**File: `backend/core/utils.py`**

Add this function (if file doesn't exist, create it):

```python
import secrets
import string

def generate_public_id(prefix: str = "ch_") -> str:
    """
    Generate public client ID.
    Format: ch_<18-char alphanumeric>
    Example: ch_abc123xyz456789
    """
    chars = string.ascii_lowercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(18))
    return prefix + random_part
```

---

### Step 3: Backfill Existing Clients (One-Time Script)

**File: `scripts/backfill_public_ids.py`** (new file)

```python
#!/usr/bin/env python3
"""
One-time script to backfill public_id for existing clients.
Run this AFTER deploying the Client model change.
"""

import sys
sys.path.insert(0, '/path/to/ai-chatbot')

from backend.core.db import SessionLocal
from backend.models import Client
from backend.core.utils import generate_public_id

def main():
    db = SessionLocal()
    
    # Find clients without public_id
    clients = db.query(Client).filter(Client.public_id == None).all()
    
    if not clients:
        print("✅ All clients already have public_id")
        return
    
    print(f"Backfilling {len(clients)} clients...")
    
    for client in clients:
        client.public_id = generate_public_id()
        print(f"  {client.name} → {client.public_id}")
    
    db.commit()
    print(f"✅ Backfilled {len(clients)} clients")

if __name__ == "__main__":
    main()
```

**Run once after code is deployed:**
```bash
python scripts/backfill_public_ids.py
```

---

### Step 4: Create /embed.js Endpoint

**File: `backend/routes/public.py`** (new file)

```python
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pathlib import Path

public_router = APIRouter(prefix="", tags=["public"])

@public_router.get("/embed.js")
async def get_embed_script():
    """
    Public script for embedding Chat9 widget.
    No authentication required.
    
    Usage: <script src="https://chat9.live/embed.js?clientId=ch_xyz"></script>
    """
    script_path = Path(__file__).parent.parent / "static" / "embed.js"
    
    return FileResponse(
        path=script_path,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )
```

---

### Step 5: Create embed.js Script

**File: `backend/static/embed.js`** (new file)

```javascript
(function() {
  // Get script parameters from URL
  const currentScript = document.currentScript || (() => {
    const scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();
  
  const scriptSrc = currentScript.src;
  const url = new URL(scriptSrc);
  const clientId = url.searchParams.get('clientId');
  
  if (!clientId) {
    console.error('Chat9: clientId not found in script URL');
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
  iframe.src = `https://chat9.live/widget?clientId=${clientId}`;
  iframe.id = 'chat9-widget-iframe';
  iframe.style.cssText = `
    width: 400px;
    height: 600px;
    border: none;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  `;
  iframe.allow = "microphone; camera";
  
  container.appendChild(iframe);
  
  console.log('Chat9 Widget loaded', { clientId });
})();
```

---

### Step 6: Create /widget/chat API Endpoint

**File: `backend/routes/widget.py`** (new file)

```python
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from backend.core.db import get_db
from backend.models import Client, Chat, Message

widget_router = APIRouter(prefix="/widget", tags=["widget"])

@widget_router.post("/chat")
def widget_chat(
    message: str = Query(..., description="User message"),
    client_id: str = Query(..., description="Public client ID (ch_xyz)"),
    session_id: str = Query(None, description="Optional session ID"),
    db: Session = Depends(get_db),
):
    """
    PUBLIC endpoint for embedded widget.
    No authentication required (clientId = permission).
    """
    
    # 1. Validate client exists
    client = db.query(Client).filter(Client.public_id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    
    # 2. Validate client is active
    if not client.is_active:
        raise HTTPException(status_code=403, detail="Client is not active")
    
    # 3. Get or create chat session
    if session_id:
        chat = db.query(Chat).filter(Chat.session_id == session_id).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        chat = Chat(client_id=client.id)
        db.add(chat)
        db.commit()
        session_id = chat.session_id
    
    # 4. Call your existing chat service to process the message
    # IMPORTANT: Replace with your actual chat service implementation
    # This is a placeholder — you need to adapt to your code
    try:
        # Example: response_text = your_chat_service.process_message(client.id, message)
        response_text = "This is a placeholder response. Connect to your chat service."
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing message: {str(e)}")
    
    # 5. Save user message
    user_msg = Message(
        chat_id=chat.id,
        role="user",
        content=message,
    )
    db.add(user_msg)
    db.commit()
    
    return {
        "response": response_text,
        "session_id": session_id,
    }
```

---

### Step 7: Register Routers in main.py

**File: `backend/main.py`**

Add these imports at the top:

```python
from backend.routes.public import public_router
from backend.routes.widget import widget_router
```

Add these lines in app setup (after other router includes):

```python
app.include_router(public_router)
app.include_router(widget_router)
```

---

### Step 8: Create /widget Page

**File: `frontend/app/widget/page.tsx`** (new file)

```typescript
'use client';

import { useSearchParams } from 'next/navigation';
import { ChatWidget } from '@/components/ChatWidget';

export default function WidgetPage() {
  const searchParams = useSearchParams();
  const clientId = searchParams.get('clientId');
  
  if (!clientId) {
    return (
      <div style={{ padding: '20px', textAlign: 'center', color: '#666' }}>
        Invalid client ID
      </div>
    );
  }
  
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: '#fff' }}>
      <ChatWidget clientId={clientId} />
    </div>
  );
}
```

---

### Step 9: Create ChatWidget Component

**File: `frontend/components/ChatWidget.tsx`** (new file)

```typescript
'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface ChatWidgetProps {
  clientId: string;
}

export function ChatWidget({ clientId }: ChatWidgetProps) {
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSend = async () => {
    if (!input.trim()) return;

    setLoading(true);
    try {
      const res = await fetch(
        `/widget/chat?clientId=${clientId}&message=${encodeURIComponent(input)}&session_id=${sessionId}`,
        { method: 'POST' }
      );
      
      if (!res.ok) {
        throw new Error(`API error: ${res.status}`);
      }
      
      const data = await res.json();
      
      setMessages([
        ...messages,
        { role: 'user', content: input },
        { role: 'assistant', content: data.response }
      ]);
      setSessionId(data.session_id);
      setInput('');
    } catch (error) {
      console.error('Error:', error);
      setMessages([...messages, { role: 'error', content: 'Failed to send message' }]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '16px' }}>
      {/* Message list */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        marginBottom: '16px',
        border: '1px solid #e0e0e0',
        borderRadius: '8px',
        padding: '12px',
        background: '#fafafa',
      }}>
        {messages.length === 0 && (
          <div style={{ color: '#999', textAlign: 'center', paddingTop: '20px' }}>
            Start a conversation...
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} style={{ marginBottom: '12px', textAlign: msg.role === 'user' ? 'right' : 'left' }}>
            <div style={{
              background: msg.role === 'user' ? '#007bff' : msg.role === 'error' ? '#dc3545' : '#e9ecef',
              color: msg.role === 'user' ? 'white' : '#000',
              padding: '8px 12px',
              borderRadius: '8px',
              display: 'inline-block',
              maxWidth: '80%',
            }}>
              {msg.content}
            </div>
          </div>
        ))}
      </div>
      
      {/* Input area */}
      <div style={{ display: 'flex', gap: '8px' }}>
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && handleSend()}
          placeholder="Type a message..."
          disabled={loading}
        />
        <Button onClick={handleSend} disabled={loading}>
          {loading ? 'Sending...' : 'Send'}
        </Button>
      </div>
    </div>
  );
}
```

---

### Step 10: Add Embed Settings to Dashboard

**File: `frontend/app/dashboard/[clientId]/settings/page.tsx`**

Find the component that renders the settings page and add this section:

```typescript
'use client';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';

interface EmbedSettingsProps {
  publicId: string;
}

export function EmbedSettings({ publicId }: EmbedSettingsProps) {
  const embedCode = `<script src="https://chat9.live/embed.js?clientId=${publicId}"></script>`;
  
  const handleCopy = () => {
    navigator.clipboard.writeText(embedCode);
    toast.success('Embed code copied to clipboard!');
  };
  
  return (
    <Card style={{ padding: '20px', marginTop: '20px' }}>
      <h2>Embed on Your Website</h2>
      <p style={{ color: '#666', marginTop: '8px' }}>
        Copy and paste this code before closing &lt;/body&gt; tag on your website:
      </p>
      
      <pre style={{
        background: '#f5f5f5',
        padding: '12px',
        overflow: 'auto',
        margin: '12px 0',
        borderRadius: '4px',
        fontSize: '12px',
      }}>
        {embedCode}
      </pre>
      
      <Button onClick={handleCopy}>Copy Code</Button>
      
      <div style={{ marginTop: '20px', fontSize: '14px', lineHeight: '1.6' }}>
        <p><strong>How it works:</strong></p>
        <ol>
          <li>Copy the code above</li>
          <li>Paste it on your website (any domain)</li>
          <li>Widget appears automatically (bottom-right)</li>
          <li>Done! Works instantly</li>
        </ol>
      </div>
    </Card>
  );
}
```

Then use it in your settings page:
```typescript
<EmbedSettings publicId={client.public_id} />
```

---

## TESTING

Before pushing, verify all of this:

- [ ] Backend starts without errors (`npm run dev` or `python -m uvicorn backend.main:app`)
- [ ] Client model compiles (no TypeErrors)
- [ ] public_id column exists in Client table
- [ ] Backfill script runs successfully (all clients have public_id)
- [ ] `GET /embed.js` returns JavaScript file (check Content-Type: application/javascript)
- [ ] `/widget?clientId=ch_test123` loads and renders ChatWidget
- [ ] `/widget/chat?clientId=ch_test123&message=hello` accepts POST and returns JSON
- [ ] Dashboard shows embed code section
- [ ] Copy button works (code goes to clipboard)
- [ ] Test embed.js on localhost:3000 and another test domain
- [ ] No errors in browser console
- [ ] No CORS errors (requests should be same-origin from iframe)
- [ ] Session continuity works (send 2 messages, both appear in conversation)

**Test Script:**
```bash
# Test embed.js loads
curl -I http://localhost:8000/embed.js

# Test widget page
curl http://localhost:8000/widget?clientId=ch_test123

# Test API endpoint
curl -X POST "http://localhost:8000/widget/chat?clientId=ch_test123&message=hello"
```

---

## GIT PUSH

```bash
git add backend/models.py backend/core/utils.py backend/routes/public.py backend/routes/widget.py backend/static/embed.js backend/main.py frontend/app/widget/page.tsx frontend/components/ChatWidget.tsx frontend/app/dashboard/[clientId]/settings/page.tsx scripts/backfill_public_ids.py

git commit -m "feat: implement FI-EMBED-MVP — public script widget for zero-config embedding

- Add public_id to Client model (ch_<18char> format)
- Create /embed.js endpoint (public script)
- Create /widget page and ChatWidget component
- Create /widget/chat API endpoint (clientId-based, no auth)
- Add embed code display in dashboard
- Backfill script for existing clients

Fixes CORS issue: widget now loads in iframe from our domain (same-origin requests)"

git push origin feature/embed-mvp-public-script
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

### Known Limitations (Phase 2)
- No rate limiting (will add later)
- No mobile responsiveness (fixed 400×600)
- No error handling (timeout, onerror)
- No customization (colors, size, position)
- No CSP documentation

### Implementation Notes
- `/widget/chat` is a NEW PUBLIC endpoint (do NOT modify existing `/chat`)
- public_id is intentionally public (not secret, like a username)
- api_key stays private (used for server-to-server only)
- Requests from iframe are same-origin (no CORS needed)
- ChatWidget is a basic component — enhance UI in Phase 2

### CORS Resolution
- Old problem: Customer domain calls API → CORS blocks
- New solution: embed.js creates iframe pointing to our domain → iframe calls API from our domain (same-origin) → no CORS
- Result: Works on ANY domain without setup

### Files You're Creating
- `backend/routes/public.py` — new
- `backend/routes/widget.py` — new
- `backend/static/embed.js` — new
- `frontend/app/widget/page.tsx` — new
- `frontend/components/ChatWidget.tsx` — new
- `scripts/backfill_public_ids.py` — new

### Files You're Modifying
- `backend/models.py` — add 1 column + 1 import
- `backend/core/utils.py` — add 1 function
- `backend/main.py` — add 2 imports + 2 router includes
- `frontend/app/dashboard/[clientId]/settings/page.tsx` — add 1 component
