# Feature Ideas & Hypotheses

Гипотезы о фичах — то, что может быть полезно, но ещё не решено делать.
Записываем идеи сюда, чтобы не потерять. Обсуждаем перед реализацией.

---

## 💡 Гипотезы

### [FI-001] Telegram интеграция
**Статус:** P2 — интересный канал, но не блокер для ядра продукта


**Идея:** Подключить чатбота к Telegram — клиент создаёт бота через BotFather и подключает его к платформе.

**Как может работать:**
- Клиент вводит Telegram Bot Token в дашборде
- Наш backend регистрирует webhook
- Сообщения из Telegram → наш /chat → ответ обратно в Telegram

**Ценность:** Telegram популярен для поддержки клиентов, особенно в СНГ/Европе.

**Вопросы:**
- Нужен ли отдельный бот на каждого клиента или один общий?
- Как обрабатывать медиафайлы (фото, голос)?
- Насколько это востребовано у целевой аудитории?

**Сложность:** Средняя (python-telegram-bot + webhook endpoint)

---

### [FI-002] Свой API ключ OpenAI
**Статус:** DONE (MVP) → продолжение в FI-006 (encrypt + rotation)


**Идея:** Клиент подключает собственный OpenAI API ключ — платформа использует его вместо нашего.

**Как может работать:**
- Поле "OpenAI API Key" в настройках клиента (шифруется в БД)
- При запросе к /chat — используем ключ клиента, а не наш
- Если ключа нет — используем наш (платный тариф)

**Ценность:**
- Клиент контролирует расходы сам
- Снимает с нас риск перерасхода
- Привлекает технических клиентов

**Вопросы:**
- Как хранить ключ безопасно? (шифрование в БД, не в открытом виде)
- Что если ключ невалидный / кончились деньги?
- Это бесплатный тариф или платный?

**Сложность:** Низкая (поле в Client модели + передача в OpenAI вызовы)

---

### [FI-003] Per-user rate limiting
**Статус:** P3 — логично делать вместе с тарифами


**Идея:** Считать лимиты по user_id/API ключу, а не по IP-адресу.

**Когда нужно:** При введении платных тарифов — "100 запросов/день на бесплатном плане".

**Сложность:** Средняя (нужен Redis для счётчиков)

**Зависит от:** Тарифных планов (Stripe)

---

### [FI-004] Redis-backed sliding window rate limiting
**Статус:** P3 — пригодится при высоком трафике, не для раннего MVP


**Идея:** Вместо фиксированного окна (сбрасывается раз в минуту) — скользящее окно.

**Проблема которую решает:** Сейчас можно сделать 30 запросов в 00:59 и ещё 30 в 01:00 — итого 60 за 2 секунды.

**Когда нужно:** При продаже API другим разработчикам, высокий трафик.

**Сложность:** Высокая (Redis + sliding window алгоритм)

**Зависит от:** Реальной нагрузки (пока не актуально)

---

### [FI-005] Приветственное сообщение от бота
**Статус:** P2 — UX-улучшение для виджета


**Идея:** Когда пользователь открывает виджет, бот сам пишет первое сообщение: приветствие + краткая вводная, с чем он может помочь.

**Как может работать:**
- В настройках клиента хранится строка "greeting_message" (по умолчанию что-то вроде: "Привет! Я бот поддержки CDNvideo. Помогу с настройкой CDN-сервисов, API, live-трансляциями и интеграцией с CMS.")
- Виджет сразу показывает это сообщение от роли assistant ещё до первого вопроса.
- Альтернатива: если greeting не задан — использовать стандартный шаблон.

**Ценность:**
- Пользователь сразу понимает зону ответственности бота
- Уменьшает "страх пустого чата" — диалог не начинается с тишины

**Сложность:** Низкая (на уровне виджета + одна настройка в клиенте)

**Зависит от:** Ничего, можно делать отдельно от RAG

---

### [FI-006] ENCRYPTION_KEY rotation / recovery flow
**Статус:** P1 — важно для безопасности, особенно для прод


**Idea:** Provide a safe way to recover from losing the master ENCRYPTION_KEY (or rotate it) without silently breaking all clients.

**How it could work:**
- Admin-only endpoint/UI to:
  - put the app into "maintenance" mode for key rotation;
  - temporarily block new chat/embedding requests;
  - require each client to re-enter their OpenAI API key after a key loss/rotation;
- Optional: support key rotation flow where old keys are decrypted with old key and re-encrypted with new one (requires storing old key temporarily).

**Value:**
- Clear, explicit behavior when ENCRYPTION_KEY is lost or changed;
- No silent 500 errors for end-users; instead, a clear message: "Please re-connect your OpenAI key".

**Complexity:** Medium–High (depends on whether we support true rotation or just "reset all").

**Depends on:**
- Admin/auth story (how we manage operator access to the platform)
- Observability (to detect errors and guide recovery)

---

## ✅ Принято в разработку

### Приоритет P1 (качество ответов)

- [FI-002] Свой API ключ OpenAI — реализовано (MVP), дальше — шифрование и ENCRYPTION_KEY flow (FI-006)
- [FI-006] ENCRYPTION_KEY rotation / recovery flow — важен для продакшена
- [FI-007] Per-client system prompt (RAG instructions)
- [FI-008] Hybrid search (vector + keyword fallback)
- [FI-009] Improved chunking + metadata (headings, doc path, type)
- [FI-010] Feedback on answers (👍/👎) + bad answers report

### Приоритет P2 (интеграции и каналы)

- [FI-001] Telegram интеграция
- [FI-005] Приветственное сообщение от бота

### Приоритет P3 (масштабирование и тарифы)

- [FI-003] Per-user rate limiting
- [FI-004] Redis-backed sliding window rate limiting
- [FI-011] FAQ layer above RAG (ручные Q&A для самых частых вопросов)
- [FI-013] Chat sessions performance (N+1 and pagination)

## ❌ Отклонено
_(пусто)_
