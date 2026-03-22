# Chat9 Development Progress

**Last updated:** 2026-03-22 (UTC)  
**Overall status:** ✅ MVP feature-complete, deployed to production

---

## ✅ COMPLETED (2026-03-22)

### Bug fixes & tech debt

- ✅ **FIX: race condition in `generate_ticket_number`** (`fix/ticket-number-race-condition`)
  - Два конкурентных запроса для одного клиента могли оба вычислить одинаковый номер тикета → `IntegrityError` → 500 для одного пользователя
  - `generate_ticket_number()`: `SELECT FOR UPDATE SKIP LOCKED` (advisory lock на PostgreSQL; SQLite игнорирует) + regex `^ESC-(\d+)$` вместо `startswith + int(num[4:])`
  - `create_escalation_ticket()`: retry-цикл max 3 попытки при `IntegrityError` → `db.rollback()` → пересчёт номера; на 3-й неудаче исключение пробрасывается
  - Новые тесты: `test_generate_ticket_number_concurrent_reads_return_same`, `test_create_escalation_ticket_retries_on_integrity_error`, `test_create_escalation_ticket_raises_after_max_retries`; 193/193 тестов прошли
  - Промпт `cursor_prompts/FIX-ticket-number-race-condition.md` — удалить после merge

---

## ✅ COMPLETED (2026-03-21)

### L2 escalation tickets (FI-ESC)
- ✅ **FI-ESC (v1)** — при провале RAG, запросе «человека» или ручном действии создаётся тикет **ESC-####** (per tenant), письмо на email владельца клиента, ответ пользователю формулирует отдельный OpenAI-call с JSON; машинный маркер `[[escalation_ticket:…]]` при необходимости дописывается в коде
- **API:** JWT `GET/POST /escalations`, `GET /escalations/{id}`, `POST /escalations/{id}/resolve`; X-API-Key `POST /chat/{session_id}/escalate`; публично `POST /widget/escalate` + `chat_ended` / `locale` на виджете (см. `backend/routes/widget.py`)
- **UI:** `frontend/app/(app)/escalations/page.tsx`, пункт **Escalations** в навбаре; виджет: **Talk to support**, баннер тикета, блокировка ввода при закрытом чате (`ChatWidget.tsx`)
- **Модель/миграция:** `EscalationTicket`, колонки `Chat` для state machine; `backend/migrations/versions/fi_esc_escalation_tickets.py` (`fi_esc_v1`); модуль `backend/escalation/`
- **QA:** `docs/qa/FI-ESC-escalation-tickets-qa.md`

### Disclosure controls (FI-DISC) — tenant-wide response level
- ✅ **FI-DISC (v1)** — один уровень детализации ответа на весь тенант (**Detailed** / **Standard** / **Corporate**) для всех каналов (виджет, `POST /chat` по X-API-Key); жёсткие лимиты + блок `[Response level: …]` в system-части RAG-промпта (`build_rag_prompt` / `generate_answer`); загрузка `Client.disclosure_config` в `process_chat_message` и `run_debug`
- **Хранение:** `clients.disclosure_config` JSON; каноническое поле **`level`**; при чтении поддерживается алиас **`default_level`**
- **API:** `GET` / `PUT /clients/me/disclosure` (PUT — только для подтверждённого email)
- **UI:** `frontend/app/(app)/settings/disclosure/page.tsx`, пункт навигации **Response controls**, `api.disclosure`
- **Миграция:** `fi_disc_v1` (`backend/migrations/versions/fi_disc_disclosure_config.py`); модуль `backend/disclosure_config.py`; тесты `tests/test_disclosure.py`
- Промпт FI-DISC удалён после merge; **не** в scope v1: блоклист тем, preview, сегменты/KYC по уровню — см. `BACKLOG_PRODUCT.md` (future phases)

### Identity / widget (FI-KYC)
- ✅ **FI-KYC** — идентификация пользователя виджета через **краткоживущий HMAC-токен** (не через `data-*` в embed): `POST /widget/session/init` (`api_key`, опционально `identity_token`), ответ `session_id` + `mode` (`identified` | `anonymous`); контекст в `chats.user_context` (JSON); в LLM попадают только `plan_tier`, `locale`, `audience_tag`
- **Секрет подписи:** `POST/GET/POST` `/clients/me/kyc/secret|status|rotate` (шифрование как у OpenAI key; ротация с перекрытием старого ключа 1 ч); UI: `frontend/app/(app)/settings/widget/page.tsx`, `api.kyc`, пункт навигации **Widget API**
- **Код:** `backend/core/security.py` (`generate_kyc_token`, `validate_kyc_token`), миграция `fi_kyc_user_identification`, таблица `user_sessions` (схема под v2), тесты `tests/test_kyc.py`
- Промпт `cursor_prompts/FI-KYC-user-identification.md` **удалён** после внедрения (описание здесь и в `BACKLOG_PRODUCT.md`)

### Widget / marketing
- ✅ **FI-038** — футер виджета «Powered by Chat9 →» в `frontend/components/ChatWidget.tsx` (ссылка на сайт; prod: iframe-виджет через `backend/static/embed.js` + `/widget`)
- Удалён неиспользуемый legacy-скрипт `backend/widget/static/embed.js` (старый `data-api-key` + `#ai-chat-widget`); README, demo-docs и `docs/03-tech-stack.md` приведены к актуальному embed (`clientId` / `public_id`)

### Search / retrieval
- ✅ **FI-019 ext (FI-008)** — BM25 + RRF гибридный поиск (`rank-bm25`); промпт `FI-019ext-bm25-hybrid-hnsw.md` удалён после внедрения
  - PostgreSQL: `_pgvector_search` (top `2×top_k`) + `bm25_search_chunks` по `chunk_text` → `reciprocal_rank_fusion` (k=60)
  - SQLite (тесты): только Python cosine, без BM25 (как в спеке промпта)
  - Debug API: режим **`hybrid`** на Postgres; на SQLite по-прежнему **vector / keyword** по порогу косинуса
  - Зависимость: `backend/requirements.txt` → `rank-bm25>=0.2.2`

### RAG / embeddings
- ✅ **FI-009** — Sentence-aware chunking + метаданные эмбеддингов (`feature/fi-009-improved-chunking`)
  - `chunk_text()`: границы по предложениям, ~500 символов мягкий лимит, `overlap_sentences`
  - `metadata`: `chunk_index`, `char_offset`, `char_end`, `filename`, `file_type`
  - Промпт `cursor_prompts/FI-009-improved-chunking.md` удалён после внедрения; описание в `BACKLOG_PRODUCT.md` / `BACKLOG_RAG_QUALITY.md`
- ✅ **FI-032 (phase 1)** — document health check: `health_status`, `run_document_health_check`, QA-чеклист `docs/qa/FI-032-document-health-check.md`; промпт `cursor_prompts/FI-032-document-health-check.md` удалён.
- ✅ **FI-034** — LLM-based answer validation (`feature/fi-034-answer-validation`): после `generate_answer()` вызывается `validate_answer()` (gpt-4o-mini, `temperature=0`); при `is_valid=false` и `confidence < 0.4` ответ заменяется на fallback; ошибки валидации не блокируют ответ (`validation_skipped`). Результат в `POST /chat/debug` → `debug.validation`. Промпт `cursor_prompts/FI-034-llm-answer-validation.md` удалён после внедрения.
- ✅ **FI-043** — PII redaction Stage 1 (regex): модуль `backend/chat/pii.py` (`redact` / `redact_text`); в `process_chat_message()` и `run_debug()` перед вызовами OpenAI текст вопроса маскируется (email, телефоны, типичные API-ключи, номера карт → `[EMAIL]`, `[PHONE]`, `[API_KEY]`, `[CREDIT_CARD]`). В `Message.content` сохраняется **оригинал**. Те же регулярки применяются к вопросу в `validate_answer()` (второй вызов LLM). Тесты: `tests/chat/test_pii.py`. Промпт `cursor_prompts/FI-043-pii-redaction-regex.md` удалён после внедрения.

---

## ✅ COMPLETED (2026-03-20 — continued)

### UI & Widget (morning session)
- ✅ **FI-UI: Auth transition + dark brand navbar** (`feature/ui-brand-transition`)
  - AuthTransition: fullscreen #0A0A0F fade ~400ms after login
  - Dark navbar h-12, logo, links, Admin badge, pink ghost Logout
  - email from `api.auth.getMe()` (parallel, no backend changes needed)
- ✅ **FI-UI: Auth pages dark theme** (`feature/auth-pages-dark-theme`)
  - AuthCard/AuthCardCentered unified with AuthShell + cardShell
  - `authStyles.ctaLink` — magenta CTA links
  - forgot-password + verify pages updated
  - Auto-verify by link (no code field — matches current API contract)
- ✅ **Widget rate limiting** (`fix/widget-rate-limiting`)
  - `POST /widget/chat` — 20/min via slowapi
  - 135 tests passed
- Промпты в `cursor_prompts/`: `FI-UI_brand-transition.md`, `FI-UI_auth-pages-dark-theme.md`, `widget-rate-limiting.md` — **удалены** после внедрения (актуальное описание здесь и в `BACKLOG_PRODUCT.md`).

---

## ✅ COMPLETED (2026-03-20 — morning)

### Dependencies & Infrastructure
- ✅ **PyPDF2 → pypdf** migration (branch `chore/deps-pypdf2-openai`)
  - `requirements.txt` (root + backend): removed PyPDF2, added pypdf>=4.0.0, openai>=1.70.0
  - `documents/parsers.py`: `from pypdf import PdfReader`
  - `tests/test_documents.py`: updated PdfWriter to pypdf
  - 135 tests passed

### pgvector Migration
- ✅ **Migration `dd643d1a544a`** — Fix vector column type + HNSW index
  - Added `vector Vector(1536)` column to `embeddings` table
  - Backfill: `(metadata->>'vector')::vector` (note: `->>`  not `->`, json→text→vector)
  - HNSW index: `CREATE INDEX USING hnsw (vector vector_cosine_ops)`
  - Ran successfully on Railway prod DB

### Production Deploy (2026-03-20)
- ✅ `main` → `deploy` → Vercel + Railway auto-deployed
- ✅ Forgot password tested end-to-end (email → reset link → login)
- ✅ All features now live at getchat9.live

---

## ✅ COMPLETED (2026-03-19 — Full Session)

### Security & Code Quality
- ✅ Rate limiting: `/validate` (20/min), `/search` (30/min), `/chat` (30/min)
- ✅ Input validation: `limit/offset` (1-100, ≥0)
- ✅ `m.feedback` None protection
- ✅ `datetime.utcnow()` → `datetime.now(timezone.utc)` (3 files)
- ✅ Broad exceptions → explicit (crypto.py)
- ✅ Exception chaining: `from None` → `from e`
- ✅ N+1 queries fixed (list_sessions, list_bad_answers)
- ✅ pgvector native search — SQL `<=>` instead of Python cosine loop

### Features
- ✅ **FI-EMBED-MVP** — Zero-config widget embedding (CORS solved via iframe)
  - `public_id` on Client model (ch_xxx format)
  - `/embed.js` public endpoint
  - `/widget/chat` public API (no auth, clientId-based)
  - `/widget` iframe page + ChatWidget component
  - Dashboard shows embed code
- ✅ **FI-AUTH: Forgot Password** — Full reset flow
  - `POST /auth/forgot-password` (Brevo email, rate limited 3/hour)
  - `POST /auth/reset-password` (token validation, 1h TTL)
  - Frontend pages: `/forgot-password`, `/reset-password`
  - "Forgot password?" link on login page
- ✅ **FI-UI: Sign in button** — Added to landing page navigation

### Infrastructure
- ✅ Vercel `deploy` branch created — decouple commits from deploys
  - `main` = development (no auto-deploy)
  - `deploy` = production (Vercel listens here)
- ✅ `NEXT_PUBLIC_APP_URL` set on Vercel

---

## 📋 NEXT UP

### Widget Testing:
1. **Test FI-EMBED-MVP on real domain** — waiting for domain admin to update embed script

### Backlog (P1–P2):
2. **FI-021** — Background embeddings (async processing)
3. **FI-026** — CI/CD pipeline (GitHub Actions: pytest + ruff + eslint on PR)
4. **FI-039** — Daily summary email (Brevo)
5. **FI-040** — Client analytics dashboard
6. **FI-041** — Status page integration (real-time incident awareness)

### Medium-term (P3):
6. **CI/CD pipeline** (GitHub Actions: pytest + ruff + eslint on PR)
7. **Langfuse tracing** (LLM observability)
8. **Per-client system prompt**
9. **Multiple file upload**

---

## 📊 FEATURES LIVE IN PRODUCTION

- ✅ Document upload (PDF, Markdown, Swagger, Text)
- ✅ RAG pipeline (OpenAI text-embedding-3-small + gpt-4o-mini; sentence-aware chunking + chunk metadata; regex PII redaction перед внешними вызовами FI-043; post-generation answer validation FI-034)
- ✅ Hybrid retrieval (PostgreSQL: pgvector + BM25 + RRF; SQLite tests: cosine only)
- ✅ pgvector native search (SQL cosine_distance, HNSW index)
- ✅ Multi-tenant isolation (client_id scoping)
- ✅ Chat widget (embeddable, ~6KB vanilla JS)
- ✅ Zero-config widget embed (public_id + iframe)
- ✅ **Response controls (FI-DISC v1):** tenant-wide detail level (Detailed / Standard / Corporate), dashboard **Response controls**
- ✅ Optional **identified widget sessions** (FI-KYC): HMAC identity token + `/widget/session/init`, signing secret in dashboard
- ✅ Widget footer «Powered by Chat9 →» (FI-038)
- ✅ Dashboard (documents, logs, feedback, analytics)
- ✅ Document health check (phase 1): `health_status`, GPT-structured analysis, re-check API
- ✅ Email verification (Brevo)
- ✅ Forgot password flow (Brevo) — tested end-to-end
- ✅ Admin metrics
- ✅ Chat logs with feedback (👍/👎)
- ✅ Bad answers review + training
- ✅ **L2 escalation tickets (FI-ESC):** inbox `/escalations`, виджет Talk to support, тикеты при low-similarity / no-docs / human request / manual escalate
- ✅ Landing page (getchat9.live)
- ✅ Sign in button on landing page
- ✅ CORS security (whitelist)
- ✅ Rate limiting (chat, search, validate, widget/chat)

---

## 🏗️ INFRASTRUCTURE

```
User → getchat9.live (Vercel, Next.js)
     ↘ ai-chatbot-production-6531.up.railway.app (FastAPI)
       ↘ PostgreSQL 15 + pgvector
       ↘ OpenAI API (embeddings + gpt-4o-mini)
       ↘ Brevo (transactional email)

Git branches:
  main   → development (no auto-deploy)
  deploy → production (Vercel + Railway listen here)
```

---

## ⚠️ KNOWN ISSUES / TECH DEBT

| Issue | Priority | Notes |
|-------|----------|-------|
| FI-EMBED-MVP real-domain test | 🟡 P1 | Waiting for admin to update embed script |
| Static Stats on landing page | 🟡 P2 | Hardcoded, connect real API later |
| No CI/CD pipeline | 🟡 P2 | GitHub Actions needed |
| Footer links hardcoded | 🟢 P3 | Update when docs site ready |

---

## 📎 Cursor prompts (`cursor_prompts/`)

Реализованные промпты удаляются из каталога после merge; описание фичи остаётся здесь и в `BACKLOG_*`.

**Сейчас в репозитории:** `_TEMPLATE_cursor-prompt.md`; `FI-007-per-client-system-prompt.md`; `FI-ESC-escalation-tickets.md` (архив спеки; реализация — блок **L2 escalation (FI-ESC)** выше); `ci-cd-github-actions.md`; `FIX-ticket-number-race-condition.md` (удалить после merge `fix/ticket-number-race-condition`). Промпт FI-DISC удалён после внедрения — описание: блок **Disclosure controls (FI-DISC)** выше и `docs/IMPLEMENTED_FEATURES.md`.

---

## 📚 Реестр фич vs бэклог

| File | Contents |
|------|---------|
| **`IMPLEMENTED_FEATURES.md`** | **Implemented features registry** (English, by area, links to code/API); extend on major releases |
| `BACKLOG_PRODUCT.md` | Product features (FI-xxx), RICE scored |
| `BACKLOG_TECH_DEBT.md` | Tech improvements |
| `BACKLOG_SECURITY-IMPROVEMENTS.md` | Security: vectorDB filter, rate limiting, tracing |
| `BACKLOG_EMBED-PHASE2.md` | Widget Phase 2/3 (embed.js, mobile, CSP; **tier-2** limits after baseline slowapi) |
| `BACKLOG_RAG_QUALITY.md` | RAG quality: chunking, re-ranker |
| `BACKLOG_MONETIZATION.md` | Pricing strategy |

---

_Updated: 2026-03-22_
