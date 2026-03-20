# FI-034: LLM-based Answer Validation — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b feature/fi-034-answer-validation
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/chat/service.py` — add `validate_answer()`, update `process_chat_message()` and `process_chat_debug()`
- `backend/chat/schemas.py` — add `validation` field to `ChatResponse`

**Do NOT touch:**
- migrations
- `backend/models.py`
- `backend/search/service.py`
- Frontend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** RAG pipeline generates an answer and returns it immediately without quality check. The bot can hallucinate or give off-topic answers when context is weak.

**Current pipeline:**
```
retrieve_context → generate_answer → save → return
```

**Goal:** Add validation step between generation and saving:
```
retrieve_context → generate_answer → validate_answer → save → return
```

**Key constraint:** If validation itself fails (OpenAI error, JSON parse error) — do NOT block the answer. Fail gracefully, log the error, continue.

---

## WHAT TO DO

### 1. Add `validate_answer()` to `backend/chat/service.py`

```python
import json
import logging

logger = logging.getLogger(__name__)

VALIDATION_PROMPT = """You are a fact-checker for a support chatbot.

Context (retrieved from documentation):
{context}

Question: {question}

Answer to validate: {answer}

Check if the answer is:
1. Grounded in the provided context (not hallucinated)
2. Actually answers the question

Respond ONLY with JSON (no markdown, no explanation):
{{"is_valid": true/false, "confidence": 0.0-1.0, "reason": "short explanation"}}"""


def validate_answer(
    question: str,
    answer: str,
    context_chunks: list[str],
    *,
    api_key: str,
) -> dict:
    """
    Ask LLM to validate if the answer is grounded in context.
    Returns {"is_valid": bool, "confidence": float, "reason": str}.
    On any error, returns {"is_valid": True, "confidence": 1.0, "reason": "validation_skipped"}.
    """
    if not context_chunks:
        return {"is_valid": False, "confidence": 0.0, "reason": "no_context"}

    context = "\n\n---\n\n".join(context_chunks[:3])  # top 3 chunks only
    prompt = VALIDATION_PROMPT.format(
        context=context,
        question=question,
        answer=answer,
    )

    try:
        openai_client = get_openai_client(api_key)
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        raw = response.choices[0].message.content or ""
        result = json.loads(raw.strip())
        return {
            "is_valid": bool(result.get("is_valid", True)),
            "confidence": float(result.get("confidence", 1.0)),
            "reason": str(result.get("reason", "")),
        }
    except Exception as e:
        logger.warning(f"Answer validation failed (non-blocking): {e}")
        return {"is_valid": True, "confidence": 1.0, "reason": "validation_skipped"}
```

### 2. Update `process_chat_message()`

After `generate_answer()`, before saving:

```python
# 3. Generate answer
answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)

# 3.5 Validate answer (non-blocking)
validation = validate_answer(question, answer, chunk_texts, api_key=api_key)
LOW_CONFIDENCE_THRESHOLD = 0.4
if not validation["is_valid"] and validation["confidence"] < LOW_CONFIDENCE_THRESHOLD:
    answer = "I don't have enough information in my knowledge base to answer this question accurately."

# 4. Find or create Chat (rest unchanged)
```

### 3. Update `process_chat_debug()`

Add validation to debug dict:
```python
debug = {
    "mode": mode,
    "chunks": chunks_debug,
    "validation": validate_answer(question, answer, chunk_texts, api_key=api_key),
}
```

### 4. Update `ChatResponse` in `backend/chat/schemas.py`

```python
from typing import Optional

class ChatResponse(BaseModel):
    answer: str
    session_id: UUID
    source_documents: list[UUID]
    tokens_used: int
    validation: Optional[dict] = None  # shown in debug mode only
```

---

## TESTING

Before pushing:
- [ ] `validate_answer()` with empty context returns `{"is_valid": False, ...}`
- [ ] `validate_answer()` does not raise on OpenAI error — returns fallback dict
- [ ] `process_chat_message()` still returns an answer even when validation fails
- [ ] `pytest -q` passes

---

## GIT PUSH

```bash
git add backend/chat/service.py backend/chat/schemas.py
git commit -m "feat: add LLM-based answer validation with graceful fallback (FI-034)"
git push origin feature/fi-034-answer-validation
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- Validation makes an extra OpenAI call — costs tokens from the client's key
- Threshold `0.4`: only replace answer if BOTH `is_valid=False` AND `confidence < 0.4`
- Validation result shown in debug mode; widget users never see it
- `temperature=0` for validation — we want deterministic fact-checking

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Added LLM-based answer validation step to the RAG pipeline. Low-confidence hallucinated answers are replaced with a fallback message. Validation errors are non-blocking.

## Changes
- `backend/chat/service.py` — added validate_answer(), updated process_chat_message() and process_chat_debug()
- `backend/chat/schemas.py` — added optional validation field to ChatResponse

## Testing
- [ ] Tests pass
- [ ] Validation failure does not block answer
- [ ] Low-confidence answers replaced with fallback
- [ ] Debug endpoint includes validation result

## Notes
Extra OpenAI call per request (client's key). Threshold: confidence < 0.4 to replace answer.
```
