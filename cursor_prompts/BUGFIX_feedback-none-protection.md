# BUGFIX: Protect Against m.feedback=None in get_session_logs

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/bugfix-feedback-none
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/chat/service.py` — fix `m.feedback` None handling in `get_session_logs`

**Do NOT touch:**
- Routes, models, migrations
- Other functions
- Database

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Issue:** In `get_session_logs`, code calls `m.feedback.value` without checking if `m.feedback` is `None`. If old data exists with null feedback (from migrations or edge cases), this raises `AttributeError`.

**Current code (backend/chat/service.py, ~line 388):**
```python
return [
    (m.id, chat.session_id, m.role.value, m.content, m.feedback.value, m.ideal_answer, m.created_at)
    for m in messages
]
```

**Problem:** If `m.feedback is None`, calling `.value` crashes with:
```
AttributeError: 'NoneType' object has no attribute 'value'
```

**Solution:** Use `(m.feedback or MessageFeedback.none).value` to default to "none" if null.

---

## WHAT TO DO

### 1. Find the function

Locate `get_session_logs` in `backend/chat/service.py` (around line 310-390).

### 2. Fix the return statement

Find the list comprehension that builds the return value:

**Before:**
```python
return [
    (m.id, chat.session_id, m.role.value, m.content, m.feedback.value, m.ideal_answer, m.created_at)
    for m in messages
]
```

**After:**
```python
return [
    (m.id, chat.session_id, m.role.value, m.content, (m.feedback or MessageFeedback.none).value, m.ideal_answer, m.created_at)
    for m in messages
]
```

**Explanation:**
- `m.feedback or MessageFeedback.none` — If `m.feedback` is `None`, use default `MessageFeedback.none`
- `.value` — Get the string value ("none", "up", "down")
- Safe for both old data (null) and new data (enum)

### 3. Verify MessageFeedback import

Make sure `MessageFeedback` enum is imported at the top of the file:
```python
from backend.models import MessageFeedback
```

(It should already be imported if the file uses it elsewhere.)

---

## TESTING

Before pushing:
- [ ] Backend starts without errors
- [ ] Call GET `/chat/logs/{session_id}` with valid session
- [ ] Response includes messages with feedback (should work)
- [ ] If manually set `feedback=NULL` in DB, endpoint still returns 200 (doesn't crash)
- [ ] Feedback value is "none" for null entries

**Manual test:**
```bash
# Set one message's feedback to NULL directly in DB
sqlite3 chatbot.db "UPDATE message SET feedback = NULL WHERE id = 'some-id';"

# Then call endpoint (should not crash)
curl "http://localhost:8000/chat/logs/{session_id}" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Should return 200, not 500
```

---

## GIT PUSH

```bash
git add backend/chat/service.py
git commit -m "fix: protect against None feedback in get_session_logs"
git push origin feature/bugfix-feedback-none
```

---

## NOTES

- Safe for both old data (null feedback) and new data (enum)
- Defaults to "none" when null (reasonable default)
- No migration needed (backward compatible)
- Also consider adding DB constraint `NOT NULL` on feedback column if safe
