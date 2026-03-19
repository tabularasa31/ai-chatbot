# Ревью кода ai-chatbot

## Критичные и важные проблемы

### 1. Публичный эндпоинт `/clients/validate/{api_key}` без rate limiting

```109:124:backend/clients/routes.py
@clients_router.get("/validate/{api_key}", response_model=ValidateApiKeyResponse)
def validate_api_key(
    api_key: str,
    db: Annotated[Session, Depends(get_db)],
) -> ValidateApiKeyResponse:
```

Эндпоинт публичный и позволяет проверять валидность API-ключей. Без ограничения частоты запросов возможен перебор ключей (32 hex-символа). Нужен rate limit (например, 10–20 запросов в минуту с IP).

---

### 2. `/search` без rate limiting

```20:25:backend/search/routes.py
@search_router.post("", response_model=SearchResponse)
def search_route(
    body: SearchRequest,
    ...
```

Эндпоинт защищён JWT, но не ограничен по частоте. Каждый запрос вызывает OpenAI embeddings. Рекомендуется добавить лимит (например, 30/min, как у `/chat`).

---

### 3. `list_bad_answers`: нет валидации `limit` и `offset`

```277:283:backend/chat/routes.py
@chat_router.get("/bad-answers", response_model=BadAnswerListResponse)
def list_bad_answers(
    ...
    limit: int = 50,
    offset: int = 0,
) -> BadAnswerListResponse:
```

`limit` и `offset` не проверяются. Возможны:
- `limit=999999` — большая нагрузка на БД;
- `offset=-1` — ошибка в SQL.

Рекомендуется: `limit` в диапазоне 1–100, `offset >= 0`.

---

### 4. Устаревший `datetime.utcnow()`

Используется в:
- `backend/auth/routes.py` (строки 44, 110)
- `backend/core/security.py` (строка 54)
- `backend/models.py` (`_utcnow()`)
- тестах

В Python 3.12+ `datetime.utcnow()` помечен как deprecated. Лучше использовать `datetime.now(timezone.utc)`.

---

### 5. Потенциальный `AttributeError` при `m.feedback`

```388:390:backend/chat/service.py
    return [
        (m.id, chat.session_id, m.role.value, m.content, m.feedback.value, m.ideal_answer, m.created_at)
        for m in messages
```

Если `m.feedback` когда-либо будет `None` (старые данные, миграции), вызов `.value` приведёт к `AttributeError`. Безопаснее: `(m.feedback or MessageFeedback.none).value`.

---

## Средние проблемы

### 6. CORS: `allow_credentials=False`

```37:42:backend/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    ...
)
```

Токен передаётся через cookie (`document.cookie` в `api.ts`). При `allow_credentials=False` браузер не отправляет cookies в cross-origin запросах. Если фронт и бэк на разных доменах, авторизация по cookie может не работать. Сейчас используется `Authorization: Bearer`, так что это менее критично, но стоит проверить, что cookie действительно не нужны.

---

### 7. Слишком широкий `except` в `crypto.py`

```28:31:backend/core/crypto.py
    try:
        return f.decrypt(value.encode()).decode()
    except (InvalidToken, Exception) as e:
        raise RuntimeError(f"Failed to decrypt: {e}") from e
```

`Exception` перехватывает всё. Лучше явно указывать ожидаемые исключения (например, `InvalidToken`, `ValueError`, `TypeError`).

---

### 8. `HTTPException` из `from None` в `openai_client.py`

```32:36:backend/core/openai_client.py
    except RuntimeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to decrypt OpenAI API key.",
        ) from None
```

`from None` скрывает исходную причину ошибки. Для отладки лучше сохранять цепочку исключений (`from e` или без `from None`).

---

### 9. N+1 в `list_chat_sessions`

```310:318:backend/chat/service.py
    for chat in chats:
        messages = (
            db.query(Message)
            .filter(Message.chat_id == chat.id)
            ...
        )
```

Для каждой сессии выполняется отдельный запрос. При большом числе сессий это даёт N+1. Лучше один запрос с `joinedload` или агрегацией.

---

### 10. N+1 в `list_bad_answers`

```309:319:backend/chat/routes.py
    for msg in bad_messages:
        prev_user = (
            db.query(Message)
            .filter(...)
            .first()
        )
```

Для каждого «плохого» сообщения — отдельный запрос. Можно заменить одним запросом с подзапросом или оконной функцией.

---

### 11. Дублирование проверки `client.openai_api_key`

Проверка `if not client.openai_api_key` повторяется в:
- `chat/routes.py` (строки 90, 135)
- `search/routes.py` (строка 36)
- `embeddings/routes.py` (строка 51)

Имеет смысл вынести в общую зависимость/хелпер.

---

### 12. Загрузка файла целиком в память

```67:68:backend/documents/routes.py
    content = file.file.read()
    if len(content) > MAX_FILE_SIZE:
```

Файл до 50 MB читается в память. При множестве одновременных загрузок это может увеличить потребление памяти. Для больших файлов можно рассмотреть потоковую обработку.

---

## Мелкие замечания

### 13. `import logging` внутри `try` в auth

```59:61:backend/auth/routes.py
    except Exception as e:
        # Do not block signup if email fails in dev
        import logging
        logging.getLogger(__name__).warning(...)
```

`logging` лучше импортировать в начале модуля.

---

### 14. Хардкод URL в embed.js

```5:5:backend/widget/static/embed.js
  var apiBase = scriptEl ? new URL(scriptEl.src).origin : "https://ai-chatbot-production-6531.up.railway.app";
```

URL захардкожен. Лучше вынести в конфиг или переменную окружения.

---

### 15. Несогласованность типов `user.id`

В `api.ts`:
```typescript
return data as { token: string; expires_in: number; user: { id: number; email: string } };
```

В бэкенде `user.id` — UUID. На фронте ожидается `number`. Нужно привести типы к одному виду (например, `string` для UUID).

---

### 16. `documents.upload` без токена

```205:208:frontend/lib/api.ts
      const res = await fetch(`${BASE_URL}/documents`, {
        method: "POST",
        ...
        headers: token ? { Authorization: `Bearer ${token}` } : {},
```

При отсутствии токена запрос уйдёт без заголовка. Эндпоинт защищён `require_verified_user`, так что вернётся 401. Логичнее всегда использовать `authFetch`, чтобы не дублировать логику авторизации.

---

## Что сделано хорошо

- Разделение JWT и API key для разных эндпоинтов
- Шифрование OpenAI API key (Fernet)
- Проверка владельца для документов, сессий, сообщений
- `require_verified_user` для критичных операций
- Rate limiting на auth и chat
- Валидация паролей и email
- CORS с явным списком `ALLOWED_ORIGINS`

---

## Приоритеты

| Приоритет | Проблема |
|-----------|----------|
| Высокий | Rate limit для `/clients/validate/{api_key}` |
| Высокий | Rate limit для `/search` |
| Высокий | Валидация `limit`/`offset` в `list_bad_answers` |
| Средний | Замена `datetime.utcnow()` на `datetime.now(timezone.utc)` |
| Средний | Защита от `m.feedback is None` в `get_session_logs` |
| Низкий | Рефакторинг N+1, вынос общих проверок, мелкие правки |
