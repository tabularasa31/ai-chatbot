# CURSOR PROMPT: [FI-ESC] L2 Escalation Tickets — v1

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-esc-escalation-tickets
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/models.py` — add EscalationTicket model
- `backend/migrations/versions/` — new Alembic migration
- `backend/escalation/` — new module: `__init__.py`, `service.py`, `routes.py`, `schemas.py`
- `backend/chat/service.py` — detect escalation triggers, call escalation service
- `backend/chat/routes.py` — expose escalation endpoint
- `backend/main.py` — register escalation router
- `frontend/app/(app)/escalations/page.tsx` — new Escalations inbox page
- `frontend/components/ChatWidget.tsx` — add "Talk to support" button + ticket confirmation flow
- `frontend/lib/api.ts` — add escalation API calls
- `tests/` — add tests for escalation logic

**Do NOT touch:**
- `backend/auth/`
- `backend/documents/`
- `backend/embeddings/`
- `backend/search/`

---

## CONTEXT

Currently, when the bot can't answer, it returns "I don't have information about this." — and stops there. The user is left with nothing: no next step, no way to reach a human, no confirmation their question was heard.

This is worse than no bot at all. Every unanswered question is a missed feedback signal for documentation improvement.

This feature makes the bot recognize its own failure and act: create a structured escalation ticket automatically, notify the user with a ticket number and response time, and surface the ticket in the tenant's dashboard.

**v1 scope (this prompt):**
- Internal tickets only (stored in DB, visible in dashboard + email notification to tenant)
- 4 escalation triggers: low similarity, no documents, manual request, answer rejection
- User sees ticket number + expected response time in chat
- Tenant sees tickets in /escalations dashboard page
- No external helpdesk integration in v1 (Zendesk/Intercom = v2)

---

## WHAT TO DO

### 1. EscalationTicket model (`backend/models.py`)

```python
class EscalationTrigger(str, enum.Enum):
    low_similarity = "low_similarity"    # T-1: best chunk score < threshold
    no_documents = "no_documents"        # T-2: no chunks found at all
    user_request = "user_request"        # T-3: user explicitly asked for human
    answer_rejected = "answer_rejected"  # T-4: user thumbed down or said "not helpful"

class EscalationPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class EscalationStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"

class EscalationTicket(Base):
    __tablename__ = "escalation_tickets"
    
    id: UUID (primary key)
    client_id: UUID (FK → clients.id, index)
    ticket_number: str (unique, e.g. "ESC-0001")  # sequential per client
    
    # Question & context
    primary_question: str  # the question that triggered escalation
    conversation_summary: str | None  # last 5 turns as text
    
    # Retrieval context (what the bot tried)
    trigger: EscalationTrigger
    best_similarity_score: float | None  # best chunk score at trigger time
    retrieved_chunks_preview: JSON | None  # [{document_id, score, preview: first 200 chars}]
    
    # User context (from KYC if available, anonymous otherwise)
    user_id: str | None  # from KYC session
    user_email: str | None  # for L2 agent to reply
    user_name: str | None
    plan_tier: str | None
    user_note: str | None  # optional note added by user before submission
    
    # Status & resolution
    priority: EscalationPriority (default: medium)
    status: EscalationStatus (default: open)
    resolution_text: str | None  # L2 agent fills this on resolve
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    
    # Chat reference
    chat_id: UUID | None (FK → chats.id)
    session_id: UUID | None
```

### 2. Alembic migration

Create migration for `escalation_tickets` table with indexes on `client_id`, `status`, `created_at`.

### 3. Escalation module (`backend/escalation/`)

**`service.py`** — core logic:

```python
ESCALATION_THRESHOLD = 0.45  # configurable, default from spec

def should_escalate(
    best_similarity_score: float | None,
    chunk_count: int,
    trigger_override: EscalationTrigger | None = None,
) -> tuple[bool, EscalationTrigger | None]:
    """
    Evaluate escalation triggers in order (T-1 through T-4).
    Returns (should_escalate, trigger_type).
    
    T-1: best_similarity_score < ESCALATION_THRESHOLD (or is None with chunks)
    T-2: chunk_count == 0 (no documents found)
    T-3: trigger_override == user_request
    T-4: trigger_override == answer_rejected
    """

def detect_human_request(message: str) -> bool:
    """
    Detect if the user is explicitly requesting a human agent.
    
    Patterns (case-insensitive):
    - "talk to", "speak to", "connect me to", "get me", "I want a human/agent/person/support"
    - "поговорить с", "соедини с", "хочу с человеком"
    - "this is useless", "not helpful" + ("human" or "support" or "agent")
    """

def compute_priority(
    trigger: EscalationTrigger,
    plan_tier: str | None,
    user_context: dict | None,
) -> EscalationPriority:
    """
    Priority rules:
    - T-3 (user_request) + enterprise/pro plan → critical
    - T-3 (user_request) → high
    - T-1/T-2 + enterprise plan → high
    - T-4 (answer_rejected) → medium
    - Default → medium
    """

def generate_ticket_number(client_id: UUID, db: Session) -> str:
    """
    Sequential per client: ESC-0001, ESC-0002, etc.
    Query max ticket_number for client → increment.
    Thread-safe: use SELECT FOR UPDATE or handle IntegrityError.
    """

def create_escalation_ticket(
    client_id: UUID,
    primary_question: str,
    trigger: EscalationTrigger,
    db: Session,
    *,
    chat_id: UUID | None = None,
    session_id: UUID | None = None,
    best_similarity_score: float | None = None,
    retrieved_chunks: list | None = None,
    conversation_turns: list | None = None,
    user_context: dict | None = None,
    user_note: str | None = None,
) -> EscalationTicket:
    """
    Create and store an escalation ticket.
    Send email notification to tenant (via existing Brevo service).
    Return the created ticket.
    """

def format_user_message(ticket: EscalationTicket, sla_hours: int = 24) -> str:
    """
    Format the message shown to the user in chat after ticket creation.
    
    Template:
    "I wasn't able to find an answer to your question. I've created a support ticket
    so our team can help you directly.
    
    Ticket: #{ticket_number}
    Expected response: within {sla_hours} hours{email_line}
    
    Is there anything you'd like to add for the support team? (optional — reply or skip)"
    
    {email_line} = "\nWe'll follow up at {user_email}" if user_email is present, else ""
    """

def resolve_ticket(
    ticket_id: UUID,
    client_id: UUID,
    resolution_text: str,
    db: Session,
) -> EscalationTicket:
    """Mark ticket as resolved, store resolution, set resolved_at."""
```

**`routes.py`**:

```
GET  /escalations              → list all tickets for client (filter: status, priority, date range)
GET  /escalations/{ticket_id}  → get single ticket
POST /escalations/{ticket_id}/resolve  → resolve ticket with resolution_text
POST /chat/{session_id}/escalate       → manual escalation trigger (user clicks "Talk to support")
```

**`schemas.py`**: Pydantic schemas for all request/response types.

### 4. Integrate into chat pipeline (`backend/chat/service.py`)

In `process_chat_message()`, after step 3 (generate answer):

```python
# After retrieving chunks and before generating answer:

# Check T-3: did user ask for human? (check before RAG)
if detect_human_request(question):
    ticket = create_escalation_ticket(..., trigger=EscalationTrigger.user_request, ...)
    return (format_user_message(ticket), [], 0)

# After retrieval:
escalate, trigger = should_escalate(best_score, len(chunk_texts))
if escalate:
    # Still generate a best-effort answer (for T-1) but also create ticket
    answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)
    ticket = create_escalation_ticket(..., trigger=trigger, ...)
    # Append escalation notice to answer
    answer = answer + "\n\n" + format_user_message(ticket)
    return (answer, document_ids, tokens_used)
```

Note: T-4 (answer rejection) is handled via a separate endpoint (`POST /chat/{session_id}/escalate`) called when the user thumbs-down. Not in-band in the chat pipeline.

### 5. Email notification to tenant

When a ticket is created, send an email to the tenant's registered email using the existing Brevo service:

```
Subject: [Chat9] New support ticket #{ticket_number} — {primary_question[:60]}

A user question couldn't be answered by your bot.

Ticket: {ticket_number}
Question: {primary_question}
Trigger: {trigger}
Priority: {priority}
User: {user_email or "anonymous"}

View in dashboard: https://getchat9.live/escalations/{ticket_id}
```

### 6. Frontend: Escalations page (`frontend/app/(app)/escalations/page.tsx`)

Show a table of tickets:
- Columns: Ticket #, Question (truncated), Priority, Status, User (email if available), Created, Actions
- Filter by status (open / in_progress / resolved)
- Click on row → expand to show full context:
  - Primary question
  - Conversation summary
  - Retrieved chunks preview (document name + score)
  - User note (if any)
  - Resolution text field + "Mark as resolved" button

### 7. Widget: "Talk to support" button

In `frontend/components/ChatWidget.tsx`, add a small "Talk to support" link below the message input (always visible).

On click → send a message "I'd like to talk to a human agent" which triggers T-3 in the backend.

When a ticket confirmation message arrives from the bot (contains "Ticket: #ESC-"):
- Show a persistent banner in the widget: "Ticket #ESC-XXXX created — our team will follow up"

### 8. API client (`frontend/lib/api.ts`)

```typescript
escalations: {
  list: (params?: {status?: string}) => apiRequest('GET', '/escalations', params),
  get: (id: string) => apiRequest('GET', `/escalations/${id}`),
  resolve: (id: string, resolution: string) => apiRequest('POST', `/escalations/${id}/resolve`, {resolution_text: resolution}),
  manualEscalate: (sessionId: string, note?: string) => apiRequest('POST', `/chat/${sessionId}/escalate`, {user_note: note}),
}
```

Add "Escalations" link to the dashboard navigation.

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New test: `should_escalate()` — T-1 fires when score < 0.45
- [ ] New test: `should_escalate()` — T-2 fires when chunk_count == 0
- [ ] New test: `detect_human_request()` — detects "talk to a human", "connect me to support"
- [ ] New test: `compute_priority()` — T-3 + pro plan → critical
- [ ] New test: ticket creation stores correct fields
- [ ] New test: `generate_ticket_number()` — sequential, unique per client
- [ ] New test: chat pipeline creates ticket when similarity < threshold
- [ ] New test: chat pipeline detects human request, returns ticket message without RAG
- [ ] New test: GET /escalations — returns only tickets for authenticated client (tenant isolation)
- [ ] New test: resolve endpoint sets status=resolved, resolved_at != null
- [ ] Manual test: ask unanswerable question → ticket appears in /escalations
- [ ] Manual test: click "Talk to support" → ticket created immediately

---

## GIT PUSH

```bash
git add backend/models.py backend/escalation/ backend/chat/ backend/main.py \
        backend/migrations/versions/ \
        frontend/app/(app)/escalations/ frontend/components/ChatWidget.tsx \
        frontend/lib/api.ts tests/
git commit -m "feat: add L2 escalation tickets — auto-trigger + dashboard inbox (FI-ESC)"
git push origin feature/fi-esc-escalation-tickets
```

---

## NOTES

- **v1 is internal only** — tickets stored in DB, visible in dashboard, email notification. No Zendesk/Intercom (v2).
- **T-4 (answer rejection)** — implement via separate endpoint, not in main chat pipeline. Called when user thumbs-down.
- **Don't break existing chat flow** — escalation logic wraps around the existing pipeline. If escalation service fails, chat still returns an answer (wrap create_escalation_ticket in try/except).
- **Ticket number format**: `ESC-{padded_sequential}` e.g. ESC-0001. Use DB-level sequence or row count per client.
- **Email notification** is best-effort — if Brevo fails, ticket is still created. Log the email failure.
- **SLA**: hardcode 24 hours for v1. Configurable per tenant is a v2 feature.
- Dependency: FI-KYC should be done first so tickets can include user email. But FI-ESC works without KYC — user_email will just be null (anonymous).

---

## PR DESCRIPTION

```markdown
## Summary
Adds automatic L2 escalation tickets. When the bot can't answer (low similarity, no docs, user request, answer rejection), it creates a structured ticket, notifies the user with a ticket number and SLA, and surfaces the ticket in a new /escalations dashboard page. Tenants receive an email notification per ticket.

## Changes
- `backend/models.py` — EscalationTicket model with trigger, priority, status, user context
- `backend/migrations/versions/XXX` — escalation_tickets table
- `backend/escalation/` — new module: service + routes + schemas
- `backend/chat/service.py` — T-1/T-2/T-3 trigger detection in chat pipeline
- `backend/main.py` — register escalation router
- `frontend/app/(app)/escalations/page.tsx` — escalations inbox with resolve workflow
- `frontend/components/ChatWidget.tsx` — "Talk to support" button + ticket banner
- `frontend/lib/api.ts` — escalation API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: unanswerable question → ticket in /escalations + email received
- [ ] Manual: "Talk to support" button → ticket created immediately

## Notes
v1: internal tickets only (DB + email). Zendesk/Intercom delivery in v2.
Works standalone — does not require FI-KYC (user_email will be null for anonymous sessions).
```
