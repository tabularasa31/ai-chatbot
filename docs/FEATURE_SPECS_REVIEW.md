# Feature Specs Review (2026-03-19)

Елина подготовила 7 детальных спеков для возможных фич. Обзор релевантных для Chat9:

## Status Page Integration ⭐ (ВЫСОКИЙ ПРИОРИТЕТ)

**Файл:** `status-page-spec.docx` (160 KB спеца)

### Ключевые идеи
- Интегрировать в бота данные о статусе сервисов (Statuspage.io, Instatus, etc.)
- Когда пользователь спрашивает "why is X broken" во время инцидента → бот отвечает: "There's an active incident affecting X. Started 14:23 UTC. Status: investigating."
- Автоматический polling каждые 60 сек + webhook support
- Redis cache (TTL 90 сек)
- Relevance classification: инцидент показывается только если он релевантен вопросу

### Как это поможет Chat9
1. **Дифференциатор** — конкуренты (DocsBot, SiteGPT) не имеют real-time incident awareness
2. **Viral value** — каждый инцидент → увеличение вовлечённости бота (люди проверяют статус чаще)
3. **Reduce support tickets** — клиент не получает 100 одинаковых вопросов про один инцидент
4. **Premium feature** — можно закрыть за платной подпиской

### Что нужно сделать
- Implement polling worker + Redis caching (2-3 дня)
- Query-time status check (0.5 дня)
- Tenant dashboard: setup для подключения статуса (1 день)
- Tests + edge cases (1 день)

**Estimated effort:** 5–6 дней

**Приоритет:** FI-041, P2 (после гpt-4o-mini и email verification)

---

## Error Tracking & Observability

**Файл:** `error-tracking-spec.docx`

Про обработку ошибок, логирование, мониторинг. Не critical для MVP, но пригодится для production readiness.

---

## Escalation Flow

**Файл:** `escalation-spec.docx`

Про интеграцию с Zendesk/Intercom когда бот не может ответить. Это уже в нашем roadmap как FI-027.

---

## Knowledge Ingestion

**Файл:** `knowledge-ingestion-spec.docx`

Про загрузку документов, парсинг, обработку. Мы уже имеем базовую версию, но спек может содержать идеи для улучшения (incremental updates, real-time indexing, etc.).

---

## KYC / Disclosure Controls

**Файлы:** `kyc-spec.docx`, `kyc-sdk-spec.docx`, `disclosure-controls-spec.docx`

Про compliance, данные пользователей, соответствие GDPR. Для B2B SaaS важно, но не critical для ранней версии.

---

## Выводы

### Топ 3 идеи для Chat9:
1. **Status Page Integration (FI-041)** — самая ценная, дифференциатор, может быть P1
2. **Escalation to Zendesk (FI-027)** — уже в roadmap, нужна для SMB сегмента
3. **Knowledge Ingestion improvements** — анализ спека может дать идеи для optimize document processing

### Не критично (v2.0):
- KYC / compliance (до того как будем work с enterprise)
- Error tracking (нужен для production, но MVP может работать с базовыми logs)
- Disclosure controls (будущее, когда будет клиентских данных)

---

**Дата:** 2026-03-19
**Автор:** Елина (спеки), подготовил: Assistant
