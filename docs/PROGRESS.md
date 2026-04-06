# Chat9 Development Progress

**Last updated:** 2026-04-06 (UTC) — controlled clarification layer MVP, widget quick replies, clarification-aware debug metadata
**Overall status:** ✅ MVP feature-complete, deployed to production

---

## ✅ COMPLETED (2026-04-06) — controlled clarification layer MVP

### Typed chat outcomes + clarification policy

- ✅ **Typed chat outcomes** (`backend/chat/service.py`, `backend/chat/schemas.py`): chat turns now resolve to `answer`, `clarification`, or `partial_with_clarification` instead of treating every assistant turn as plain answer text.
- ✅ **Canonical additive response contract:** `POST /chat` and `POST /widget/chat` now return canonical `text`, `message_type`, and optional structured `clarification`, while keeping legacy `answer` / `response` aliases for compatibility.
- ✅ **Deterministic clarification policy:** clarification is considered only after the normal guard/FAQ/retrieval pipeline and only for narrow MVP triggers: ambiguous intent, missing critical slot, or low retrieval confidence.
- ✅ **Safe partial-answer boundary:** `partial_with_clarification` is limited to approved safe sources rather than free-form “weak answer” generation.

### Multi-turn clarification flow

- ✅ **Bounded clarification state** (`chats.user_context["clarification_state"]`): minimal versioned flow state stored without introducing a new persistence model.
- ✅ **Continuation vs new-intent detection:** pending clarification replies are classified deterministically; continuation replies are synthesized with the original question and clarification prompt, then re-enter the standard chat decision pipeline.
- ✅ **Flow cleanup guarantees:** clarification state is cleared on resolution, supersede/new intent, and forced best-effort completion.
- ✅ **Clarification turn limit:** one clarification turn per active clarification flow in MVP; if unresolved after the limit, the bot must return best-effort instead of asking again.

### Widget + debug + tests

- ✅ **Widget quick replies:** the widget renders clarification options as quick replies only on the latest assistant clarification turn; button clicks send both visible label text and structured `option_id` when available.
- ✅ **Debug metadata:** `/chat/debug` now exposes clarification-aware decision fields such as `clarification_considered`, `message_type`, `clarification_reason`, `clarification_type`, and `safe_partial_source_type`.
- ✅ **Regression coverage:** added backend and widget tests for clarification branching, continuation vs new intent, malformed clarification state, cleanup behavior, schema invariants, and quick replies.

### Docs sync

- ✅ Updated `AGENTS.md`, `docs/04-features.md`, `docs/03-tech-stack.md`, `docs/06-developer-test-runbook.md`, `docs/07-observability-rollout.md`, `docs/IMPLEMENTED_FEATURES.md`, and Russian chat/widget docs to reflect the shipped clarification flow.

---

## ✅ COMPLETED (2026-04-06) — smart OpenAPI ingestion for files and URLs

### OpenAPI / Swagger ingestion pipeline

- ✅ **Structured OpenAPI parsing** (`backend/documents/parsers.py`): `.json`, `.yaml`, and `.yml` OpenAPI/Swagger files now enter a dedicated semantic ingestion path instead of being embedded as raw machine-readable text.
- ✅ **Semantic validation:** uploaded or fetched structured payloads must parse as JSON/YAML objects, expose OpenAPI/Swagger markers, and contain at least one supported HTTP operation under `paths`.
- ✅ **URL auto-detection** (`backend/documents/url_service.py`): URL sources that return structured JSON/YAML with OpenAPI signals are routed through the same `swagger` pipeline instead of generic page ingestion.
- ✅ **Strict invalid-OpenAPI handling for structured URLs:** JSON/YAML blobs that look like OpenAPI but fail semantic validation now return a clear invalid-OpenAPI error instead of being silently indexed as generic text.

### Retrieval-friendly chunking

- ✅ **Operation-aware chunks** (`backend/embeddings/service.py`): OpenAPI knowledge is now indexed as one primary chunk per API operation (`method + path`) rather than generic sentence chunks over the whole spec.
- ✅ **Stable endpoint rendering:** primary chunks include endpoint, operation ID, summary, description, tags, deprecated status, auth, parameters, request body summary, responses, example call, and selected `x-...` extensions.
- ✅ **Rich schema detail chunks:** large or schema-rich operations now produce additional `request_schema` and `response_schema` chunks for better retrieval on payload structure questions.
- ✅ **Nested field extraction:** schema detail chunks include required fields, top-level properties, one-level nested properties for rich config objects, and flattened `field path: ...` lines for точечные developer-style queries like `cache.disable` or `origin.hostname`.
- ✅ **Embedding metadata upgrade:** OpenAPI chunks now persist retrieval metadata such as `path`, `method`, `operation_id`, `deprecated`, `content_types`, `response_codes`, `auth_schemes`, `source_kind`, and `source_format`.

### Reliability hardening + coverage

- ✅ **Ref/cycle protection:** local `$ref` resolution now has clearer cycle/depth guards for complex `allOf` / schema graphs, with graceful degradation instead of runaway expansion.
- ✅ **Structured ingestion hardening:** OpenAPI URL ingestion avoids double parsing, reuses normalized chunk objects directly, and sanitizes reserved OpenAPI chunk sentinels inside rendered examples.
- ✅ **Regression coverage:** added tests for YAML uploads, OpenAPI URL auto-detection, invalid structured payload handling, rich operation chunk splitting, round-trip render/extract behavior, unchanged-content short-circuiting, and cyclic `allOf/$ref` schemas.

### Docs + QA sync

- ✅ **Product/docs/demo sync:** updated core docs, Russian docs, demo docs, and QA plans to document OpenAPI file support, semantic endpoint chunking, request/response schema detail chunks, and URL auto-detection behavior.

---

## ✅ COMPLETED (2026-04-01) — aligned `/chat/debug` with public chat pipeline + soft rejection texts

### `run_chat_pipeline` — shared pure pipeline

- ✅ **`ChatPipelineResult` dataclass** (`backend/chat/service.py`): structured result covering `user_output` (raw_answer, final_answer, tokens_used), `decision` (strategy, reject_reason, flags, validation_outcome), `retrieval`, `validation`, `escalation` (compute-only), and `faq_match` for debug.
- ✅ **`run_chat_pipeline`** — pure function with invariant stage order: injection → embed → FAQ → relevance → retrieve → low-retrieval guard → generate → validate → escalation decision. No DB writes, no escalation side effects, no observability.
- ✅ **`process_chat_message`** refactored to call `run_chat_pipeline` for the Normal RAG branch; escalation state machine and all side effects (DB persist, ticket creation, trace) remain in `process_chat_message`.
- ✅ **`run_debug`** replaced with a call to `run_chat_pipeline`; now mirrors all guard/FAQ/RAG decisions of the public chat without persisting anything.

### Debug response enhancements

- ✅ **`ChatDebugResponse`** gains `raw_answer` field — shows what the LLM generated before validation fallback.
- ✅ **`DebugInfoResponse`** gains `strategy`, `reject_reason`, `is_reject`, `is_faq_direct`, `validation_applied`, `validation_outcome`.

### Soft rejection texts (`backend/guards/reject_response.py`)

- ✅ **`RejectReason.INSUFFICIENT_CONFIDENCE`** added for validation-fallback path.
- ✅ **Out-of-domain bucket** (`NOT_RELEVANT`, `LOW_RETRIEVAL_SCORE`): «Извините, но я не могу помочь с этим вопросом. Я могу ответить на вопросы по {product_name} или его настройкам[, например про {topic_hint}].»
- ✅ **Injection bucket** (`INJECTION_DETECTED`): «Извините, но я не могу помочь с этим запросом. Я могу ответить на вопросы по {product_name}, если нужно.»
- ✅ **Low-confidence bucket** (`INSUFFICIENT_CONFIDENCE`): «Сейчас у меня недостаточно информации, чтобы надёжно ответить. Попробуйте уточнить вопрос или задать его иначе[, например про {topic_hint}].»
- ✅ All texts use `product_name` from `TenantProfile` with `"данному продукту"` fallback.

### Tests

- ✅ `test_chat_no_embeddings` updated to expect new INSUFFICIENT_CONFIDENCE text.
- ✅ `test_debug_no_embeddings` updated; now also asserts `validation_outcome == "fallback"`.
- ✅ Added: `test_build_reject_response_*` (6 cases for all rejection semantics).
- ✅ Added: `test_run_chat_pipeline_injection_detected`, `test_run_chat_pipeline_not_relevant`.
- ✅ Added: `test_run_chat_pipeline_validation_fallback_uses_insufficient_confidence_text`.
- ✅ Added: `test_run_debug_does_not_create_db_records` (no Chat/Message written).
- ✅ Added: `test_run_debug_guard_reject_shows_strategy_and_reject_reason`.
- ✅ Added: `test_chat_debug_endpoint_exposes_pipeline_fields`.

### Follow-up fixes (same session)

- ✅ **Single injection detection** — `run_chat_pipeline` gains optional `precomputed_injection` param; `process_chat_message` passes its already-computed `injection_result` so `detect_injection` is called exactly once per request, not twice.
- ✅ **Missing test imports** — `from backend.guards.reject_response import RejectReason, build_reject_response` added to `tests/test_relevance_guard.py`; updated assertions were failing with `NameError` before this fix.

---

## ✅ DOCS / CONFIG (2026-03-29) — full trace capture mode (`FULL_CAPTURE_MODE`)

- ✅ **`FULL_CAPTURE_MODE`:** documented env flag (default `true`) that short-circuits adaptive sampling for 100% trace capture; `false` preserves existing tenant / high-volume heuristics.
- ✅ **Langfuse annotations:** documented metadata `sampling_mode` and `sampling_mode:*` tags for filtering.
- ✅ **`docs/07-observability-rollout.md`:** Railway env list + “Trace sampling” section + AC-12 note.
- ✅ **`docs/04-features.md`:** FI-115 observability subsection points to config/service paths and rollout doc.
- ✅ **`docs/IMPLEMENTED_FEATURES.md`:** registry row for the capability.

## ✅ COMPLETED (2026-03-28) — contradiction reliability policy

- ✅ **Corroboration-based contradiction cap:** contradiction evidence now remains visible in canonical retrieval reliability, but a single contradiction fact stays evidence-only instead of forcing `low`.
- ✅ **Multiplicity threshold:** contradiction now caps reliability to `low` only when corroborated by multiple facts on one logical overlap pair or by contradiction across multiple logical overlap pairs.
- ✅ **Canonical logical-pair handling:** mirrored `(a, b)` / `(b, a)` contradiction emissions no longer double-count for thresholding or dedupe.
- ✅ **Precedence + reason semantics:** contradiction short-circuits overlap capping once threshold is reached, and `cap_reason="contradiction"` remains visible even when the base score was already `low`.
- ✅ **Docs + regression sync:** updated product docs, feature registry, and search/chat tests to match the new contradiction policy.

## ✅ COMPLETED (2026-03-29) — contradiction observability projection

- ✅ **Projection-only contradiction metrics:** `build_reliability_projection()` now emits `contradiction_detected`, `contradiction_count`, `contradiction_pair_count`, and `contradiction_basis_types` without changing canonical reliability semantics.
- ✅ **Canonical-source invariant:** derived metrics read only final canonical contradiction evidence under `reliability.evidence.contradiction.pairs`; the historical `pairs` name remains documented as flat fact-level entries.
- ✅ **Trace/debug propagation:** the new fields now appear automatically in `source-overlap-check`, root metadata for `/search` and chat traces, and `run_debug()` output through the shared projection path.
- ✅ **Regression coverage + docs sync:** added projection semantics tests for count vs pair-count, stable basis ordering, mirror-dedup behavior, and propagation coverage across search/chat/debug surfaces.

## 📄 DOCS (2026-03-29) — contradiction LLM adjudication (two-source model)

- ✅ **`docs/04-features.md`:** documented optional shadow adjudication, the split between canonical `evidence.contradiction_adjudication` (only when a batch was sent) vs `contradiction_adjudication_observability` (not serialized; skip/completed status for traces), and discipline for metrics vs product scoring.
- ✅ **`docs/07-observability-rollout.md`:** noted that `contradiction_adjudication_*` projection fields are tied to the observability path; pointer to product doc.

---

## ✅ COMPLETED (2026-03-28) — symmetric BM25 variant evaluation

- ✅ **Explicit BM25 expansion policy:** retrieval now supports `BM25_EXPANSION_MODE` with `asymmetric` as the default and `symmetric_variants` as the opt-in lexical expansion path.
- ✅ **Lexical-safe symmetric BM25 path:** BM25 can now evaluate the normalized lexical-safe variant set over the shared vector-built candidate corpus, merge hits deterministically before RRF, and keep `has_lexical_signal` semantics tied to the final merged lexical branch output.
- ✅ **Expanded lexical observability:** traces now record BM25 expansion mode, lexical variant-eval counts, extra BM25 eval work, merged hit counts before/after cap, and compact winner provenance in the `bm25-search` span.
- ✅ **Regression coverage + PG verification:** added unit/chat/pgvector coverage for deterministic lexical merge behavior, no-effective-change controls, cap interaction, and PG-path symmetric lexical evaluation.
- ✅ **Docs sync:** product docs and FI-115 runbook now describe the vector/BM25 role split and the asymmetric-vs-symmetric evaluation gate.

---

## ✅ COMPLETED (2026-03-28) — query-variant retrieval observability

- ✅ **FI-115 instrumentation:** retrieval now records query-variant fan-out and added work in traces: variant count/mode, extra embedded inputs, extra embedding API requests, extra vector-search calls, and retrieval/embedding/vector stage durations.
- ✅ **Trace coverage parity:** direct `POST /search` requests now emit lightweight root traces, so query-cost measurements are available outside chat flow too.
- ✅ **Tagging fix:** variant tags now merge with existing tenant tags for both sampled and deferred traces, preserving tenant-level segmentation.
- ✅ **Docs + runbook:** observability rollout notes updated; added `docs/qa/FI-115-query-variant-cost.md` as the production evidence template for p50/p95 single-vs-multi review.

---

## ✅ COMPLETED (2026-03-27) — URL-source page deletion in Knowledge

- ✅ **Granular source-page deletion:** users can now delete a single indexed URL-derived page from an expanded Knowledge source without deleting the whole source.
- ✅ **Persistent refresh protection:** manually deleted page URLs are stored on the source and skipped by later crawler refreshes, so removed pages do not silently come back.
- ✅ **Backend contract:** added `DELETE /documents/sources/{source_id}/pages/{document_id}` with ownership/source validation and source aggregate recalculation after deletion.
- ✅ **Regression coverage:** added API tests for successful page deletion, source mismatch rejection, and “deleted page does not reappear on refresh”.
- ✅ **Docs + QA sync:** updated feature docs and QA checklists to cover per-page deletion behavior.

---

## ✅ COMPLETED (2026-03-26) — Knowledge inline source detail + shared capacity 100

- ✅ **Knowledge UI refinement:** URL-source details moved out of the narrow right sidebar into an inline expandable row under the selected source. The main list row now carries the key operational fields: status, indexed progress, schedule, health / warnings, and row actions (`Edit`, `Refresh`, `Delete`).
- ✅ **Simplified source detail:** the expanded source panel now focuses on recent runs and exclusions instead of duplicating primary metadata in a separate side panel.
- ✅ **Shared knowledge capacity:** the old split assumptions (`max 20` uploaded files, fixed per-source page cap) were replaced with a single client-wide capacity of `100` documents across uploaded files and indexed URL pages together.
- ✅ **Crawler capacity semantics:** URL-source indexing now respects remaining client capacity while still allowing refreshes to update already indexed pages for the same source.
- ✅ **Docs + QA sync:** updated product docs and QA checklists to match the inline Knowledge UX and the new shared-capacity behavior.

---

## ✅ COMPLETED (2026-03-25) — URL sources v1 hardening + documentation sync

- ✅ **FI-URL v1 hardening:** URL-source crawler now validates public hostnames/IPs, blocks SSRF targets (localhost, private, loopback, link-local, reserved ranges), validates redirects hop-by-hop, ignores env proxies, and returns clearer upstream error messages for `404`, auth-protected URLs, `5xx`, and oversized responses.
- ✅ **API contract tightening:** URL source schemas now validate `url` as `AnyHttpUrl`; schedule is restricted to `daily | weekly | manual`; route layer normalizes typed URLs before handing them to the service.
- ✅ **Knowledge routes cleanup:** `GET /documents/sources` now matches before UUID document routes and returns `404 Client not found` consistently; URL-source detail loads the latest 5 runs via SQL instead of Python-side sorting.
- ✅ **Regression coverage:** added tests for SSRF blocking, redirect-to-localhost protection, stricter schema validation, knowledge-source route access, and recent-run ordering.
- ✅ **Docs + QA:** added a dedicated QA checklist for URL sources at `docs/qa/FI-URL-url-sources-v1.md` and synced core product docs.

---

## ✅ COMPLETED (2026-03-24) — Coverage hardening + dev test runbook

- ✅ **Coverage hardening (high-risk zones):** added regression tests for escalation state machine transitions, manual escalation endpoint (`/chat/{session_id}/escalate`), auth forgot/reset flow, and RAG retrieval edge/error paths.
- ✅ **Search API error contract:** `POST /search` now returns `503` when OpenAI embeddings call fails (`APIError`) instead of leaking an internal failure mode.
- ✅ **Developer runbook:** added `docs/06-developer-test-runbook.md` with grouped local/CI commands (`P0 smoke`, `auth reset`, `escalation`, `RAG edge`, `pgvector`, coverage snapshot) and linked from core docs.

---

## ✅ COMPLETED (2026-03-23) — Landing live demo chat

### Live chat demo on landing page (feature/landing-demo-chat)

- ✅ **DemoBlock** — заменён статичный макет чата на `DemoChat`: живые API-запросы к `/widget/chat`, тёмная цветовая схема лендинга (`#2D2D44` / `#38BDF8` / `#E879F9`), аватары у сообщений бота, typing-indicator (три точки). Анимация появления переведена на `whileInView + once: true` — не сбрасывается при скролле. Скролл сообщений происходит внутри контейнера чата, не прокручивает страницу.
- ✅ **Proxy routes fix** — `frontend/app/widget/chat/route.ts` и `escalate/route.ts`: frontend product seam now accepts `botId` and still understands legacy `clientId`; при проксировании на бэкенд значение уходит как `client_id` (FastAPI ожидает snake_case). Без этого все запросы возвращали 422.
- ✅ **ChatWidget error handling** — добавлена `formatApiDetail`: корректно читает `detail` из FastAPI-ответа в любом формате (строка, массив validation objects). Устранён `[object Object]` в сообщениях об ошибках.
- ✅ **Config** — `NEXT_PUBLIC_LANDING_DEMO_BOT_ID` is now the preferred env for the landing demo public bot ID; legacy `NEXT_PUBLIC_LANDING_DEMO_CLIENT_ID` still works as a compatibility alias. При отсутствии — fallback-заглушка без падения страницы.
- **Setup:** set `NEXT_PUBLIC_LANDING_DEMO_BOT_ID=ch_...` (public bot ID from the dashboard embed snippet). Legacy `NEXT_PUBLIC_LANDING_DEMO_CLIENT_ID=ch_...` still works in `.env.local` and Vercel env until fully migrated.

---

## ✅ COMPLETED (2026-03-22) — UI redesign session

### Sidebar navigation & design system (feat/sidebar-navigation-redesign)

- ✅ **UI-NAV: Sidebar layout** — все навигационные ссылки перенесены из navbar в фиксированный левый сайдбар (200px). Navbar: только Chat9, email, Logout. Sidebar: иконки, группировка секций (main nav / SETTINGS / Admin), активное состояние через `usePathname`. Navbar сделан `fixed top-0 z-100` — не уезжает при скролле.
- ✅ **UI-NAV: Knowledge hub** (`/knowledge`, бывший `/documents`) — единая страница: карточки внешних источников (GitHub + coming soon: Confluence, Notion, URL Crawler) + единая таблица всех проиндексированных источников (файлы, будущие git/url строки) с type-бейджами, health-индикатором, действиями Delete/Re-check.
- ✅ **UI-NAV: Agents page** (`/settings`) — новая страница управления OpenAI API key (перенесена с Dashboard). Пункт **Agents** в секции SETTINGS сайдбара. С Dashboard убраны форма ключа и Quick links; при отсутствии ключа — amber-баннер со ссылкой на `/settings`.
- ✅ **UI-NAV: Design system** — единый стиль по всем app-страницам (dashboard, knowledge, agents, logs, review, escalations, debug, response controls, widget api):
  - Карточки: `rounded-xl border border-slate-200` (без `shadow-md`)
  - Primary button: `bg-violet-600 hover:bg-violet-700 rounded-lg transition-colors`
  - Secondary button: `bg-slate-100 hover:bg-slate-200 rounded-lg`
  - Текстовые ссылки: `text-violet-600`
  - Подзаголовки страниц: `text-slate-500 text-sm`
  - Инпуты/textarea: `border-slate-200 rounded-lg focus:border-slate-400 outline-none`
  - Error banners: `bg-red-50 border border-red-100 rounded-lg`
  - Заголовки секций (h2): `text-base font-semibold text-slate-800`
  - Active radio (Response controls): `border-violet-400 bg-violet-50/50`
- ✅ **middleware.ts** — добавлены `/knowledge` и `/settings` в список защищённых маршрутов
- **QA:** `docs/qa/UI-NAV-sidebar-redesign-qa.md`

### Documentation sync (registry + product docs)

- ✅ **`IMPLEMENTED_FEATURES.md` / `PROGRESS.md`** — путь UI для FI-021: `knowledge/page.tsx` (старый `/documents` удалён)
- ✅ **`docs/04-features.md`** — embed docs now distinguish public bot terminology from the legacy `clientId` compatibility seam (`embed.js?clientId=…`, optional `Chat9Config.widgetUrl`); Dashboard section names stay aligned with UI-NAV (Knowledge, Agents, sidebar); Admin — в сайдбаре
- ✅ **`demo-docs/04-dashboard-features.md`** — Knowledge hub, Agents (`/settings`), навигация через sidebar
- ✅ **`README.md`** — формулировка про Dashboard / Knowledge hub

---

## ✅ COMPLETED (2026-03-22)

### Bug fixes & tech debt

- ✅ **FI-026: GitHub Actions CI** (в `main`; промот в `deploy` через PR)
  - [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): on `push` / `pull_request` to **`main`** and **`deploy`** — job **Backend (pytest + ruff)** (Python 3.11): `pip install -r requirements.txt`, `ruff check backend`, `pytest tests/ -q --cov=backend --cov-report=term-missing` (SQLite test env в workflow); job **Frontend (eslint + build)** (Node 20): `npm ci`, `npm run lint`, `npm run build` с `NEXT_PUBLIC_API_URL=https://ci.invalid`
  - [`backend/ruff.toml`](../backend/ruff.toml): E/F/W; `extend-exclude` migrations; per-file `E402` для поздних импортов в `main.py` и `chat/service.py`
  - [`requirements.txt`](../requirements.txt): `ruff>=0.3.0`, `pgvector>=0.2.0` (импорт `backend.models` в тестах)
  - [`tests/test_admin_metrics.py`](../tests/test_admin_metrics.py) — `public_id` / `owner_email` / `has_openai_key`; мелкий фикс `f`-string в `backend/documents/service.py`
  - [`.gitignore`](../.gitignore): `.venv-ci/`
  - Доки: `TOMORROW_PLAN`, `BACKLOG_TECH_DEBT`, `IMPLEMENTED_FEATURES`, [`README.md`](../README.md#ci-github-actions) (локальные тесты: [`docs/06-developer-test-runbook.md`](06-developer-test-runbook.md))
  - **Релиз:** PR **`main` → `deploy`** после зелёного CI; опционально GitHub **ruleset** на `deploy` (PR + required checks)

- ✅ **TD-033: Per-document-type chunking config**
  - Заменён глобальный хардкод `chunk_text(doc.parsed_text)` на `CHUNKING_CONFIG` dict в `backend/embeddings/service.py`
  - Значения по типу: `swagger` 500 chars / 0 overlap, `markdown` 700/1, `pdf` 1000/1; fallback 700/1
  - Предзаполнены будущие типы: `logs` 300/0, `code` 600/1
  - Клиентских настроек нет — конфиг централизованный, правится в одном месте в коде
  - Ветка: `chore/td-033-chunking-config`

- ✅ **FI-021: Background embeddings** (async, `BackgroundTasks`)
  - `POST /embeddings/documents/{id}` возвращает `202 Accepted` немедленно; генерация чанков и вызов OpenAI уходят в `FastAPI.BackgroundTasks` с собственной DB-сессией (`SessionLocal`)
  - Новый статус `DocumentStatus.embedding` (синий badge): `ready → embedding → ready|error`
  - Фронтенд: polling `GET /documents/{id}` каждые 2 сек до `ready` или `error` (таймаут 120 сек); live-обновление статуса без перезагрузки страницы
  - Изменения: `backend/models.py`, `backend/embeddings/service.py` (`run_embeddings_background`), `backend/embeddings/routes.py`, `frontend/lib/api.ts` (`getById`), `frontend/app/(app)/knowledge/page.tsx`

- ✅ **FIX: race condition in `generate_ticket_number`** (`fix/ticket-number-race-condition`, merged)
  - Два конкурентных запроса для одного клиента могли оба вычислить одинаковый номер тикета → `IntegrityError` → 500 для одного пользователя
  - `generate_ticket_number()`: `SELECT FOR UPDATE SKIP LOCKED` (advisory lock на PostgreSQL; SQLite игнорирует) + regex `^ESC-(\d+)$` вместо `startswith + int(num[4:])`
  - `create_escalation_ticket()`: retry-цикл max 3 попытки при `IntegrityError` → `db.rollback()` → пересчёт номера; на 3-й неудаче исключение пробрасывается
  - Новые тесты: `test_generate_ticket_number_concurrent_reads_return_same`, `test_create_escalation_ticket_retries_on_integrity_error`, `test_create_escalation_ticket_raises_after_max_retries`; 193/193 тестов прошли

---

## ✅ COMPLETED (2026-03-21)

### L2 escalation tickets (FI-ESC)
- ✅ **FI-ESC (v1)** — при провале RAG, запросе «человека» или ручном действии создаётся тикет **ESC-####** (per client), письмо на email владельца клиента, ответ пользователю формулирует отдельный OpenAI-call с JSON; машинный маркер `[[escalation_ticket:…]]` при необходимости дописывается в коде
- **API:** JWT `GET/POST /escalations`, `GET /escalations/{id}`, `POST /escalations/{id}/resolve`; X-API-Key `POST /chat/{session_id}/escalate`; публично `POST /widget/escalate` + `chat_ended` / `locale` на виджете (см. `backend/routes/widget.py`)
- **UI:** `frontend/app/(app)/escalations/page.tsx`, пункт **Escalations** в навбаре; виджет: **Talk to support**, баннер тикета, блокировка ввода при закрытом чате (`ChatWidget.tsx`)
- **Модель/миграция:** `EscalationTicket`, колонки `Chat` для state machine; `backend/migrations/versions/fi_esc_escalation_tickets.py` (`fi_esc_v1`); модуль `backend/escalation/`
- **QA:** `docs/qa/FI-ESC-escalation-tickets-qa.md`

### Disclosure controls (FI-DISC) — client-wide response level
- ✅ **FI-DISC (v1)** — один уровень детализации ответа на весь тенант (**Detailed** / **Standard** / **Corporate**) для всех каналов (виджет, `POST /chat` по X-API-Key); жёсткие лимиты + блок `[Response level: …]` в system-части RAG-промпта (`build_rag_prompt` / `generate_answer`); загрузка `Client.disclosure_config` в `process_chat_message` и `run_debug`
- **Хранение:** `clients.disclosure_config` JSON; каноническое поле **`level`**; при чтении поддерживается алиас **`default_level`**
- **API:** `GET` / `PUT /clients/me/disclosure` (PUT — только для подтверждённого email)
- **UI:** `frontend/app/(app)/settings/disclosure/page.tsx`, пункт навигации **Response controls**, `api.disclosure`
- **Миграция:** `fi_disc_v1` (`backend/migrations/versions/fi_disc_disclosure_config.py`); модуль `backend/disclosure_config.py`; тесты `tests/test_disclosure.py`
- Промпт FI-DISC удалён после merge; **не** в scope v1: блоклист тем, preview, сегменты/KYC по уровню — см. `BACKLOG_PRODUCT.md` (future phases)

### Identity / widget (FI-KYC)
- ✅ **FI-KYC** — идентификация пользователя виджета через **краткоживущий HMAC-токен** (не через `data-*` в embed): `POST /widget/session/init` (`api_key`, опционально `identity_token`), ответ `session_id` + `mode` (`identified` | `anonymous`); контекст в `chats.user_context` (JSON); в LLM попадают только `plan_tier`, `locale`, `audience_tag`
- **Секрет подписи:** `POST/GET/POST` `/clients/me/kyc/secret|status|rotate` (шифрование как у OpenAI key; ротация с перекрытием старого ключа 1 ч); UI: `frontend/app/(app)/settings/widget/page.tsx`, `api.kyc`, пункт навигации **Widget API**
- ✅ **FI-KYC continuity v2** — backend resume for identified users (`client_id + user_id`, 24h, only open chats), browser-local continuity for anonymous users, `Start new chat` after `chat_ended`, controlled widget session retries, live `user_sessions` lifecycle tracking
- **Код:** `backend/core/security.py` (`generate_kyc_token`, `validate_kyc_token`), `backend/routes/widget.py`, `backend/widget/service.py`, `backend/user_sessions/service.py`, миграции `fi_kyc_user_identification` и `phase4_user_sessions_active_unique_v1`, тесты `tests/test_kyc.py`, `tests/test_widget.py`, `tests/test_chat.py`, `tests/test_escalation.py`
- Промпт `cursor_prompts/FI-KYC-user-identification.md` **удалён** после внедрения (описание здесь и в `BACKLOG_PRODUCT.md`)

### Widget / marketing
- ✅ **FI-038** — футер виджета «Powered by Chat9 →» в `frontend/components/ChatWidget.tsx` (ссылка на сайт; prod: iframe-виджет через `backend/static/embed.js` + `/widget`)
- Удалён неиспользуемый legacy-скрипт `backend/widget/static/embed.js` (старый `data-api-key` + `#ai-chat-widget`); README, demo-docs и `docs/03-tech-stack.md` приведены к актуальному embed seam: product-facing bot ID with legacy `clientId` / `public_id` compatibility

### Search / retrieval
- ✅ **FI-019 ext (FI-008)** — BM25 + RRF гибридный поиск (`rank-bm25`); промпт `FI-019ext-bm25-hybrid-hnsw.md` удалён после внедрения
  - PostgreSQL: `_pgvector_search` (top `2×top_k`) + `bm25_search_chunks` по `chunk_text` → `reciprocal_rank_fusion` (k=60)
  - SQLite (тесты): Python cosine только для vector candidate acquisition; дальше тот же BM25 → RRF → reranking → post-ranking flow по in-memory candidate pool после merge/dedup/truncation
  - lexical participation определяется отдельно от reranker lexical feature: overlap в candidate pool включает hybrid contract даже там, где raw BM25 scores плоские/нестабильные
  - Debug API: режим и confidence semantics выровнены с production path; `chunks[].score` отражает финальный pipeline score, `best_confidence_score` остаётся vector-derived
  - Зависимость: `requirements.txt` → `rank-bm25>=0.2.2`
- ✅ **FI-115** — observability for deterministic query variants before retrieval
  - root traces now carry `variant_mode`, `query_variant_count`, `extra_embedded_queries`, `extra_embedding_api_requests`, `extra_vector_search_calls`, `retrieval_duration_ms`
  - search stages expose `query-expansion`, `query-embedding`, and richer `vector-search` payloads for latency/cost comparison
  - direct `/search` now has trace parity with chat; evaluation runbook lives in `docs/qa/FI-115-query-variant-cost.md`

### RAG / embeddings
- ✅ **FI-009** — Sentence-aware chunking + метаданные эмбеддингов (`feature/fi-009-improved-chunking`)
  - `chunk_text()`: границы по предложениям, ~500 символов мягкий лимит, `overlap_sentences`
  - `metadata`: `chunk_index`, `char_offset`, `char_end`, `filename`, `file_type`
  - Промпт `cursor_prompts/FI-009-improved-chunking.md` удалён после внедрения; описание в `BACKLOG_PRODUCT.md` / `BACKLOG_RAG_QUALITY.md`
- ✅ **FI-032 (phase 1)** — document health check: `health_status`, `run_document_health_check`, QA-чеклист `docs/qa/FI-032-document-health-check.md`; промпт `cursor_prompts/FI-032-document-health-check.md` удалён.
- ✅ **FI-034** — LLM-based answer validation (`feature/fi-034-answer-validation`): после `generate_answer()` вызывается `validate_answer()` (gpt-4o-mini, `temperature=0`); при `is_valid=false` и `confidence < 0.4` ответ заменяется на fallback; ошибки валидации не блокируют ответ (`validation_skipped`). Результат в `POST /chat/debug` → `debug.validation`. Промпт `cursor_prompts/FI-034-llm-answer-validation.md` удалён после внедрения.
- ✅ **FI-043 + privacy hardening** — regex PII redaction expanded into outbound-safe storage/access flow: `backend/chat/pii.py` now returns structured redaction metadata; before OpenAI calls and escalation delivery the question is masked (email, phone, API key, card, password, id-doc, IP, tokenized URLs). Original text is stored encrypted in `messages.content_original_encrypted`, redacted text lives in `messages.content_redacted` and legacy `messages.content`. Added tenant privacy settings, `pii_events` audit log, original-content view/delete controls, retention cleanup, admin Privacy Log UI and CSV export. Main tests: `tests/chat/test_pii.py`, `tests/test_chat.py`, `tests/test_escalation.py`, `tests/test_admin_metrics.py`, frontend privacy UI tests.

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
2. **FI-039** — Daily summary email (Brevo)
3. **FI-040** — Client analytics dashboard
4. **FI-041** — Status page integration (real-time incident awareness)

### Medium-term (P3):
5. **Per-client system prompt**
6. **Multiple file upload**
7. **FI-115 production review** — collect p50/p95 single-vs-multi evidence and decide on guardrails (`max_variants`, normalization, caching)

---

## 📊 FEATURES LIVE IN PRODUCTION

- ✅ Document upload (PDF, Markdown, Swagger/OpenAPI)
- ✅ **Async embedding** (FI-021): `202 Accepted` + background task, polling по статусу `embedding → ready|error`
- ✅ RAG pipeline (OpenAI text-embedding-3-small + gpt-4o-mini; sentence-aware chunking + chunk metadata; regex PII redaction перед внешними вызовами FI-043; post-generation answer validation FI-034)
- ✅ **Per-type chunking** (TD-033): оптимальные параметры чанкинга по типу документа (swagger/markdown/pdf)
- ✅ Hybrid retrieval (PostgreSQL: pgvector candidate acquisition + shared BM25/RRF/reranking; SQLite mirrors the same downstream orchestration with Python cosine candidates)
- ✅ Contradiction reliability policy: contradiction stays visible in reliability evidence/signals, single facts remain evidence-only, corroborated same-pair or multi-pair contradiction caps to `low`, mirrored pair emissions no longer double-count
- ✅ pgvector native search (SQL cosine_distance, HNSW index)
- ✅ Retrieval observability (Langfuse-style traces for chat + `/search`, including query-variant cost/latency fields)
- ✅ Multi-tenant isolation (client_id scoping)
- ✅ Chat widget (embeddable, ~6KB vanilla JS)
- ✅ Zero-config widget embed (public_id + iframe)
- ✅ **Response controls (FI-DISC v1):** tenant-wide detail level (Detailed / Standard / Corporate), dashboard **Response controls**
- ✅ Optional **identified widget sessions** (FI-KYC): HMAC identity token + `/widget/session/init`, signing secret in dashboard
- ✅ Widget footer «Powered by Chat9 →» (FI-038)
- ✅ Dashboard (API key, embed snippet), Knowledge hub, logs, feedback, review, escalations, debug; sidebar navigation (UI-NAV)
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

CI: GitHub Actions — `.github/workflows/ci.yml` on push/PR to `main` + `deploy`
```

---

## ⚠️ KNOWN ISSUES / TECH DEBT

| Issue | Priority | Notes |
|-------|----------|-------|
| FI-EMBED-MVP real-domain test | 🟡 P1 | Waiting for admin to update embed script |
| Static Stats on landing page | 🟡 P2 | Hardcoded, connect real API later |
| ~~No CI/CD pipeline~~ | — | ✅ FI-026 — `.github/workflows/ci.yml` |
| Footer links hardcoded | 🟢 P3 | Update when docs site ready |

---

## 📎 Cursor prompts (`cursor_prompts/`)

Реализованные промпты удаляются из каталога после merge; описание фичи остаётся здесь и в `BACKLOG_*`.

**Сейчас в репозитории:** `_TEMPLATE_cursor-prompt.md`; `RULES-database-migrations.md`. Описания реализованных промптов (FI-007, FI-ESC, FI-DISC и др.) — в блоках выше и в `docs/IMPLEMENTED_FEATURES.md`. `ci-cd-github-actions.md` и `FIX-ticket-number-race-condition.md` намеренно не хранятся в репозитории — CI: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml); локальный runbook: [`docs/06-developer-test-runbook.md`](06-developer-test-runbook.md).

---

## 📚 Реестр фич vs бэклог

| File | Contents |
|------|---------|
| `06-developer-test-runbook.md` | Developer-focused test command groups (P0 smoke, auth reset, escalation, RAG edge cases, pgvector, coverage) |
| **`IMPLEMENTED_FEATURES.md`** | **Implemented features registry** (English, by area, links to code/API); extend on major releases |
| `BACKLOG_PRODUCT.md` | Product features (FI-xxx), RICE scored |
| `BACKLOG_TECH_DEBT.md` | Tech improvements |
| `BACKLOG_SECURITY-IMPROVEMENTS.md` | Security: vectorDB filter, rate limiting, tracing |
| `BACKLOG_EMBED-PHASE2.md` | Widget Phase 2/3 (embed.js, mobile, CSP; **tier-2** limits after baseline slowapi) |
| `BACKLOG_RAG_QUALITY.md` | RAG quality: chunking, re-ranker |
| `BACKLOG_MONETIZATION.md` | Pricing strategy |

---

_Updated: 2026-03-22 (FI-026 CI documented)_
