# RAG Quality Backlog

Everything that improves bot answer quality.
Detailed research (6 models) — in `RAG_QUALITY_RESEARCH.md`.

---

## 🔴 P1 — Do first

### ~~[FI-031] Org config layer~~ — Cancelled
**Decision (2026-03-21):** Approach scrapped. Data about the client's product will be sourced differently. Disclosure controls (FI-DISC) and escalation (FI-ESC) cover the key use cases instead.

---

### ~~[FI-007] Per-client system prompt~~ — Deferred indefinitely
**Decision (2026-03-21):** Deferred. FI-031 (prerequisite) was cancelled. Re-evaluate when a clear approach to tenant product data injection is defined. Cursor prompt deleted.

---

### [FI-009] Chunking + metadata — baseline ✅ / phase 2 open

**Shipped (2026-03-21):** В проде sentence-aware чанкинг в `backend/embeddings/service.py`: границы по предложениям, мягкий лимит ~500 символов, `overlap_sentences` (default 1). В `embeddings.metadata` (JSON): `chunk_index`, `char_offset`, `char_end`, `filename`, `file_type`.

**Still backlog (research / quality next steps — см. `RAG_QUALITY_RESEARCH.md`):**
- Целевые размеры в **токенах** и overlap 60–120 токенов (рецепт Grok: 450 / 120).
- **Структурный** сплит: заголовки H1–H3, не рвать code blocks.
- **Small-to-Big / sentence-window** при retrieval (малый чанк для поиска, расширенный контекст в промпт).
- Расширенные метаданные: `section_title`, `doc_date`, `content_type`, явный `tenant_id` в мете чанка.

---

### ~~[FI-008] Hybrid search: BM25 + RRF~~ ✅ Done (2026-03-21)
**Was:** Pure cosine similarity missed exact strings — error codes, CLI commands, SDK method names, endpoint paths.

**Implemented:**
- In-memory BM25 over `chunk_text` via `rank-bm25` (`BM25Okapi`), not Postgres `tsvector`
- RRF fusion of vector + BM25 rankings (`reciprocal_rank_fusion`, k=60)
- PostgreSQL: `_pgvector_search` + BM25 + RRF in `search_similar_chunks`; SQLite tests: cosine-only path
- Scores after fusion are RRF (not cosine); debug `mode`: `hybrid` on Postgres
- Spec reference (still useful for future work): `specs/hybrid-search-spec.docx` (FR-2, FR-3)

---

### [FI-033] Query expansion / rewriting
**Problem:** Quality depends on question phrasing. "how do I set up auth?" and "what is the authentication flow?" should return the same chunks.

**Options:**
- Query rewriting: normalize before retrieval.
- Query expansion: generate 2–3 paraphrases via LLM → search all in parallel → merge via RRF.

**Note:** Adds latency + cost (extra LLM call before retrieval). Run after FI-008 hybrid is proven insufficient (FI-008 is live as of 2026-03-21).
Spec reference: `specs/hybrid-search-spec.docx` (FR-1)

---

### ~~[FI-034] LLM-based answer validation~~ ✅ Done (2026-03-21)
**Was:** After generation, ask the model whether the answer is grounded in context; if low confidence → safe fallback.

**Implemented:**
- `backend/chat/service.py`: `validate_answer()` after `generate_answer()` in `process_chat_message()`; threshold `confidence < 0.4` together with `is_valid=false` (including empty retrieval → `no_context`); the `question` argument is the **redacted** user text (FI-043), same as for retrieval and generation
- OpenAI/JSON failures → non-blocking `validation_skipped`, original answer kept
- `run_debug()` / `POST /chat/debug`: `debug.validation` with `{is_valid, confidence, reason}`
- `ChatResponse.validation` optional (reserved; public `/chat` does not populate)

---

### [FI-032] Document health check
**Idea:** After document upload — automatic GPT analysis:
- Incomplete sections (no answer to typical questions).
- Broken / outdated URLs.
- Documentation gaps.
- Poor structure for RAG (long sections without subheadings).

**UI:** "Document health" tab/section in dashboard with warnings.
**Effort:** 3 days.

---

## 🟠 P2 — Next sprint

### [FI-042] Cross-encoder reranker
**Problem:** Bi-encoder (embedding model) scores query and chunk independently. Cross-encoder sees them together → more accurate relevance scoring.

**Plan:**
- Run on top-20 candidates after RRF fusion → return top-5 to LLM
- Model: `cross-encoder/ms-marco-MiniLM-L-6-v2` (local, free) or Cohere Rerank 3.5 (API)
- Cross-encoder score → feeds Answer Reliability Score (top chunk score as proxy)
- Adds ~200–400ms latency → run when BM25+RRF hybrid is proven insufficient for quality

**Do after:** FI-008 hybrid is live (✅ 2026-03-21) and latency budget is measured in prod.
Spec reference: `specs/hybrid-search-spec.docx` (FR-4)

### [FI-036] HyDE (Hypothetical Document Embeddings)
**When:** Question like "API limits?" — too short.
- Generate hypothetical answer via GPT → search chunks by it.
- Helps with short/vague questions.

### [FI-037] Approved Answers Layer
**Idea:** 👍 answers → separate search layer.

```
Question → Semantic search in approved_answers (>0.85?) → Instant answer (no GPT)
                                                  ↓ no
                                            Regular RAG
```

- Level 1 MVP: `approved_answers` table, on 👍 → insert.
- Level 2: ideal_answer as reference + editing in /review.
- Level 3: (question + context + ideal_answer) → dataset for fine-tuning.

### [FI-029] Document versioning & recency scoring
- Fields: `is_current`, `version`, `valid_until`, `updated_at`.
- Retrieval filter: `WHERE is_current = true`.
- Recency scoring: `final_score = semantic * 0.7 + recency * 0.3`.
- On update: new version + old `is_current=false`.
- Namespace per major version (api-v1, api-v2).

---

## 🟡 P3 — Long-term

### [FI-028] Cross-lingual retrieval
- Switch to multilingual embeddings: **Jina v3/v4** or **Cohere Embed v3 multilingual**.
- System prompt: "Answer in question language, even if context is in another."
- For critical clients: store chunks in original + machine translation to EN.

### [FI-011] FAQ layer (auto-generation from tickets)
- Not manual Q&A input, but auto-generation from uploaded tickets.
- Client approves/rejects suggested pairs.

---

## Research

Full recommendations from 6 models (Perplexity, ChatGPT, DeepSeek, Gemini, Sonnet, Grok) → `RAG_QUALITY_RESEARCH.md`.
