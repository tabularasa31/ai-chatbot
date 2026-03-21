# CURSOR PROMPT: [FI-ESC] L2 Escalation Tickets — v2

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
- `backend/models.py` — add EscalationTicket model + Chat flag columns
- `backend/migrations/versions/` — new Alembic migrations
- `backend/escalation/` — new module: `__init__.py`, `service.py`, `routes.py`, `schemas.py`
- `backend/escalation/openai_escalation.py` — OpenAI structured completion for escalation UX
- `backend/chat/service.py` — detect escalation triggers, chat state machine, call escalation service
- `backend/chat/routes.py` — expose manual escalation endpoint
- `backend/main.py` — register escalation router
- `frontend/app/(app)/escalations/page.tsx` — new Escalations inbox page
- `frontend/components/ChatWidget.tsx` — add "Talk to support" button + ticket banner + closed state
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
- **User-facing escalation wording is produced by OpenAI** (same stack as the main bot): the model sees full chat context and writes the reply in the user's language; the backend only orchestrates (tickets, flags, parsing) and relays the model output to the client (see Escalation UX via OpenAI below)
- Tenant sees tickets in /escalations dashboard page
- No external helpdesk integration in v1 (Zendesk/Intercom = v2)

---

## Escalation UX via OpenAI (language = user's language)

**Why not static "locale tables"?** The product requirement is: the user always reads answers in their own language, including every escalation step. That wording should come from the same OpenAI model you already use for chat, so tone and language stay aligned. The backend does not author user-visible paragraphs from translation files for escalation v1.

### Division of responsibility

| Layer | Responsibility |
|-------|---------------|
| Python (orchestration) | Triggers (T-1…T-4), ticket CRUD, `parse_contact_email`, chat flags (`escalation_*`, `ended_at`), tenant email (Brevo), appending machine token `[[escalation_ticket:{ticket_number}]]` if the model omitted it |
| OpenAI | All natural-language replies for escalation phases: handoff, ask-email, invalid-email retry, follow-up question, clarify, ack, goodbye, "chat closed" — in the language of the conversation |

### Context you MUST send to the model on every escalation completion

Build messages (system + developer/tool-style instruction + full recent thread as user/assistant turns) including at least:

1. **Conversation history** — same window you use for normal chat (or last N turns), so the model infers language, formality (ты/вы), and domain.
2. **Structured fact block** (developer message or JSON attachment) — non-negotiable facts only, e.g. `ticket_number`, `sla_hours`, `user_email` (if known), `trigger`, `phase` (enum below), `clarify_round` (0 or 1), whether this is after successful email capture, etc.
3. **KYC / embed user_context** if present (plan, name, locale hint — model may use locale hint but must still follow actual user messages).

### System instructions (essence, implement in English in code)

- You are the same assistant as in this chat. Reply only in the same language the user has been using; match their formality.
- You must communicate: request passed to human support; they will reply by email at `{email}` when known; ask for email when unknown; ticket id and approximate SLA — do not promise exact times.
- End with an offer to help further in chat ("anything else?") when phase requires it.
- Do not invent ticket numbers, emails, or SLA — use only values from the fact block.
- Keep answers concise and calm.

### Phases (`EscalationPhase` enum for the fact block)

`handoff_email_known` | `handoff_ask_email` | `email_parse_failed` | `followup_awaiting_yes_no` | `chat_already_closed`

(goodbye / "anything else?" / clarify да-нет live inside `message_to_user`, not separate phases)

### Structured model output (required for phases that move state)

Use JSON schema / structured outputs (or parse JSON reliably) so one completion returns:

- `message_to_user` (string) — shown in the widget as the assistant message
- `followup_decision` — `yes` | `no` | `unclear` | `null`
  - Only when `phase == followup_awaiting_yes_no`: model must set yes/no/unclear from the latest user message + thread context.
  - Python state machine: `no` → `ended_at` + relay `message_to_user` (goodbye); `yes` → clear `escalation_followup_pending` + relay `message_to_user` (no RAG on that turn); first `unclear` → relay `message_to_user` (model should politely re-ask да/нет) + set clarify flag; second `unclear` → code forces `yes`.

This avoids maintaining per-locale keyword lists for да/нет/yes/no.

### Relay

Persist `message_to_user` as the assistant `Message` in DB and return it to the client unchanged (after ensuring the `[[escalation_ticket:{ticket_number}]]` line is present once when a ticket exists — append in code if missing).

### Failures

If OpenAI errors: short English fallback string is acceptable for v1 or retry once; log and never leave the user with no body. Optional tiny `FALLBACK_EN_*` constants only for outages — not the primary UX.

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

class EscalationPhase(str, enum.Enum):
    handoff_email_known = "handoff_email_known"
    handoff_ask_email = "handoff_ask_email"
    email_parse_failed = "email_parse_failed"
    followup_awaiting_yes_no = "followup_awaiting_yes_no"
    chat_already_closed = "chat_already_closed"

class EscalationTicket(Base):
    __tablename__ = "escalation_tickets"

    id: UUID (primary key)
    client_id: UUID (FK → clients.id, index)
    ticket_number: str (unique, e.g. "ESC-0001")  # sequential per client

    # Question & context
    primary_question: str
    conversation_summary: str | None  # last 5 turns as text

    # Retrieval context
    trigger: EscalationTrigger
    best_similarity_score: float | None
    retrieved_chunks_preview: JSON | None  # [{document_id, score, preview: first 200 chars}]

    # User context (from KYC if available, anonymous otherwise)
    user_id: str | None
    user_email: str | None
    user_name: str | None
    plan_tier: str | None
    user_note: str | None

    # Status & resolution
    priority: EscalationPriority (default: medium)
    status: EscalationStatus (default: open)
    resolution_text: str | None

    # Timestamps
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None

    # Chat reference
    chat_id: UUID | None (FK → chats.id)
    session_id: UUID | None
```

**Extend existing `Chat` model:**

```python
# Add to Chat:
escalation_awaiting_ticket_id: UUID | None  # FK → escalation_tickets.id, nullable, ON DELETE SET NULL, indexed
# When non-null: next user message is the reply to ask-email prompt (no RAG); parsing in code.

escalation_followup_pending: bool  # default false, server_default=false
# When true: next user message classified via OpenAI structured output (followup_decision).

ended_at: datetime | None  # nullable. When set, chat is closed.
# Further user messages get short assistant reply (chat_already_closed phase); widget disables input.
```

**Flag check order in chat pipeline:** `ended_at` → `escalation_awaiting_ticket_id` → `escalation_followup_pending` → normal RAG.

### 2. Alembic migration

Two migrations (or one combined):
1. `escalation_tickets` table with indexes on `client_id`, `status`, `created_at`
2. Add `escalation_awaiting_ticket_id`, `escalation_followup_pending`, `ended_at` to `chats`

### 3. Escalation module (`backend/escalation/`)

**`service.py`:**

```python
ESCALATION_THRESHOLD = 0.45

def should_escalate(
    best_similarity_score: float | None,
    chunk_count: int,
    trigger_override: EscalationTrigger | None = None,
) -> tuple[bool, EscalationTrigger | None]:
    """T-1: score < threshold. T-2: chunk_count == 0. T-3/T-4: trigger_override."""

def detect_human_request(message: str) -> bool:
    """
    Patterns (case-insensitive):
    - "talk to", "speak to", "connect me to", "get me", "I want a human/agent/person/support"
    - "поговорить с", "соедини с", "хочу с человеком"
    - "this is useless"/"not helpful" + ("human" or "support" or "agent")
    """

def compute_priority(
    trigger: EscalationTrigger,
    plan_tier: str | None,
    user_context: dict | None,
) -> EscalationPriority:
    """
    T-3 + enterprise/pro → critical
    T-3 → high
    T-1/T-2 + enterprise → high
    T-4 → medium
    Default → medium
    """

def generate_ticket_number(client_id: UUID, db: Session) -> str:
    """Sequential per client: ESC-0001, ESC-0002. Thread-safe."""

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
    """Create ticket, send email notification to tenant, return ticket."""

def parse_contact_email(message: str) -> str | None:
    """Extract single email from user reply. Return None if unclear or multiple."""

def apply_collected_contact_email(
    ticket_id: UUID,
    chat_id: UUID,
    email: str,
    db: Session,
) -> None:
    """
    - Set EscalationTicket.user_email = email
    - Merge email into Chat.user_context if applicable
    - Clear chat.escalation_awaiting_ticket_id
    - Set chat.escalation_followup_pending = True
    """

def resolve_ticket(
    ticket_id: UUID,
    client_id: UUID,
    resolution_text: str,
    db: Session,
) -> EscalationTicket:
    """Mark resolved, store resolution, set resolved_at."""

def get_latest_escalation_ticket_for_chat(chat_id: UUID, db: Session) -> EscalationTicket | None:
    """Most recent escalation ticket linked to this chat."""

def fact_from_ticket(ticket: EscalationTicket, sla_hours: int = 24) -> dict:
    """Serialize ticket_number, sla_hours, user_email, trigger, etc. for the model fact block."""

def build_chat_messages_for_openai(chat: Chat, current_user_text: str) -> list[dict]:
    """Same roles/content as main chat completion; includes current user message."""

def _escalation_clarify_already_asked(chat: Chat) -> bool:
    """True if chat.user_context already has escalation_followup_clarify."""

def _set_escalation_clarify_flag(chat: Chat) -> None:
    """Merge escalation_followup_clarify: true into user_context."""

def _clear_escalation_clarify_flag(chat: Chat) -> None:
    """Remove escalation_followup_clarify from user_context."""
```

**`openai_escalation.py`:**

```python
class EscalationLlmResult(BaseModel):
    message_to_user: str
    followup_decision: Literal["yes", "no", "unclear"] | None = None
    tokens_used: int = 0

def complete_escalation_openai_turn(
    *,
    phase: EscalationPhase,
    chat_messages: list[dict],  # role + content; same window as main chat
    fact_json: dict,            # ticket_number, sla_hours, user_email, trigger, clarify_round, ...
    latest_user_text: str | None,
    api_key: str,
    model: str | None = None,
) -> EscalationLlmResult:
    """
    One OpenAI structured completion.
    
    System/developer text in English is fine; model output must match the user's language in chat_messages.
    After return: ensure [[escalation_ticket:{ticket_number}]] is present when a ticket exists
    (append in Python if missing).
    
    On OpenAI error: return EscalationLlmResult with short English fallback, log exception.
    """

def handoff_user_message_via_openai(
    ticket: EscalationTicket,
    chat: Chat,
    *,
    phase: EscalationPhase,
    api_key: str,
) -> str:
    """Build chat_messages + fact_json from DB, call complete_escalation_openai_turn, return message_to_user."""
```

**`routes.py`:**

```
GET  /escalations                      → list tickets (filter: status, priority, date range)
GET  /escalations/{ticket_id}          → get single ticket
POST /escalations/{ticket_id}/resolve  → resolve with resolution_text
POST /chat/{session_id}/escalate       → manual escalation (user clicks "Talk to support")
```

### 4. Integrate into chat pipeline (`backend/chat/service.py`)

Build `msgs` once per request (same message list used for main RAG):

```python
msgs = build_chat_messages_for_openai(chat, current_user_text=question)

# --- Chat already closed ---
if chat.ended_at is not None:
    out = complete_escalation_openai_turn(
        phase=EscalationPhase.chat_already_closed,
        chat_messages=msgs, fact_json={},
        latest_user_text=question, api_key=api_key,
    )
    return (out.message_to_user, [], out.tokens_used)

# --- Awaiting contact email (parse in code; wording from OpenAI) ---
if chat.escalation_awaiting_ticket_id:
    email = parse_contact_email(question)
    ticket = db.get(EscalationTicket, chat.escalation_awaiting_ticket_id)
    if email:
        apply_collected_contact_email(ticket.id, chat.id, email, db)
        db.refresh(ticket)
        out = complete_escalation_openai_turn(
            phase=EscalationPhase.handoff_email_known,
            chat_messages=msgs, fact_json=fact_from_ticket(ticket),
            latest_user_text=question, api_key=api_key,
        )
        chat.escalation_followup_pending = True
        return (out.message_to_user, [], out.tokens_used)
    out = complete_escalation_openai_turn(
        phase=EscalationPhase.email_parse_failed,
        chat_messages=msgs, fact_json=fact_from_ticket(ticket),
        latest_user_text=question, api_key=api_key,
    )
    return (out.message_to_user, [], out.tokens_used)

# --- Awaiting follow-up (yes/no) ---
if chat.escalation_followup_pending:
    ticket = get_latest_escalation_ticket_for_chat(chat.id, db)
    out = complete_escalation_openai_turn(
        phase=EscalationPhase.followup_awaiting_yes_no,
        chat_messages=msgs,
        fact_json={
            **fact_from_ticket(ticket),
            "clarify_round": 1 if _escalation_clarify_already_asked(chat) else 0,
        },
        latest_user_text=question, api_key=api_key,
    )
    decision = out.followup_decision or "unclear"
    if decision == "unclear" and _escalation_clarify_already_asked(chat):
        decision = "yes"  # second unclear → continue chat
    if decision == "yes":
        chat.escalation_followup_pending = False
        _clear_escalation_clarify_flag(chat)
        return (out.message_to_user, [], out.tokens_used)
    if decision == "no":
        chat.escalation_followup_pending = False
        _clear_escalation_clarify_flag(chat)
        chat.ended_at = utcnow()
        return (out.message_to_user, [], out.tokens_used)
    # first "unclear": model re-asks да/нет; set flag
    _set_escalation_clarify_flag(chat)
    return (out.message_to_user, [], out.tokens_used)

# --- T-3: user asked for human (before RAG) ---
if detect_human_request(question):
    ticket = create_escalation_ticket(..., trigger=EscalationTrigger.user_request, ...)
    phase = (
        EscalationPhase.handoff_ask_email
        if not ticket.user_email
        else EscalationPhase.handoff_email_known
    )
    out = complete_escalation_openai_turn(
        phase=phase, chat_messages=msgs,
        fact_json=fact_from_ticket(ticket),
        latest_user_text=question, api_key=api_key,
    )
    if not ticket.user_email:
        chat.escalation_awaiting_ticket_id = ticket.id
    else:
        chat.escalation_followup_pending = True
    return (out.message_to_user, [], out.tokens_used)

# --- Normal RAG + T-1/T-2 escalation ---
# ... retrieval happens here ...

escalate, trigger = should_escalate(best_score, len(chunk_texts))
if escalate:
    answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)
    ticket = create_escalation_ticket(..., trigger=trigger, ...)
    esc = complete_escalation_openai_turn(
        phase=(
            EscalationPhase.handoff_ask_email
            if not ticket.user_email
            else EscalationPhase.handoff_email_known
        ),
        chat_messages=msgs, fact_json=fact_from_ticket(ticket),
        latest_user_text=question, api_key=api_key,
    )
    answer = answer + "\n\n" + esc.message_to_user
    if not ticket.user_email:
        chat.escalation_awaiting_ticket_id = ticket.id
    else:
        chat.escalation_followup_pending = True
    return (answer, document_ids, tokens_used + esc.tokens_used)
```

Note: T-4 (answer rejection) handled via `POST /chat/{session_id}/escalate`. Same flags + same OpenAI escalation path.

### 5. Email notification to tenant

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

Table of tickets:
- Columns: Ticket #, Question (truncated), Priority, Status, User (email if available), Created, Actions
- Filter by status (open / in_progress / resolved)
- Click on row → expand:
  - Primary question, conversation summary, retrieved chunks preview, user note
  - Resolution text field + "Mark as resolved" button

### 7. Widget: "Talk to support" button

In `frontend/components/ChatWidget.tsx`:
- Add link below message input (always visible). Button label is a UI string only.
- On click → `POST /chat/{session_id}/escalate` (no fake user message). Backend returns model's handoff text.
- Parse `[[escalation_ticket:ESC-####]]` from assistant body (strip from display if desired). Show banner with ticket id.
- After `chat.ended_at`: disable input/send, show closed state (copy from last assistant message).

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
- [ ] New test: `detect_human_request()` — detects "talk to a human", "connect me to support", "хочу с человеком"
- [ ] New test: `compute_priority()` — T-3 + pro plan → critical
- [ ] New test: `parse_contact_email()` — valid email extracted, None on garbage
- [ ] New test: ticket creation stores correct fields, sequential ticket_number
- [ ] New test: chat pipeline — `ended_at` set → returns closed message (no RAG)
- [ ] New test: chat pipeline — `escalation_awaiting_ticket_id` set + valid email → `apply_collected_contact_email` called
- [ ] New test: chat pipeline — `escalation_awaiting_ticket_id` set + invalid email → `email_parse_failed` phase
- [ ] New test: chat pipeline — `escalation_followup_pending` + "yes" → followup cleared, no ended_at
- [ ] New test: chat pipeline — `escalation_followup_pending` + "no" → `ended_at` set
- [ ] New test: `followup_decision == "unclear"` twice → second unclear treated as "yes"
- [ ] New test: GET /escalations — tenant isolation (only own tickets)
- [ ] New test: resolve endpoint sets status=resolved, resolved_at != null
- [ ] New test: `complete_escalation_openai_turn()` — OpenAI error → fallback string returned, no exception raised
- [ ] Manual test: ask unanswerable → ticket appears in /escalations + email received
- [ ] Manual test: click "Talk to support" → ask-email phase → enter email → confirmed → follow-up question
- [ ] Manual test: say "no" to follow-up → chat closed, input disabled

---

## GIT PUSH

```bash
git add backend/models.py backend/escalation/ backend/chat/ backend/main.py \
        backend/migrations/versions/ \
        frontend/app/(app)/escalations/ frontend/components/ChatWidget.tsx \
        frontend/lib/api.ts tests/
git commit -m "feat: add L2 escalation tickets — OpenAI-generated UX, chat state machine (FI-ESC)"
git push origin feature/fi-esc-escalation-tickets
```

---

## NOTES

- **Language is emergent, not configured.** Model sees full chat history → writes in user's language automatically. No locale tables, no Accept-Language parsing.
- **v1 is internal only** — tickets in DB, email notification. Zendesk/Intercom = v2.
- **T-4 (answer rejection)** — separate endpoint, not in main chat pipeline.
- **Never raise in `complete_escalation_openai_turn()`** — fallback string on error, log exception.
- **Ticket number**: `ESC-{zero-padded}` per client. Use `SELECT FOR UPDATE` or handle `IntegrityError`.
- **Email notification**: best-effort — ticket created even if Brevo fails. Log failure.
- **`[[escalation_ticket:ESC-####]]`** machine token: append in Python if model omitted it, strip in widget display.
- Works without FI-KYC — `user_email` will be null for anonymous sessions (triggers `handoff_ask_email` phase).

---

## PR DESCRIPTION

```markdown
## Summary
Adds automatic L2 escalation tickets with a stateful chat flow. When the bot can't answer (low similarity, no docs, user request, answer rejection), it creates a structured ticket and enters an OpenAI-driven multi-turn escalation flow: collect user email if unknown, confirm ticket, offer to continue or close chat. All user-facing wording is generated by OpenAI in the user's language from the full chat context — no locale tables.

## Changes
- `backend/models.py` — EscalationTicket + EscalationPhase + Chat flag columns (escalation_awaiting_ticket_id, escalation_followup_pending, ended_at)
- `backend/migrations/versions/XXX` — escalation_tickets table + chat flag columns
- `backend/escalation/service.py` — triggers, ticket CRUD, email parsing, state helpers
- `backend/escalation/openai_escalation.py` — structured OpenAI completion for escalation UX
- `backend/escalation/routes.py` + `schemas.py` — escalations API
- `backend/chat/service.py` — chat state machine (closed → await_email → followup → RAG)
- `backend/main.py` — register escalation router
- `frontend/app/(app)/escalations/page.tsx` — escalations inbox with resolve workflow
- `frontend/components/ChatWidget.tsx` — "Talk to support" + ticket banner + closed state
- `frontend/lib/api.ts` — escalation API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: unanswerable → ticket → email collected → confirmed → follow-up → "no" → chat closed
- [ ] Manual: "Talk to support" → immediate ticket + handoff message

## Notes
Language is emergent from chat context, not configured. Works without FI-KYC (email collected inline).
```
