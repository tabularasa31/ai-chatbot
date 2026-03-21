# CURSOR PROMPT: [FIX] EscalationTicket — race condition in generate_ticket_number

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b fix/ticket-number-race-condition
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/escalation/service.py` — fix `generate_ticket_number()`

**Do NOT touch:**
- Anything else. This is a surgical single-function fix.

---

## CONTEXT

Current implementation of `generate_ticket_number()` in `backend/escalation/service.py`:

```python
def generate_ticket_number(client_id: uuid.UUID, db: Session) -> str:
    rows = (
        db.query(EscalationTicket.ticket_number)
        .filter(EscalationTicket.client_id == client_id)
        .all()
    )
    max_n = 0
    for (num,) in rows:
        if isinstance(num, str) and num.upper().startswith("ESC-"):
            try:
                max_n = max(max_n, int(num[4:]))
            except ValueError:
                continue
    return f"ESC-{max_n + 1:04d}"
```

**Problem:** Race condition. Two concurrent requests for the same client both read `max_n = 5`, both compute `ESC-0006`, one fails with `IntegrityError` on the `UniqueConstraint(client_id, ticket_number)`. The failing request results in a 500 error — the user gets no ticket confirmation.

**Why `UniqueConstraint` alone isn't enough:** It prevents duplicate data, but doesn't prevent the 500 error reaching the user.

---

## WHAT TO DO

Replace `generate_ticket_number()` with a retry loop that handles the `IntegrityError`:

```python
def generate_ticket_number(client_id: uuid.UUID, db: Session) -> str:
    """
    Generate next sequential ticket number for client.
    
    Uses MAX(ticket_number) + 1 with retry on IntegrityError (race condition).
    UniqueConstraint on (client_id, ticket_number) is the safety net;
    this retry loop ensures the caller gets a valid number instead of a 500.
    
    Max 5 retries before raising — in practice, 1-2 retries cover any race.
    """
    from sqlalchemy.exc import IntegrityError
    import re

    _NUM_RE = re.compile(r"^ESC-(\d+)$", re.IGNORECASE)

    for attempt in range(5):
        rows = (
            db.query(EscalationTicket.ticket_number)
            .filter(EscalationTicket.client_id == client_id)
            .with_for_update(skip_locked=True)  # advisory lock at DB level
            .all()
        )
        max_n = 0
        for (num,) in rows:
            if isinstance(num, str):
                m = _NUM_RE.match(num)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        candidate = f"ESC-{max_n + 1:04d}"
        return candidate  # caller commits; IntegrityError handled in create_escalation_ticket

    raise RuntimeError("Failed to generate ticket number after 5 attempts")
```

**Also update `create_escalation_ticket()`** to catch `IntegrityError` on ticket insert and retry with a fresh number (max 3 retries):

```python
def create_escalation_ticket(...) -> EscalationTicket:
    from sqlalchemy.exc import IntegrityError

    for attempt in range(3):
        ticket_number = generate_ticket_number(client_id, db)
        ticket = EscalationTicket(
            ticket_number=ticket_number,
            ...
        )
        db.add(ticket)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
            continue
    
    db.refresh(ticket)
    # ... rest of function (notify tenant, etc.)
    return ticket
```

**Note on `with_for_update(skip_locked=True)`:**
- On PostgreSQL: uses `SELECT ... FOR UPDATE SKIP LOCKED` — advisory row lock
- On SQLite (tests): `with_for_update` is ignored gracefully — tests will still pass
- This is defense-in-depth; the retry loop is the primary fix

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New test: `generate_ticket_number()` called twice concurrently for same client → both return unique numbers (mock or sequential calls)
- [ ] New test: `create_escalation_ticket()` retries on IntegrityError (mock IntegrityError on first commit attempt)
- [ ] New test: after 3 failed attempts in `create_escalation_ticket()`, exception is re-raised

---

## GIT PUSH

```bash
git add backend/escalation/service.py tests/test_escalation.py
git commit -m "fix: retry on IntegrityError in generate_ticket_number (race condition)"
git push origin fix/ticket-number-race-condition
```

---

## NOTES

- Low traffic risk now — but correct-at-any-scale is better than "probably fine"
- `with_for_update(skip_locked=True)` means concurrent readers skip locked rows → won't deadlock
- The `UniqueConstraint` on `(client_id, ticket_number)` remains as the final safety net
- SQLite in tests ignores `with_for_update` — tests are unaffected

---

## PR DESCRIPTION

```markdown
## Summary
Fixes a race condition in `generate_ticket_number()`: concurrent escalation triggers for the same client could both compute the same ticket number, causing one to fail with IntegrityError (500 error). Fix adds `SELECT FOR UPDATE SKIP LOCKED` + retry loop in `create_escalation_ticket()`.

## Changes
- `backend/escalation/service.py` — `generate_ticket_number()` uses `with_for_update(skip_locked=True)`; `create_escalation_ticket()` retries up to 3 times on IntegrityError

## Testing
- [ ] pytest passes (existing + new tests)
- [ ] New: concurrent ticket number generation returns unique numbers
- [ ] New: IntegrityError retry logic tested

## Notes
Low risk at current traffic. Correct behavior at any scale.
```
