# Plan — 2026-03-22

> Согласовать с Elina перед запуском. После согласования удалить этот файл и обновить PROGRESS.md.

---

## Контекст

**MVP feature-complete** — все switching cost moats готовы (FI-ESC, FI-DISC, FI-KYC, Gap Analyzer).
Следующий фокус: стабильность + технический долг + начало P2 (retention + growth features).

---

## P1 — Технический долг (делать первым)

### ~~1. FIX: race condition в generate_ticket_number~~ ✅ Done (2026-03-22)
- Промпт-файл удалён после merge (см. `PROGRESS.md`).
- Смержено в `main`; `generate_ticket_number` + `create_escalation_ticket` с retry-циклом

### ~~2. FI-021: Background embeddings (async)~~ ✅ Done (2026-03-22)
- `202 Accepted` немедленно, `BackgroundTasks`, статус: `ready → embedding → ready|error`
- Фронтенд polling каждые 2 сек; live-обновление статуса

### ~~3. FI-026: CI/CD (GitHub Actions)~~ ✅ Done (2026-03-22)
- `.github/workflows/ci.yml`: push to `main` + pull requests to `main` — backend **Ruff** + **pytest** из корня (`tests/`), frontend **eslint** + **next build**
- `backend/ruff.toml`; в `backend/requirements.txt`: **ruff**, **pgvector**
- Прод: merge to `main` после зелёного CI
- Справка: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`docs/06-developer-test-runbook.md`](06-developer-test-runbook.md)

---

## P2 — Продуктовые фичи

### 4. FI-039: Daily Summary Email
- "Chat9 as a team member" — утреннее письмо с итогами дня
- Brevo уже настроен
- Ключевой дифференциатор по стратегии

### 5. FI-040: Client Analytics Dashboard
- Метрики прямо в дашборде: sessions, tokens, cost, top topics, % unanswered
- Нужны данные для Gap Analyzer phase 2

---

## P3 — Позже

_(пусто — фичи перенесены в backlog)_

---

## На обсуждение

~~**Вопрос по FI-021:** делать через FastAPI `BackgroundTasks` (просто, без зависимостей) или через Celery (надёжнее, но сложнее)?~~ → Сделано через `BackgroundTasks`.

**Вопрос по тестированию:** проверить FI-EMBED-MVP на реальном домене (`getchat9.live`) — ждёт действия от admin.

---

## 🚀 Deploy checklist

**Vercel:**
- [x] Settings → Git → Production Branch = `main`
- [x] Settings → Environment Variables → `NEXT_PUBLIC_API_URL` задан для Production (значение: Railway backend URL)

**Railway:**
- [x] Service → Settings → Branch = `main`

**Проверено.** Redeploy на Vercel — только если позже меняли переменные.

---

## 🐛 Баг: NetworkError при сохранении OpenAI key

**Симптом:** При добавлении OpenAI API key в дашборде — `NetworkError when attempting to fetch resource`.

**Это не бэкенд-ошибка** — запрос не дошёл до сервера. Браузерная ошибка сети.

**Проверено:**
- [x] Vercel → Environment Variables → `NEXT_PUBLIC_API_URL` есть (`https://…railway.app`) для production branch `main`
- [x] DevTools → Network → `PATCH /clients/me` — полный URL корректный, ответ от backend
- [x] Относительный `/clients/me` без домена не воспроизводится

**Было (диагностика):** при отсутствии `NEXT_PUBLIC_API_URL` на production `BASE_URL = ""` и запрос уходил на Vercel вместо Railway — отсюда `NetworkError`.
