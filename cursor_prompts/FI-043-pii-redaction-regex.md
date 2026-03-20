# CURSOR PROMPT: [FI-043] PII Redaction — Stage 1 (Regex)

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-043-pii-redaction
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/chat/service.py` — intercept messages before external API calls
- `backend/chat/pii.py` — create this new file with redaction logic
- `tests/chat/test_pii.py` — create tests for redaction

**Do NOT touch:**
- `backend/models.py`
- `backend/migrations/`
- `backend/search/`
- `backend/embeddings/`
- `backend/widget/`
- `backend/auth/`
- `backend/clients/`
- Any existing tests

**No DB changes. No new endpoints. No frontend changes.**

If you think something outside Scope must be changed, STOP and describe it in a comment.

---

## CONTEXT

Every user message currently goes to OpenAI verbatim — both to the embeddings API (for retrieval) and to the completion API (for answer generation). Users sometimes include emails, phone numbers, or API keys in their messages.

The fix is simple: redact before sending out, preserve original in memory for response context. No DB changes needed in this PR — original text is already stored in `Message.content` (that's fine for now, storage rules are a v2 concern).

---

## WHAT TO DO

### 1. Create `backend/chat/pii.py`

This module contains all redaction logic. No external dependencies — pure Python regex only.

```python
"""
PII Redaction Layer — Stage 1 (Regex).

Replaces sensitive entities with typed placeholders before
text is sent to any external API (OpenAI embeddings or completion).

Entities detected:
  Tier 1A (always redact):
    - EMAIL        → [EMAIL]
    - PHONE        → [PHONE]
    - API_KEY      → [API_KEY]
    - CREDIT_CARD  → [CREDIT_CARD]

Order of application: most-specific patterns first to avoid partial matches.
"""

import re

# ── Patterns ──────────────────────────────────────────────────────────────────

# API keys: Bearer tokens, sk-* (OpenAI style), hex strings 32+ chars, base64 tokens 40+ chars
_API_KEY_PATTERNS = [
    r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",         # Bearer <token>
    r"\bsk-[A-Za-z0-9]{20,}\b",                  # OpenAI-style sk-...
    r"\b[A-Fa-f0-9]{32,}\b",                     # hex 32+ chars (API keys, hashes)
    r"\b[A-Za-z0-9+/]{40,}={0,2}\b",            # base64 40+ chars
]

# Credit cards: 13–19 digit sequences with optional spaces/dashes
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d[ -]?){13,18}\d\b"
)

# Phone numbers: RU formats + international
_PHONE_RE = re.compile(
    r"""
    (?:
        # International: +7, +1, +44, etc. with various separators
        \+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{1,4}[\s\-.]?\d{1,9}
        |
        # RU: 8 (XXX) XXX-XX-XX or 8XXXXXXXXXX
        \b8[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{2}[\s\-.]?\d{2}\b
        |
        # Compact international without spaces: +79991234567
        \+\d{10,14}\b
    )
    """,
    re.VERBOSE,
)

# Email: standard RFC-like pattern
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Compile API key patterns
_API_KEY_RE = re.compile("|".join(_API_KEY_PATTERNS))


# ── Public API ─────────────────────────────────────────────────────────────────

def redact(text: str) -> tuple[str, bool]:
    """
    Redact PII from text using regex patterns.

    Applies patterns in order: API keys → credit cards → phones → emails.
    More specific patterns run first to avoid partial matches.

    Args:
        text: Original user message.

    Returns:
        Tuple of (redacted_text, was_redacted).
        was_redacted is True if any substitution was made.
    """
    original = text

    text = _API_KEY_RE.sub("[API_KEY]", text)
    text = _CREDIT_CARD_RE.sub("[CREDIT_CARD]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)

    return text, text != original


def redact_text(text: str) -> str:
    """Convenience wrapper — returns only the redacted string."""
    redacted, _ = redact(text)
    return redacted
```

### 2. Apply redaction in `backend/chat/service.py`

Import at the top:
```python
from backend.chat.pii import redact
```

In `process_chat_message()`, redact the question before it reaches any external service:

```python
def process_chat_message(
    client_id: uuid.UUID,
    question: str,
    session_id: uuid.UUID,
    db: Session,
    *,
    api_key: str,
) -> tuple[str, list[uuid.UUID], int]:
    # Redact PII before sending to OpenAI
    redacted_question, _was_redacted = redact(question)

    # 1. Retrieve context (use redacted_question for embedding search)
    chunk_texts, doc_ids, _scores, _mode = retrieve_context(
        client_id, redacted_question, db, api_key, top_k=5
    )
    document_ids = list(dict.fromkeys(doc_ids))

    # 2. Generate answer (use redacted_question in the prompt)
    answer, tokens_used = generate_answer(redacted_question, chunk_texts, api_key=api_key)

    # 3-7. Save to DB — store original question (not redacted)
    # Original is preserved for tenant admin context; only redacted goes to OpenAI
    # ... rest of DB saving logic unchanged, using original `question` for Message.content
```

In `run_debug()`, same — redact before retrieve_context and generate_answer, but preserve original for debug output:

```python
def run_debug(...):
    redacted_question, _was_redacted = redact(question)
    chunk_texts, document_ids, scores, mode = retrieve_context(
        client_id, redacted_question, db, api_key, top_k=5
    )
    answer, tokens_used = generate_answer(redacted_question, chunk_texts, api_key=api_key)
    # debug dict unchanged
```

**Key rule:** `redacted_question` → OpenAI. Original `question` → DB (Message.content).

### 3. Tests (`tests/chat/test_pii.py`)

Write tests for `backend/chat/pii.py`. Cover:

```python
# Emails
assert redact_text("my email is user@example.com please help") == "my email is [EMAIL] please help"
assert redact_text("contact support@company.co.uk") == "contact [EMAIL]"

# Phones — RU formats
assert "[PHONE]" in redact_text("звони на +7 (999) 123-45-67")
assert "[PHONE]" in redact_text("мой номер 8-999-123-45-67")
assert "[PHONE]" in redact_text("+79991234567")

# Phones — international
assert "[PHONE]" in redact_text("call me at +1-800-555-0100")

# API keys
assert "[API_KEY]" in redact_text("my key is sk-abc123XYZ789verylongkeyhere1234")
assert "[API_KEY]" in redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123")

# Credit cards
assert "[CREDIT_CARD]" in redact_text("card: 4111 1111 1111 1111")
assert "[CREDIT_CARD]" in redact_text("4111111111111111")

# No false positives — normal text should not be redacted
text = "how do I reset my password?"
assert redact_text(text) == text

# was_redacted flag
_, was_redacted = redact("send to test@email.com")
assert was_redacted is True

_, was_redacted = redact("how do I reset my password?")
assert was_redacted is False

# Multiple entities in one message
result = redact_text("I'm John, email test@test.com, phone +79991234567")
assert "[EMAIL]" in result
assert "[PHONE]" in result
assert "test@test.com" not in result
assert "+79991234567" not in result
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] `pytest tests/chat/test_pii.py -v` — all new PII tests pass
- [ ] Manual check: no false positives on common support questions ("how do I reset password?", "what is the pricing?")

---

## GIT PUSH

```bash
git add backend/chat/pii.py backend/chat/service.py tests/chat/test_pii.py
git commit -m "feat: add PII redaction layer Stage 1 (regex) before OpenAI calls (FI-043)"
git push origin feature/fi-043-pii-redaction
```

---

## NOTES

- No DB schema changes — keep it minimal
- Original question stored in `Message.content` as-is (tenant admin needs it for L2 context)
- Only redacted text leaves the platform perimeter (to OpenAI)
- Credit card pattern is intentionally broad — better to false-positive on a long number than miss a real card
- Hex 32+ char pattern will catch MD5/SHA hashes too — acceptable tradeoff for v1
- Stage 2 (Presidio NER for names, addresses) is FI-044 — do not implement here

---

## PR DESCRIPTION

```markdown
## Summary
Adds PII redaction layer (Stage 1, regex-only) that intercepts user messages before they are sent to OpenAI. Emails, phone numbers, API keys, and credit card numbers are replaced with typed placeholders. Original text is preserved in the database for tenant admin context.

## Changes
- `backend/chat/pii.py` — new module with regex redaction logic
- `backend/chat/service.py` — apply `redact()` in `process_chat_message()` and `run_debug()` before external API calls
- `tests/chat/test_pii.py` — tests for all entity types + edge cases

## Testing
- [ ] All existing tests pass (`pytest -q`)
- [ ] PII tests pass (`pytest tests/chat/test_pii.py -v`)
- [ ] No false positives on normal support questions

## Notes
Stage 2 (Presidio NER) is tracked separately as FI-044, to be done when EU/enterprise clients require it.
```
