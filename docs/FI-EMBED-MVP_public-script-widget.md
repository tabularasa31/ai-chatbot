# FI-EMBED-MVP: Public Script Widget (2-3 Day Implementation)

**Status:** Ready for Implementation  
**Priority:** P1 (Urgent — blocks customer adoption)  
**Complexity:** Low (straightforward, minimal dependencies)  
**Effort:** 2–3 days (Cursor)  
**Owner:** Elle (Elina)  
**Date:** 2026-03-19

---

## Executive Summary

**Problem:** Customers can't embed Chat9 widget on their sites without manual CORS setup.

**Solution:** Public script (`/embed.js`) that creates iframe on customer domain. Iframe loads from our domain (no CORS needed).

**Result:** Customers paste 1 line of code → widget works immediately. Zero config.

**Scope (MVP Only):**
- ✅ Add public_id to Client
- ✅ Create /embed.js endpoint
- ✅ Create /widget page with chat
- ✅ Create /widget/chat API endpoint
- ✅ Show embed code in dashboard

**Out of Scope (Phase 2):**
- Rate limiting (add later)
- Customization (colors, size, position)
- Analytics, versioning, CSP docs
- Advanced error handling, mobile responsiveness

---

## Task Breakdown (2–3 Days)

### Day 1: Backend Setup (1 day)

#### Task 1.1: Add public_id to Client Model

**File: `backend/models.py`**

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

**File: `backend/core/utils.py` (add function)**

```python
import secrets
import string

def generate_public_id(prefix: str = "ch_") -> str:
    """Generate public client ID (e.g., ch_abc123xyz456789)"""
    chars = string.ascii_lowercase + string.digits
    return prefix + ''.join(secrets.choice(chars) for _ in range(18))
```

**Backfill existing clients (Python script, one-time):**

```python
# Script: scripts/backfill_public_ids.py
from backend.core.db import SessionLocal
from backend.models import Client
from backend.core.utils import generate_public_id

db = SessionLocal()
clients = db.query(Client).filter(Client.public_id == None).all()
for client in clients:
    client.public_id = generate_public_id()
db.commit()
print(f"Backfilled {len(clients)} clients")
```

**Run once, commit the changes.**

---

#### Task 1.2: Create /embed.js Endpoint

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
    Usage: <script src="https://chat9.live/embed.js?clientId=ch_xyz"></script>
    """
    script_path = Path(__file__).parent.parent / "static" / "embed.js"
    
    return FileResponse(
        path=script_path,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
    )
```

**File: `backend/main.py`** (register router)

```python
from backend.routes.public import public_router

# Add this line in app setup:
app.include_router(public_router)
```

---

#### Task 1.3: Create /widget/chat API Endpoint

**File: `backend/routes/widget.py`** (new file)

```python
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from backend.core.db import get_db
from backend.models import Client, Chat, Message

widget_router = APIRouter(prefix="/widget", tags=["widget"])

@widget_router.post("/chat")
def widget_chat(
    message: str = Query(...),
    client_id: str = Query(..., description="Public client ID (ch_xyz)"),
    session_id: str = Query(None),
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
    
    # 3. Get or create session
    if not session_id:
        chat = Chat(client_id=client.id)
        db.add(chat)
        db.commit()
        session_id = chat.session_id
    else:
        chat = db.query(Chat).filter(Chat.session_id == session_id).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Session not found")
    
    # 4. Call existing chat logic (your current /chat implementation)
    # This is pseudocode — adapt to your actual chat service
    response_text = await your_chat_service.process_message(
        client_id=client.id,
        message=message,
        session_id=session_id,
    )
    
    # 5. Save message
    new_message = Message(
        chat_id=chat.id,
        role="user",
        content=message,
    )
    db.add(new_message)
    db.commit()
    
    return {
        "response": response_text,
        "session_id": session_id,
    }
```

**File: `backend/main.py`** (register router)

```python
from backend.routes.widget import widget_router

# Add this line:
app.include_router(widget_router)
```

---

### Day 2: Frontend (1 day)

#### Task 2.1: Create embed.js Script

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
  iframe.style.cssText = `
    width: 400px;
    height: 600px;
    border: none;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  `;
  iframe.allow = "microphone; camera";
  
  container.appendChild(iframe);
  
  console.log('Chat9 Widget loaded', { clientId });
})();
```

---

#### Task 2.2: Create /widget Page

**File: `frontend/app/widget/page.tsx`** (new file)

```typescript
'use client';

import { useSearchParams } from 'next/navigation';
import { ChatWidget } from '@/components/ChatWidget';

export default function WidgetPage() {
  const searchParams = useSearchParams();
  const clientId = searchParams.get('clientId');
  
  if (!clientId) {
    return <div>Invalid client ID</div>;
  }
  
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <ChatWidget clientId={clientId} />
    </div>
  );
}
```

---

#### Task 2.3: Adapt ChatWidget Component

**File: `frontend/components/ChatWidget.tsx`** (modify existing or create)

```typescript
'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface ChatWidgetProps {
  clientId: string;
}

export function ChatWidget({ clientId }: ChatWidgetProps) {
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSend = async () => {
    if (!input.trim()) return;

    setLoading(true);
    try {
      const res = await fetch(`/widget/chat?clientId=${clientId}&message=${encodeURIComponent(input)}&session_id=${sessionId}`);
      const data = await res.json();
      
      setMessages([...messages, { role: 'user', content: input }, { role: 'assistant', content: data.response }]);
      setSessionId(data.session_id);
      setInput('');
    } catch (error) {
      console.error('Error:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '16px' }}>
      <div style={{ flex: 1, overflowY: 'auto', marginBottom: '16px', border: '1px solid #ccc', padding: '8px' }}>
        {messages.map((msg, i) => (
          <div key={i} style={{ marginBottom: '8px', textAlign: msg.role === 'user' ? 'right' : 'left' }}>
            <span style={{ background: msg.role === 'user' ? '#007bff' : '#f0f0f0', padding: '8px 12px', borderRadius: '8px', display: 'inline-block', color: msg.role === 'user' ? 'white' : 'black' }}>
              {msg.content}
            </span>
          </div>
        ))}
      </div>
      
      <div style={{ display: 'flex', gap: '8px' }}>
        <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Type a message..." />
        <Button onClick={handleSend} disabled={loading}>{loading ? 'Sending...' : 'Send'}</Button>
      </div>
    </div>
  );
}
```

---

### Day 3: Dashboard & Testing (0.5-1 day)

#### Task 3.1: Add Embed Code to Dashboard

**File: `frontend/app/dashboard/[clientId]/settings/page.tsx`** (add section)

```typescript
'use client';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';

export function EmbedSettings({ publicId }: { publicId: string }) {
  const embedCode = `<script src="https://chat9.live/embed.js?clientId=${publicId}"></script>`;
  
  const handleCopy = () => {
    navigator.clipboard.writeText(embedCode);
    toast.success('Embed code copied!');
  };
  
  return (
    <Card>
      <h2>Embed on Your Website</h2>
      <p>Copy and paste this code before closing &lt;/body&gt; tag:</p>
      
      <pre style={{ background: '#f5f5f5', padding: '12px', overflow: 'auto', margin: '12px 0' }}>
        {embedCode}
      </pre>
      
      <Button onClick={handleCopy}>Copy Code</Button>
      
      <div style={{ marginTop: '20px', fontSize: '14px', color: '#666' }}>
        <p><strong>How it works:</strong></p>
        <ol>
          <li>Copy the code above</li>
          <li>Paste it on your website (any domain)</li>
          <li>Widget appears automatically (bottom-right)</li>
          <li>Done!</li>
        </ol>
      </div>
    </Card>
  );
}
```

---

#### Task 3.2: Testing Checklist

- [ ] Backend starts without errors
- [ ] Client model has public_id (unique, indexed)
- [ ] /embed.js returns JavaScript (Content-Type correct)
- [ ] /widget?clientId=... loads and renders
- [ ] /widget/chat accepts requests and returns messages
- [ ] Session continuity works (same conversation across messages)
- [ ] Dashboard shows embed code
- [ ] Copy button works
- [ ] Test on multiple domains (localhost:3000, 127.0.0.1, test.example.com)
- [ ] iframe doesn't break customer's page layout

---

## Acceptance Criteria (MVP)

- [ ] public_id column added and backfilled
- [ ] /embed.js endpoint works (public, no auth)
- [ ] /widget page renders chat UI
- [ ] /widget/chat API endpoint works (clientId-based, public)
- [ ] Dashboard displays embed code
- [ ] Customers can copy & paste code
- [ ] Widget works on test domains (no CORS issues)
- [ ] Session continuity works (same conversation)
- [ ] No breaking changes to existing endpoints
- [ ] Basic error messages shown (invalid clientId → 404)

---

## Known Limitations (Phase 2)

- ❌ No rate limiting (add in Phase 2)
- ❌ No customization (colors, size, position)
- ❌ No mobile responsiveness (fixed 400×600)
- ❌ No error handling (timeout, onerror)
- ❌ No CSP documentation
- ❌ No analytics tracking
- ❌ No versioning (embed.js always latest)

---

## Files to Create/Modify

### New Files
- `backend/routes/public.py`
- `backend/routes/widget.py`
- `backend/static/embed.js`
- `backend/core/utils.py` (add generate_public_id)
- `frontend/app/widget/page.tsx`
- `frontend/components/ChatWidget.tsx`
- `scripts/backfill_public_ids.py` (one-time script)

### Modified Files
- `backend/models.py` (add public_id column)
- `backend/main.py` (register routers)
- `frontend/app/dashboard/[clientId]/settings/page.tsx` (add embed section)

---

## Deployment Order

1. Add public_id to Client model (code + run backfill script)
2. Deploy backend (public_router + widget_router)
3. Deploy embed.js static file
4. Deploy frontend (widget page + dashboard changes)
5. Test on production

---

## Success Metrics

- ✅ Customers can embed with 1 line of code
- ✅ Widget works on any domain (no CORS setup needed)
- ✅ Time to embed: < 1 minute
- ✅ No support tickets about domain registration

---

**Ready to start!** 🚀
