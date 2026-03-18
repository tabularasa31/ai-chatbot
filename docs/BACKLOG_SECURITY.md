# Security Backlog

Безопасность, изоляция, защита от злоупотреблений.

---

## 🟠 P2

### [FI-022] CORS — разделить по роутам
- `allow_origins=["*"]` только для `/chat` и `/embed.js`.
- Остальное — ограничить `FRONTEND_URL`.

### [FI-022 ext] CORS с белым списком доменов клиента
- Клиент указывает `allowed_origins` в дашборде.
- Backend проверяет `Origin` против `Client.allowed_origins` при `/chat`.
- Защита от использования API-ключа на чужих сайтах.
- **Effort:** 2 дня.

### [FI-023] Rate limit на `GET /clients/validate/{api_key}`
- Публичный эндпоинт без rate limit → возможен brute-force.
- Добавить `@limiter.limit("20/minute")`.

### [FI-035] Prompt injection protection
- Санитизация входящих сообщений.
- Проверка на попытки сменить роль бота ("ignore previous instructions...").

---

## 🟡 P3

### [FI-006] ENCRYPTION_KEY rotation
- Безопасное обновление мастер-ключа шифрования OpenAI keys.
- Процедура: decrypt old → encrypt new → без потери данных.
