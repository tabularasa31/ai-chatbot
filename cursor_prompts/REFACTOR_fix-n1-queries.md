# REFACTOR: Fix N+1 Queries in chat/service.py

⚠️ **CRITICAL: Follow the SETUP commands EXACTLY in order. Do NOT skip `git pull origin main`.**

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/refactor-fix-n1-queries
```

**MUST DO:**
1. `git checkout main` — switch to main
2. `git pull origin main` — fetch latest (do not skip!)
3. `git checkout -b feature/refactor-fix-n1-queries` — create NEW branch

**DO NOT reuse old branches or skip the pull step.**

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/chat/service.py` — fix N+1 in `list_chat_sessions()` and `list_bad_answers()`

**Do NOT touch:**
- Database, models, migrations
- Other modules
- Business logic (only optimize queries)

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem from CODE_REVIEW.md:**

Two N+1 query issues in chat/service.py:

1. **list_chat_sessions()** — For each session, executes separate query for messages
   - Current: ~110 lines, loops through chats and queries Message for each
   - Problem: If 100 sessions, 100+ DB queries
   - Solution: Use `joinedload` or single aggregation query

2. **list_bad_answers()** — For each bad message, queries previous message
   - Current: Loops through bad_messages, for each executes query to find previous user message
   - Problem: If 50 bad answers, 50+ DB queries
   - Solution: Single query with window functions or LEFT JOIN

---

## WHAT TO DO

### 1. Fix N+1 in list_chat_sessions()

**File: backend/chat/service.py**

Find function `list_chat_sessions()` (around line 310-340)

**Current approach (N+1):**
```python
def list_chat_sessions(client_id: str, db: Session) -> list[ChatSessionSummary]:
    chats = db.query(Chat).filter(Chat.client_id == client_id).all()
    
    result = []
    for chat in chats:
        messages = (
            db.query(Message)
            .filter(Message.chat_id == chat.id)
            .order_by(Message.created_at.desc())
            .all()
        )
        # Process messages...
        result.append(ChatSessionSummary(...))
    return result
```

**Solution: Use joinedload for eager loading**

```python
def list_chat_sessions(client_id: str, db: Session) -> list[ChatSessionSummary]:
    from sqlalchemy.orm import joinedload
    
    chats = (
        db.query(Chat)
        .filter(Chat.client_id == client_id)
        .options(joinedload(Chat.messages))
        .all()
    )
    
    result = []
    for chat in chats:
        # messages now available via chat.messages (already loaded)
        messages = sorted(chat.messages, key=lambda m: m.created_at, reverse=True)
        # Process messages...
        result.append(ChatSessionSummary(...))
    return result
```

**Or use aggregation:** If you only need summary info (last message, count), query once with aggregation:

```python
def list_chat_sessions(client_id: str, db: Session) -> list[ChatSessionSummary]:
    from sqlalchemy import func
    
    chats = db.query(Chat).filter(Chat.client_id == client_id).all()
    
    result = []
    for chat in chats:
        summary = (
            db.query(
                func.count(Message.id).label("message_count"),
                func.max(Message.created_at).label("last_activity"),
            )
            .filter(Message.chat_id == chat.id)
            .first()
        )
        # Use summary.message_count, summary.last_activity
        result.append(ChatSessionSummary(...))
    return result
```

**Recommended:** Use `joinedload` if you need full message objects, or aggregation if only summaries.

---

### 2. Fix N+1 in list_bad_answers()

**File: backend/chat/routes.py** (note: it's routes, not service)

Find function `list_bad_answers()` (around line 309-319)

**Current approach (N+1):**
```python
for msg in bad_messages:
    prev_user = (
        db.query(Message)
        .filter(
            Message.chat_id == msg.chat_id,
            Message.created_at < msg.created_at,
            Message.role == MessageRole.user,
        )
        .order_by(Message.created_at.desc())
        .first()
    )
    # Use prev_user...
```

**Solution: Use window functions or LEFT JOIN**

Option 1: **Single query with window function (PostgreSQL)**
```python
from sqlalchemy import func, literal_column

bad_with_prev = (
    db.query(
        Message.id,
        Message.content.label("question"),
        literal_column("LAG(content) OVER (PARTITION BY chat_id ORDER BY created_at DESC)")
        .label("prev_question"),
    )
    .filter(Message.feedback == MessageFeedback.down)
    .all()
)
```

Option 2: **Load all messages, process in Python** (simpler for SQLite)
```python
def list_bad_answers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BadAnswerListResponse:
    client = ... # Get current client
    
    # Single query: get all messages for this client
    all_messages = (
        db.query(Message)
        .join(Chat, Chat.id == Message.chat_id)
        .filter(Chat.client_id == client.id)
        .order_by(Chat.session_id, Message.created_at)
        .all()
    )
    
    # Process in Python: group by session, find prev message for each bad answer
    messages_by_session = defaultdict(list)
    for msg in all_messages:
        messages_by_session[msg.chat_id].append(msg)
    
    bad_answers = []
    for chat_id, messages in messages_by_session.items():
        for i, msg in enumerate(messages):
            if msg.feedback == MessageFeedback.down:
                prev_msg = messages[i-1] if i > 0 else None
                bad_answers.append({
                    "message_id": msg.id,
                    "question": prev_msg.content if prev_msg else None,
                    "answer": msg.content,
                    ...
                })
    
    # Apply pagination
    return BadAnswerListResponse(
        items=bad_answers[offset:offset+limit],
        total=len(bad_answers),
    )
```

**Recommended:** Option 2 (in-memory processing) is simpler and avoids SQL complexity. Since we're filtering by `client_id`, data volume should be manageable.

---

## TESTING

Before pushing:
- [ ] Backend starts without errors
- [ ] `list_chat_sessions()` still returns correct data
- [ ] `list_bad_answers()` still returns correct data
- [ ] Performance improved (fewer DB queries — verify with logging)
- [ ] No N+1 warnings in logs

**Quick test:**
```bash
# Check: call list_chat_sessions with 10 sessions
# Before: ~10 queries to DB
# After: ~1 query (with joinedload) or ~10 aggregations (depending on approach)

# Verify no N+1 by enabling SQL logging:
# Add to backend/core/db.py or main.py:
# logging.basicConfig()
# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
```

---

## GIT PUSH

```bash
git add backend/chat/service.py backend/chat/routes.py
git commit -m "refactor: fix N+1 queries in list_chat_sessions and list_bad_answers (CODE_REVIEW)"
git push origin feature/refactor-fix-n1-queries
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- N+1 query problem: 1 main query + N subqueries = poor performance at scale
- `joinedload` in SQLAlchemy eager-loads relationships efficiently
- Window functions (LAG, ROW_NUMBER) are powerful but PostgreSQL-only
- In-memory processing is simpler, works with SQLite, acceptable if data < 10MB
- Consider adding logging to measure query count before/after optimization
- Document the optimization in code comments for future maintainers
