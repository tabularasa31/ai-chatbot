# Product Features Backlog

Продуктовые фичи для клиентов и операторов платформы.
RICE-приоритизация — в `PRODUCT_BACKLOG.md`.

---

## 🔴 P1 — Делаем сейчас

### [FI-005] Greeting message в виджете (RICE: 1440)
- Клиент задаёт `greeting_message` в настройках.
- Виджет показывает его первым при открытии (роль assistant).
- Если не задано → стандартный шаблон.
- **Effort:** 1 день.

### [FI-007] Per-client system prompt (RICE: 1020)
- Клиент настраивает характер бота в дашборде.
- Разные боты для разных клиентов.
- *(подробности в BACKLOG_RAG_QUALITY.md)*

---

## 🔴 P1 — Быстрые wins

### [FI-038] "Powered by Chat9" в подвале виджета
**Идея:** В нижней части каждого виджета — небольшая строка "Powered by Chat9" со ссылкой на getchat9.live.

**Почему это важно:**
- Каждый встроенный виджет = реклама Chat9 на сайте клиента.
- Работает как "Sent from iPhone" — пассивный вирусный маркетинг.
- Бесплатно для нас, минимальная стоимость для клиента.

**Реализация:**
- Добавить в `backend/widget/static/embed.js` строку в низу chat window:
  ```html
  <div style="...">
    Powered by <a href="https://getchat9.live" target="_blank">Chat9</a>
  </div>
  ```
- Стиль: мелкий серый текст, не отвлекает от чата.
- В будущем Premium можно убирать ("Remove branding").

**Effort:** 30 минут.

---

## 🟠 P2 — Следующий спринт

### [FI-009] Improved chunking + metadata (RICE: 420)
- Overlap + структурный chunking.
- *(подробности в BACKLOG_RAG_QUALITY.md)*

### [FI-011 v2] Автоматическая генерация FAQ из тикетов (RICE: 325)
- Не ручной ввод — автогенерация из загруженных тикетов.
- Клиент одобряет/отклоняет предложенные Q&A пары.
- УТП: "Загрузи тикеты → мы сделаем FAQ сами."

### [FI-027] Ticketing systems integration (Zendesk, Intercom, Freshdesk)
- Level 1: импорт тикетов → эмбеддинги (автосинхронизация).
- Level 2: escalation → автосоздание тикета если бот не знает.
- Level 3: live handoff к агенту.
- **Ключевое для западного рынка.**
- **Effort:** 5–8 дней (Level 1).

### [FI-014] Admin metrics (уже реализовано ✅)
- Summary + per-client таблица.
- Токены, сессии, документы, OpenAI key статус.

### [FI-012] Admin dashboard (operator view)
- Расширенный: глобальные логи, % bad answers по клиентам.
- Делать после накопления данных.

---

## 🟡 P3 — Потом

### [FI-001] Telegram интеграция (RICE: 120)
- Клиент вводит Telegram Bot Token → webhook → наш `/chat`.

### [FI-003/004] Rate limiting per-user
- Нужен вместе с тарифными планами (Stripe).

### Stripe / тарифные планы (RICE: 206)
- Free / Premium тарифы.
- Лимиты по запросам, документам, токенам.

---

## ✅ Реализовано

| FI | Что | PR |
|----|-----|-----|
| FI-015 | Email verification | #24 |
| FI-016 | Enforce verification | #26 |
| FI-017 | Brevo HTTP email | #25 |
| FI-018 | Token tracking | #27 |
| FI-014 | Admin metrics MVP | #22 |
| FI-010 | 👍/👎 + Review bad answers | #20, #21 |
| Chat logs | Inbox-style /logs | #19 |
| Review debug | Retrieval debug в /review | — |
