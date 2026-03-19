# Feature: [FI-034] LLM-based answer validation

## Проблема

Сейчас RAG pipeline генерирует ответ и сразу отдаёт его пользователю — без проверки качества.
Бот может галлюцинировать или давать ответ не по теме когда контекст слабый.

Текущий pipeline в `backend/chat/service.py`:
```
retrieve_context → generate_answer → save → return
```

Нужно добавить шаг валидации между генерацией и сохранением:
```
retrieve_context → generate_answer → validate_answer → save → return
```

---

## Что нужно сделать

### 1. Новая функция `validate_answer` в `backend/chat/service.py`

```python
def validate_answer(
    question: str,
    answer: str,
    context_chunks: list[str],
    *,
    api_key: str,
) -> dict:
    """
    Ask LLM to validate if the answer is grounded in the context.
    
    Returns:
    {
        "is_valid": bool,        # True if answer is grounded in context
        "confidence": float,     # 0.0 - 1.0
        "reason": str,           # short explanation (for debug/logs)
    }
    """
```

Промпт для валидации:
```python
VALIDATION_PROMPT = """You are a fact-checker for a support chatbot.

Context (retrieved from documentation):
{context}

Question: {question}

Answer to validate: {answer}

Check if the answer is:
1. Grounded in the provided context (not hallucinated)
2. Actually answers the question

Respond ONLY with JSON:
{{"is_valid": true/false, "confidence": 0.0-1.0, "reason": "short explanation"}}"""
```

Use `gpt-4o-mini`, `temperature=0`, `max_tokens=150`.
Parse response as JSON.

---

### 2. Обновить `process_chat_message`

```python
# 3. Generate answer
answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)

# 3.5 Validate answer (NEW)
validation = validate_answer(question, answer, chunk_texts, api_key=api_key)
if not validation["is_valid"] and validation["confidence"] < 0.4:
    answer = "I don't have enough information in my knowledge base to answer this question accurately."

# 4. Find or create Chat
...
```

---

### 3. Сохранять результат валидации в `metadata`

В `Message` модели уже есть поле `source_documents`. Добавить валидацию в ответ и лог.

В `backend/chat/schemas.py` обновить `ChatResponse`:

```python
class ChatResponse(BaseModel):
    answer: str
    session_id: UUID
    source_documents: list[UUID]
    tokens_used: int
    validation: Optional[dict] = None  # {"is_valid": bool, "confidence": float, "reason": str}
```

Валидацию логировать но **не показывать пользователю виджета** — только в dashboard (debug mode).

---

### 4. Обновить debug endpoint

В `process_chat_debug` добавить validation в возвращаемый debug dict:

```python
debug = {
    "mode": mode,
    "chunks": chunks_debug,
    "validation": validation,  # NEW
}
```

---

## Важно

- Валидация делает дополнительный вызов OpenAI (через ключ клиента) — токены тратятся
- Если валидация падает (OpenAI error, JSON parse error) — **не блокировать ответ**, просто логировать ошибку и продолжать
- `is_valid=False` не всегда означает плохой ответ — порог для замены ответа: `confidence < 0.4`
- Не добавлять миграций — всё хранится в существующих полях

---

## Файлы для изменения

1. **`backend/chat/service.py`**
   - добавить `validate_answer()`
   - обновить `process_chat_message()` — вызвать валидацию
   - обновить `process_chat_debug()` — добавить validation в debug

2. **`backend/chat/schemas.py`**
   - `ChatResponse` — добавить опциональное поле `validation`

---

## Текущий код для reference

```python
# process_chat_message — текущий pipeline
chunk_texts, doc_ids, _scores, _mode = retrieve_context(...)
answer, tokens_used = generate_answer(question, chunk_texts, api_key=api_key)
# сразу сохраняем в БД — нет валидации

# generate_answer — текущий
response = openai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.2,
    max_tokens=500,
)
```
