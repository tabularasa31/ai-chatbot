# Chat9 — implemented features registry

**Purpose:** A single grouped list of **what the product already does**, with pointers to code and APIs. It does **not** replace the full commit/session history — see [`PROGRESS.md`](./PROGRESS.md) for that.

**Last updated:** 2026-04-07 (verification-first dashboard access, bot-scoped debug)

---

## Authentication & account

| ID / area | What shipped | Code / API |
|-----------|--------------|--------------|
| Registration, JWT login | Users, JWT sessions; access tokens carry `typ=chat9_user` | `backend/auth/`, `backend/core/security.py`, `backend/core/jwt_kinds.py`, `POST /auth/register`, `POST /auth/login` |
| Email verification | Brevo email, token; successful verification provisions the user's workspace client and unlocks dashboard / tenant JWT APIs guarded by `require_verified_user` | `POST /auth/verify-email`, verify UI, `backend/auth/middleware.py`, `backend/clients/service.py` |
| Forgot password | Email request + token reset (1h TTL), rate limit | `POST /auth/forgot-password`, `POST /auth/reset-password` |
| Admin flag | `is_admin` for admin metrics | `User.is_admin`, `GET /admin/metrics/*` |

---

## Client (tenant) & settings

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| Client per user | API key, `public_id`, data isolation; one-to-one user/client invariant enforced in app + DB, normal provisioning happens during `POST /auth/verify-email` | `backend/models.py` `Client`, `backend/clients/service.py`, `POST /clients`, `GET /clients/me` |
| Per-client OpenAI key | Encrypted in DB, PATCH client | `PATCH /clients/me`, `backend/core/crypto.py` |
| **FI-DISC v1** | Single tenant-wide response detail level (detailed / standard / corporate), hard limits in prompt | `GET`/`PUT /clients/me/disclosure`, `backend/disclosure_config.py`, `backend/chat/service.py`, UI `/settings/disclosure` |
| **FI-KYC** | Widget signing secret, rotation | `POST /clients/me/kyc/secret`, `status`, `rotate`; UI `/settings/widget` |
| **Eval QA (internal MVP)** | Testers table, eval sessions/results; separate `EVAL_JWT_SECRET` + `typ=eval_tester`; `/eval/login`, `/eval/sessions`, results CRUD (append-only); CLI `scripts/create_tester.py`; Next `/eval/login`, `/eval/chat`; shared client readiness with widget | `backend/eval/*`, `backend/models.py` (`Tester`, `EvalSession`, `EvalResult`), migration `eval_qa_mvp_v1`, `tests/test_eval.py`, `docs/04-features.md` §10 |

---

## Documents & embeddings

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| Upload / parse | PDF (pypdf), MD, Swagger/OpenAPI | `backend/documents/`, `POST /documents` |
| **FI-009** | Sentence-aware chunking, chunk metadata | `backend/embeddings/service.py` (`chunk_text`), migrations |
| **TD-033** | Per-doc-type chunking: `swagger` 500 chars/0 overlap, `markdown` 700/1, `pdf` 1000/1; `CHUNKING_CONFIG` dict — tune in one place, no client UI | `backend/embeddings/service.py` |
| **FI-021** | Async embeddings: `202 Accepted` immediately, `BackgroundTasks` with own DB session, status `ready → embedding → ready/error`; frontend polls every 2 s | `backend/embeddings/routes.py`, `service.py`, `frontend/app/(app)/knowledge/page.tsx` |
| Embeddings | text-embedding-3-small, pgvector / SQLite test fallback | `backend/embeddings/`, `POST /embeddings/documents/{id}` |
| **FI-032 ph.1** | Document health check (GPT), `health_status`, re-check | `GET`/`POST /documents/{id}/health*`, `docs/qa/FI-032-document-health-check.md` |
| **FI-URL v1** | URL documentation source ingestion: preflight, same-domain crawl, background indexing, refresh, run history, inline source detail, shared client-wide knowledge capacity (files + URL pages), and per-page deletion that persists across refreshes | `backend/documents/url_service.py`, `GET/POST/PATCH/DELETE /documents/sources*`, `DELETE /documents/sources/{source_id}/pages/{document_id}`, `docs/qa/FI-URL-url-sources-v1.md` |

---

## Search & RAG chat

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-008 / FI-019 ext** | Hybrid retrieval with shared BM25 + RRF + reranking orchestration; Postgres uses pgvector for vector candidates, SQLite uses Python cosine for candidate acquisition and then the same candidate-pool lexical/ranking contract | `backend/search/service.py`, `rank-bm25` |
| Contradiction reliability policy (2026-03-28) | Contradiction evidence remains visible in canonical retrieval reliability, but only corroborated contradiction now caps to `low`: single facts stay evidence-only, same-pair or multi-pair multiplicity triggers `cap_reason="contradiction"`, reversed-orientation mirrors no longer double-count, and the contract shape stays unchanged | `backend/search/service.py`, `tests/test_search.py`, `tests/test_chat.py`, `docs/04-features.md` |
| **FI-115 + BM25 symmetry follow-up** | Query-variant retrieval observability plus explicit BM25 expansion policy: default `asymmetric`, opt-in `symmetric_variants`, lexical-safe variant evaluation over the shared candidate pool, deterministic lexical merge before RRF, BM25 variant-eval / merged-hit trace fields, root trace parity for chat and `/search`, variant segmentation tags | `backend/search/service.py`, `backend/search/routes.py`, `backend/chat/service.py`, `backend/core/config.py`, `docs/04-features.md`, `docs/07-observability-rollout.md`, `docs/qa/FI-115-query-variant-cost.md` |
| RAG pipeline | `run_chat_pipeline` — pure shared function with invariant stage order: injection → embed → FAQ → relevance → retrieve → low-retrieval guard → generate → validate → escalation decision (compute-only). `process_chat_message` wraps it with DB/escalation side effects. `run_debug` calls it directly for zero-persistence debug runs. | `backend/chat/service.py` `run_chat_pipeline`, `process_chat_message`, `POST /chat` (X-API-Key) |
| Controlled clarification (MVP) | Typed chat outcomes: `answer`, `clarification`, `partial_with_clarification`; deterministic clarification triggers for ambiguous intent / missing critical slot / low retrieval confidence; continuation-vs-new-intent handling via `user_context.clarification_state`; clarification payloads with options/requested fields; widget quick replies; clarification text localized through the shared language helper | `backend/chat/service.py`, `backend/chat/schemas.py`, `backend/routes/widget.py`, `frontend/components/ChatWidget.tsx`, `frontend/lib/api.ts`, `tests/test_chat.py`, `tests/test_widget.py` |
| **FI-034** | LLM answer validation; `INSUFFICIENT_CONFIDENCE` fallback now localizes to the question language (or locale hint before the first question); debug endpoint is bot-scoped (`POST /chat/debug?bot_id=ch_...`) and exposes `strategy`, `reject_reason`, `is_reject`, `is_faq_direct`, `validation_applied`, `validation_outcome`, `raw_answer`; contradiction observability fields (`contradiction_detected`, `contradiction_count`, `contradiction_pair_count`, `contradiction_basis_types`) | `validate_answer()`, `build_reject_response()`, `backend/chat/language.py`, `POST /chat/debug` |
| Multilingual response localization | Shared localization helper for soft rejects, clarification prompts, escalation fallbacks, and other deterministic assistant text. Priority chain: before the first user question use `locale -> browser_locale -> English`; after that use the language of the question itself. | `backend/chat/language.py`, `backend/chat/service.py`, `backend/guards/reject_response.py`, `backend/escalation/openai_escalation.py` |
| **FI-043 + privacy hardening** | Regex PII redaction before OpenAI plus encrypted original storage, redacted-safe logs/escalations, tenant privacy settings, `pii_events` audit log, original-content access/delete controls, retention cleanup, Privacy Log UI + CSV export | `backend/chat/pii.py`, `backend/chat/service.py`, `backend/escalation/service.py`, `backend/admin/routes.py`, `frontend/app/(app)/settings/privacy/page.tsx`, `frontend/app/(app)/admin/privacy/page.tsx` |
| **FI-ESC v1** | L2 escalation: tickets (DB + tenant email), triggers (low similarity, no chunks, human phrase, manual), OpenAI JSON handoff UX, `chat_ended` on `POST /chat` | `backend/escalation/`, `process_chat_message`, `POST /chat/{session_id}/escalate`, JWT `GET/POST /escalations*`, migration `fi_esc_v1` |
| Tenant knowledge extraction (Phase 1) | After embeddings finish: extract `tenant_profiles` (product name, modules, glossary, aliases, support contacts) and generate `tenant_faq` candidates (best-effort, never blocks indexing) | `backend/tenant_knowledge/*`, `backend/embeddings/service.py`, migration `add_tenant_profiles_and_faq.py` |
| Relevance guard (Phase 2) | LLM relevance pre-check (timeout + in-memory cache) + low-retrieval early reject; guard instructions applied after escalation paths; `OPENAI_REQUEST_TIMEOUT_SECONDS` and `RELEVANCE_RETRIEVAL_THRESHOLD` tune behavior | `backend/guards/relevance_checker.py`, `backend/chat/service.py`, `backend/search/service.py`, env `OPENAI_REQUEST_TIMEOUT_SECONDS`, `RELEVANCE_RETRIEVAL_THRESHOLD` |
| Injection detector v2 | Language-agnostic structural + semantic injection detection; structural check (regex pattern match) runs first, optional semantic LLM check only when structural score is ambiguous; HTTP timeout guard; `detect_injection` called once per request via `precomputed_injection` pass-through into `run_chat_pipeline` | `backend/guards/injection_detector.py`, `backend/chat/service.py` |
| Soft rejection texts | `build_reject_response(reason, profile)` returns localized soft rejection text for three semantic buckets: out-of-domain (`NOT_RELEVANT`, `LOW_RETRIEVAL_SCORE`), injection (`INJECTION_DETECTED`), and low-confidence (`INSUFFICIENT_CONFIDENCE`). Before the first question it uses locale hints; after that it follows the question language. `product_name` and `topic_hint` come from `TenantProfile` with safe fallbacks. | `backend/guards/reject_response.py`, `backend/chat/language.py` |
| FAQ match (Phase 3) | Post-relevance FAQ hybrid routing with `faq_direct`/`faq_context`/`rag_only`, guarded direct path, single embedding reuse across FAQ + retrieval, FAQ hints in prompt, and stable `faq_match` span contract (`top_score` = best raw candidate score, `selected_score` = selected FAQ score) | `backend/faq/faq_matcher.py`, `backend/chat/service.py`, `backend/search/service.py`, `tests/test_faq_matcher.py`, `tests/test_rag_pipeline.py` |
| Knowledge API (Profile + FAQ) | Tenant knowledge endpoints for profile and FAQ moderation: `GET/PATCH /knowledge/profile`, `GET /knowledge/faq`, `POST /knowledge/faq/{id}/approve`, `POST /knowledge/faq/{id}/reject`, `POST /knowledge/faq/approve-all`, `PUT/DELETE /knowledge/faq/{id}`; extraction status exposed as profile field | `backend/knowledge/routes.py`, `backend/knowledge/schemas.py`, `backend/main.py`, `backend/models.py`, `tests/test_knowledge_api.py` |
| Sessions / logs / feedback | Session list, logs, thumbs, ideal answer, bad answers | `GET /chat/sessions`, logs, feedback, bad-answers |

---

## Widget & public embed

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-EMBED-MVP** | iframe + `public_id`, `/embed.js`, public chat, and a default localized greeting for brand-new empty widget sessions (not repeated on 24h resume) | `GET /embed.js`, `POST /widget/chat`, `backend/chat/service.py`, `frontend/components/ChatWidget.tsx`, dashboard embed code |
| **FI-KYC** | `POST /widget/session/init` with optional `identity_token` → `chats.user_context`; identified resume; anonymous browser continuity; `user_sessions` tracking | `backend/routes/widget.py`, `backend/widget/service.py`, `backend/user_sessions/service.py`, `backend/core/security.py` |
| **FI-ESC (widget)** | Optional `locale` on session init and `/widget/chat`; `POST /widget/escalate` (public); response includes `chat_ended`; escalation fallback text respects latest question language or locale hint for short conversations | `backend/routes/widget.py`, `backend/escalation/openai_escalation.py`, `frontend/app/widget/escalate/route.ts`, `ChatWidget.tsx` |
| Clarification quick replies | Widget renders structured clarification options as quick-reply buttons only for the latest assistant clarification turn; button clicks send the visible label plus structured `option_id` when available | `frontend/components/ChatWidget.tsx`, `frontend/lib/widget-conversation.ts`, `frontend/app/widget/chat/route.ts` |
| **FI-038** | “Powered by Chat9” footer | `frontend/components/ChatWidget.tsx` |
| Widget rate limits | 20/min on `POST /widget/session/init`, `/widget/chat`, `/widget/escalate` | slowapi, `backend/routes/widget.py` |
| **Widget chat gate** | Single eligibility check (exists, active, OpenAI key non-empty) for `/widget/chat`, `/widget/escalate`, and eval session create | `backend/clients/widget_chat_gate.py` |
| **embed.js** | Passes `navigator.language` as `locale` into iframe URL | `backend/static/embed.js` |

---

## Product UI

| ID / area | What shipped | Where |
|-----------|--------------|-------|
| **FI-UI** | Dark brand, navbar, auth pages, post-login transition | `frontend/components/Navbar.tsx`, auth pages, `AuthTransition` |
| **UI-NAV** | Persistent sidebar (icons, active state, Settings/Admin sections); slim fixed navbar (brand + email + logout only) | `frontend/components/Sidebar.tsx`, `frontend/components/Navbar.tsx`, `frontend/app/(app)/layout.tsx` |
| **Knowledge hub** | `/knowledge` (replaces `/documents`): file upload + URL source ingestion, unified indexed sources table with type badges, status, schedule, indexed counts, health, row actions, inline expandable source detail, and per-page deletion for indexed URL pages | `frontend/app/(app)/knowledge/page.tsx` |
| Knowledge subtabs (Phase 3 UI) | `/knowledge` now supports tabbed views: `Documents` (existing), `Profile` (`?tab=profile`) with extracted profile editing + extraction status + glossary accordion, and `FAQ` (`?tab=faq`) with moderation workflow (accept/reject/accept-all/edit, pending counter, filters, extraction-driven polling) | `frontend/app/(app)/knowledge/page.tsx`, `frontend/lib/api.ts` |
| **Code snippets UX** | Inline copy icon on embed / Node.js / debug answer blocks (shared component, light/dark tone) | `frontend/components/ui/code-block-with-copy.tsx` |
| **Agents** | `/settings`: OpenAI API key management (moved from Dashboard); status banners; save/update/remove flow | `frontend/app/(app)/settings/page.tsx` |
| Dashboard, **Knowledge**, Logs, Review, Debug, **Escalations** | Main app sections | `frontend/app/(app)/` |
| **Design system** | Unified card/button/link/input/error style across all app pages; `rounded-xl border border-slate-200`, `bg-violet-600` primary, `text-violet-600` links | All `frontend/app/(app)/**` pages |
| Landing | Marketing page, Sign in | `frontend/app/` (landing routes) |

---

## Security & infrastructure

| Area | What shipped | Where |
|------|--------------|-------|
| Rate limiting | `/validate`, `/search`, `/chat`, widget | `backend/core/limiter.py`, routes |
| CORS | Production allowlist | app config |
| pgvector + HNSW | Native vector column + index | migration `dd643d1a544a`, `embeddings.vector` |
| **FI-026** | GitHub Actions on `main` + `deploy`: backend Ruff + pytest + coverage; frontend ESLint + `next build` | `.github/workflows/ci.yml`, `backend/ruff.toml` |
| Coverage hardening (2026-03-24) | Added high-risk regression tests for escalation state machine, manual escalation endpoint, auth reset flow, and retrieval edge/error paths; stable `/search` OpenAI error contract (`503`) | `tests/test_chat.py`, `tests/test_escalation.py`, `tests/test_auth.py`, `tests/test_search.py`, `tests/pgvector_tests/test_search_pgvector.py`, `backend/search/routes.py` |
| Retrieval observability (2026-03-28) | Root traces for chat + `/search`; query-variant cost/latency fields; explicit BM25 expansion metadata (`bm25_expansion_mode`, variant eval counts, merged hit counts); tenant-tag-preserving variant segmentation; regression coverage for sampled/deferred traces and symmetric BM25 retrieval behavior | `backend/observability/service.py`, `backend/search/service.py`, `tests/test_observability.py`, `tests/test_search.py`, `tests/test_chat.py`, `tests/pgvector_tests/test_search_pgvector.py` |
| Full trace capture toggle (2026-03-29) | `FULL_CAPTURE_MODE` env (default `true`) disables adaptive sampling for early/low-traffic setups; `false` keeps prior heuristics. Traces get `sampling_mode` metadata + tag | `backend/core/config.py`, `backend/observability/service.py`, `tests/test_observability.py`, `docs/07-observability-rollout.md`, `docs/04-features.md` |
| Developer test runbook | Engineer-focused grouped test commands for local/CI runs | `docs/06-developer-test-runbook.md` |
| Deploy | `main` vs `deploy`, Vercel + Railway; promote via PR after green CI | see `PROGRESS.md` → Infrastructure |

---

## CI & quality

| ID / area | What shipped | Code / API |
|-----------|--------------|------------|
| **FI-026** | GitHub Actions on `main` + `deploy`: backend `ruff` + `pytest tests/` + coverage; frontend `eslint` + `next build` | `.github/workflows/ci.yml`, `backend/ruff.toml` |

---

## Related docs

| Document | Use for |
|----------|---------|
| [`PROGRESS.md`](./PROGRESS.md) | Chronology, session context, “what happened when” |
| [`BACKLOG_EMBED-PHASE2.md`](./BACKLOG_EMBED-PHASE2.md) | Widget Phase 2/3 backlog (embed.js hardening, CSP, quotas — after baseline limits) |
| [`BACKLOG_PRODUCT.md`](./BACKLOG_PRODUCT.md) | Queue & RICE; done items marked ~~Done~~ |
| [`README.md`](../README.md) | Runbook, short API overview |
| [`qa/PRODUCT-QA-TEST-PLAN.md`](./qa/PRODUCT-QA-TEST-PLAN.md) | Manual QA (Russian) |
| [`qa/FI-URL-url-sources-v1.md`](./qa/FI-URL-url-sources-v1.md) | URL sources v1 — QA checklist |
| [`qa/FI-ESC-escalation-tickets-qa.md`](./qa/FI-ESC-escalation-tickets-qa.md) | FI-ESC escalation — чеклист для тестировщика |
| [`qa/UI-NAV-sidebar-redesign-qa.md`](./qa/UI-NAV-sidebar-redesign-qa.md) | UI-NAV sidebar redesign — QA checklist |

---

## Maintenance

- After a **major** feature: add a row to the right table and, if needed, a block in `PROGRESS.md`.
- Small bugfixes **do not** need an entry here — only user-visible capabilities.
