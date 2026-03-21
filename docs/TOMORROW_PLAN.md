# Plan — 2026-03-22

> Согласовать с Elina перед запуском. После согласования удалить этот файл и обновить PROGRESS.md.

---

## Контекст

**MVP feature-complete** — все switching cost moats готовы (FI-ESC, FI-DISC, FI-KYC, Gap Analyzer).
Следующий фокус: стабильность + технический долг + начало P2 (retention + growth features).

---

## P1 — Технический долг (делать первым)

### 1. FIX: race condition в generate_ticket_number
- Промпт: `cursor_prompts/FIX-ticket-number-race-condition.md`
- Время: ~1 час
- Риск сейчас низкий, но правильно исправить до роста трафика

### 2. FI-021: Background embeddings (async)
- Промпт: нет, нужно написать (или запустить по описанию из `BACKLOG_TECH_DEBT.md`)
- `POST /embeddings/documents/{id}` синхронный → timeout на больших файлах
- 202 Accepted сразу, статус: `pending → embedded`
- Зависимость для demo bots (auto-refresh каждые 48ч)

### 3. FI-026: CI/CD (GitHub Actions)
- Промпт: `cursor_prompts/ci-cd-github-actions.md` — проверить актуальность
- pytest + ruff + eslint на каждый PR
- 160+ тестов без автозапуска = риск

---

## P2 — Продуктовые фичи

### 4. FI-039: Daily Summary Email
- "Chat9 as a team member" — утреннее письмо с итогами дня
- Brevo уже настроен
- Ключевой дифференциатор по стратегии

### 5. FI-040: Client Analytics Dashboard
- Метрики прямо в дашборде: sessions, tokens, cost, top topics, % unanswered
- Нужны данные для Gap Analyzer phase 2

### 6. FI-DISC v2: аудиторные сегменты
- Разные уровни детализации по `audience_tag` из KYC
- Зависимость: стабильный audience_tag во всех каналах

---

## P3 — Позже

- Landing page CTA: URL-first вместо кнопки
- Demo bots (Stripe, Cloudflare) — после FI-021
- Public roadmap

---

## На обсуждение

**Вопрос по FI-021:** делать через FastAPI `BackgroundTasks` (просто, без зависимостей) или через Celery (надёжнее, но сложнее)? При текущем Railway-хостинге BackgroundTasks — оптимальный выбор.

**Вопрос по тестированию:** проверить FI-EMBED-MVP на реальном домене (`getchat9.live`) — ждёт действия от admin.

---

## 🐛 Баг: NetworkError при сохранении OpenAI key

**Симптом:** При добавлении OpenAI API key в дашборде — `NetworkError when attempting to fetch resource`.

**Это не бэкенд-ошибка** — запрос не дошёл до сервера. Браузерная ошибка сети.

**Проверить:**
1. Vercel → Settings → Environment Variables → есть ли `NEXT_PUBLIC_API_URL`?
   - Должно быть: `https://ваш-backend.railway.app`
   - Проверить для обоих окружений: `main` (dev) и `deploy` (production)
2. Если переменная есть — открыть DevTools → Network → найти упавший `PATCH /clients/me` → посмотреть полный URL и статус ответа
3. Если URL выглядит как `/clients/me` без домена — переменная не подтянулась → redeploy на Vercel после проверки

**Скорее всего:** `NEXT_PUBLIC_API_URL` не задан для `deploy` ветки, поэтому `BASE_URL = ""` и запрос идёт на относительный путь которого нет на Vercel.
