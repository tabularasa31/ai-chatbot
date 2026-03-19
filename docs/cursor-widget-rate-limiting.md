# Feature: Rate limiting for /widget/chat

## Проблема

`POST /widget/chat` — публичный эндпоинт без аутентификации. Любой может слать запросы в цикле и расходовать OpenAI токены клиента.

Текущий код (`backend/routes/widget.py`) — нет `@limiter.limit(...)`.

Другие публичные эндпоинты уже защищены:
- `/chat` → `@limiter.limit("30/minute")`
- `/search` → `@limiter.limit("30/minute")`

---

## Что нужно сделать

### 1. Добавить rate limiting на `/widget/chat`

```python
from backend.core.limiter import limiter
from fastapi import Request

@widget_router.post("/chat")
@limiter.limit("20/minute")  # per IP
def widget_chat(
    request: Request,          # ← обязательно для slowapi
    message: Annotated[str, Query(...)],
    client_id: Annotated[str, Query(...)],
    session_id: Annotated[Optional[str], Query(...)] = None,
    db: Session = Depends(get_db),
) -> dict:
    ...
```

### 2. Добавить rate limiting по `client_id` (дополнительно)

Чтобы один перегруженный клиент не блокировал других — добавить лимит по `public_id`:

```python
from slowapi import Limiter

def _widget_key_func(request: Request) -> str:
    """Rate limit by IP + client_id combination."""
    from backend.core.config import settings
    if settings.environment == "test":
        import uuid
        return str(uuid.uuid4())
    ip = request.client.host if request.client else "unknown"
    client_id = request.query_params.get("client_id", "unknown")
    return f"{ip}:{client_id}"

widget_limiter = Limiter(key_func=_widget_key_func)
```

Применить:
```python
@widget_router.post("/chat")
@widget_limiter.limit("20/minute")
def widget_chat(request: Request, ...):
```

> Если два лимитера сложно — достаточно простого `@limiter.limit("20/minute")` по IP.

### 3. Обработка ошибки лимита

`slowapi` автоматически возвращает `429 Too Many Requests`. Убедиться что `RateLimitExceeded` handler зарегистрирован в `main.py` (уже есть):

```python
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

---

## Файлы для изменения

1. **`backend/routes/widget.py`** — добавить `@limiter.limit`, `request: Request` параметр

---

## Текущий код `/widget/chat`:

```python
@widget_router.post("/chat")
def widget_chat(
    message: Annotated[str, Query(...)],
    client_id: Annotated[str, Query(...)],
    session_id: Annotated[Optional[str], Query(...)] = None,
    db: Session = Depends(get_db),
) -> dict:
    # нет rate limiting
    ...
```

## Лимиты для reference (другие эндпоинты):

```python
# chat/routes.py
@limiter.limit("30/minute")   # /chat
@limiter.limit("30/minute")   # /chat/debug

# search/routes.py  
@limiter.limit("30/minute")   # /search
```

Для виджета ставим `20/minute` — чуть строже, т.к. публичный без аутентификации.
