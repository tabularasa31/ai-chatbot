# Market Research: AI Support Bot / Doc-based Chatbot SaaS

Исследование рынка и конкурентного ландшафта для Chat9.
Дата: 2026-03-18.

---

## Рынок

### Категория
"Doc-based AI chatbot" — чатбот, который обучается на документации клиента и отвечает на вопросы пользователей. Ближайшая родительская категория: AI Customer Support / Live Chat.

### Размер рынка
- Рынок AI Customer Service активно растёт на 2025–2026.
- Крупные игроки (Intercom Fin, Tidio) уже имеют 10k+ клиентов.
- Есть специализированная ниша "docs-trained chatbot" (Chatbase, DocsBot) — быстрорастущий сегмент.
- ICP Chat9: малый и средний B2B SaaS, API/tech продукты — рынок огромный.

---

## Конкурентный ландшафт

### Сегмент 1: Enterprise AI Support Agents

**Fin by Intercom** (fin.ai)
- Позиционирование: #1 AI Agent for customer service.
- Цена: **$0.99 за resolved conversation**.
- Интеграции: любой helpdesk (Zendesk, Salesforce, HubSpot).
- Resolution rate: ~65% end-to-end.
- Целевая аудитория: средний и крупный бизнес.
- **Vs Chat9:** несравнимо дороже, требует helpdesk, сложный onboarding.

**DataDome**
- Позиционирование: Enterprise bot protection + fraud prevention.
- Цена: $1000+/mo.
- Целевая аудитория: крупные e-commerce и media.
- **Vs Chat9:** другая ниша (защита от ботов, не поддержка).

---

### Сегмент 2: SMB Live Chat + AI

**Tidio** (tidio.com)
- Позиционирование: AI + human customer service platform.
- Цены:
  - Starter: **$24/mo** (100 conversations)
  - Growth: **от $49/mo** (250+ conversations)
  - Plus: **от $749/mo** (custom)
- Фичи: live chat, tickets, AI bot (Lyro), Zendesk/Salesforce интеграция.
- Целевая аудитория: e-commerce, SMB.
- **Vs Chat9:** Tidio — общая live chat платформа, не RAG на доках. Дороже при масштабе.

**Crisp** (crisp.chat)
- Позиционирование: flat rate per workspace, всё включено.
- Цены: не показывает публично, flat monthly.
- Фичи: live chat, AI, мультиканал.
- **Vs Chat9:** похоже на Tidio, общая платформа без фокуса на документацию.

---

### Сегмент 3: Doc-based Chatbot (прямые конкуренты)

**Chatbase** (chatbase.co)
- Позиционирование: "Train ChatGPT on your data".
- Цены: не показывает публично, 10,000+ клиентов.
- Фичи: upload docs → chatbot → embed на сайт.
- **Vs Chat9:** самый близкий конкурент. Нет conversation logs, нет feedback loop, нет "your OpenAI key" модели.

**DocsBot AI** (docsbot.ai)
- Позиционирование: AI chatbot trained on your documentation.
- Цены:
  - Free: 1 бот, 50 страниц, 100 сообщений/мес.
  - Personal: **$19/mo** — 3 бота, 5k страниц, 5k сообщений.
  - Standard: **$49/mo** (most popular) — 10 ботов, 15k страниц, 15k сообщений.
  - Business: **$99/mo** — 100 ботов, 100k страниц, unbranded widget.
- Фичи: Help Scout интеграция, analytics, conversation summaries, MCP server.
- **Vs Chat9:** очень близкий конкурент. Сильнее по интеграциям. Слабее по RAG quality controls (нет нашего 👍/👎 pipeline, нет debug mode).
- **Важно:** "Unbranded widget" только в Business ($99/mo) — наша FI-038 это дифференциатор.

**SiteGPT** (sitegpt.ai)
- Позиционирование: AI customer support agent from your website/docs.
- Цены:
  - Starter: **$39/mo**
  - Growth: **$79/mo**
  - Scale: **$259/mo**
  - Enterprise: custom
- Zendesk escalation поддерживается.
- **Vs Chat9:** похожий продукт, дороже. Нет явного feedback/quality loop.

---

## Позиционирование Chat9

### Место на рынке

```
                  ДОРОГО
                     │
Enterprise ──── Fin ($0.99/conv) ──── DataDome ($1k+)
                     │
         Tidio ($24–749) ─── Crisp
                     │
         SiteGPT ($39–259) ─── DocsBot ($19–99)
                     │
      ★ CHAT9 (free/freemium) ─── Chatbase
                     │
                  ДЁШЕВО
```

### Наши дифференциаторы

1. **"Your OpenAI key"** — ты платишь OpenAI напрямую, мы не накручиваем за токены. DocsBot и SiteGPT включают AI costs в цену подписки (фактически — скрытая наценка).

2. **Feedback loop** (👍/👎 + ideal_answer + training data) — у конкурентов этого нет в таком виде. Это путь к самообучающемуся боту.

3. **Debug mode** — видно какие чанки были использованы. Нет у прямых конкурентов.

4. **"Powered by Chat9" / branding** — DocsBot берёт $99/mo за unbranded виджет. У нас это будет Premium фича.

5. **Простота** — 5 минут до первого ответа. Нет сложных helpdesk интеграций для базового use case.

---

## Слабые стороны Chat9 vs конкуренты

| Аспект | Конкуренты | Chat9 сейчас |
|--------|-----------|-------------|
| Интеграции (Zendesk, HubSpot) | DocsBot, SiteGPT, Fin | ❌ Нет (FI-027 в roadmap) |
| Multi-user / team | DocsBot, Tidio | ❌ Нет |
| Analytics / charts | DocsBot, Tidio | ❌ Базовые метрики |
| Custom widget design | Все | ❌ Нет |
| Количество ботов | DocsBot: 3–100 | 1 бот = 1 аккаунт |
| Brand awareness | Chatbase: 10k+ клиентов | 🆕 Новый игрок |

---

## Вывод: где Chat9 может выиграть

**Сейчас (до интеграций):**
- Технические B2B команды которые хотят контроль над AI costs (свой ключ).
- Команды которые хотят понимать качество ответов (debug + feedback loop).
- Небольшой SaaS с одним продуктом, одним ботом.

**После (с Zendesk + multi-tenant + branding):**
- Конкурировать с DocsBot и SiteGPT напрямую по цене и функциям.
- Позиционирование: "DocsBot but with better quality controls and transparent pricing."

---

## Источники

- fin.ai (Intercom Fin) — прямой сайт, март 2026
- tidio.com/pricing — прямой сайт, март 2026
- docsbot.ai/pricing — прямой сайт, март 2026
- sitegpt.ai/pricing — прямой сайт, март 2026
- chatbase.co — прямой сайт, март 2026
- crisp.chat/en/pricing — прямой сайт, март 2026
