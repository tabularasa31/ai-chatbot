# Fix: CORS — разделить публичные и приватные эндпоинты

## Проблема

В `backend/main.py` один глобальный CORS middleware с фиксированным списком origins:

```python
ALLOWED_ORIGINS = [...]  # только localhost + getchat9.live

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    ...
)
```

Это ломает виджет. Виджет встраивается на сайтах клиентов (`shop.example.com`, `company.io`, etc.) — их домены не в списке, браузер блокирует запросы к `/chat`.

## Что нужно сделать

Разделить CORS на два уровня:

### 1. Публичные эндпоинты — `allow_origins=["*"]`
Эндпоинты, которые вызывает виджет с клиентских сайтов:
- `POST /chat` — основной RAG endpoint (аутентификация через `X-API-Key`, не через CORS)
- `GET /health`
- `GET /embed.js`

### 2. Приватные эндпоинты — `allow_origins=ALLOWED_ORIGINS`
Эндпоинты, которые вызывает только наш дашборд (`getchat9.live`):
- `/auth/*`
- `/clients/*`
- `/documents/*`
- `/embeddings/*`
- `/search/*`
- `/chat/sessions`, `/chat/logs/*`, `/chat/bad-answers`, `/chat/messages/*` (dashboard)
- `/admin/*`

## Как реализовать

Вариант A (рекомендуемый): **два отдельных FastAPI sub-application**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Public app — for widget
public_app = FastAPI()
public_app.add_middleware(CORSMiddleware, allow_origins=["*"], ...)
public_app.include_router(chat_router, prefix="/chat")  # только POST /chat
public_app.include_router(widget_router)

# Private app — for dashboard
private_app = FastAPI()
private_app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, ...)
private_app.include_router(auth_router, prefix="/auth")
private_app.include_router(clients_router, prefix="/clients")
# ... остальные роуты

# Mount
app = FastAPI()
app.mount("/public", public_app)
app.mount("/", private_app)
```

Вариант B (проще): **кастомный CORS middleware** который смотрит на путь запроса

```python
class SplitCORSMiddleware(BaseHTTPMiddleware):
    PUBLIC_PATHS = {"/chat", "/health", "/embed.js"}
    
    async def dispatch(self, request, call_next):
        origin = request.headers.get("origin", "")
        path = request.url.path
        
        is_public = any(path.startswith(p) for p in self.PUBLIC_PATHS)
        # Если публичный — разрешаем любой origin
        # Если приватный — проверяем против ALLOWED_ORIGINS
        ...
```

## Важно

- `/chat` аутентифицируется через `X-API-Key` header, не через сессию/JWT — `allow_origins=["*"]` безопасно
- `allow_credentials=False` оставить для публичных (нельзя `credentials=True` с `origins=["*"]`)
- Не забыть добавить `X-API-Key` в `allow_headers` для публичных роутов

## Файлы для изменения

- `backend/main.py` — основные изменения
- Возможно `backend/chat/routes.py` если нужно разделить роуты дашборда и виджета

## Контекст

Текущий `main.py`:

```python
ALLOWED_ORIGINS = [
    x.strip()
    for x in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,https://getchat9.live",
    ).split(",")
    if x.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
```
