# RAG Quality Backlog

Everything that improves bot answer quality.
Detailed research (6 models) — in `RAG_QUALITY_RESEARCH.md`.

---

## 🔴 P1 — Do first

### [FI-031] Org config layer
**Problem:** Bot doesn't know support email, trial period, contacts — they're not in documents.

**Solution:**
- `org_config` (JSON) field on Client model: `support_email`, `account_manager`, `trial_period`, `support_url`.
- Dashboard UI: form for filling.
- In `build_rag_prompt()` — inject org_config block before context.

**Do BEFORE FI-007** (system prompt without org config will be incomplete).

---

### [FI-007] Per-client system prompt
**5 critical elements (consensus of all 6 models):**
1. Role and goal: "You are support for [Name]. Answer only from context."
2. Truthfulness policy: "If not found — don't invent, direct to: support_email, account_manager."
3. Language: "Answer in the question's language."
4. Multi-chunk handling: "Combine, state contradictions explicitly."
5. Fantasy limit: "Don't invent endpoints, plans, parameters."

**Additionally (Grok):** Answer structure:
```
1. Brief direct answer (1–2 sentences)
2. Quote [1]
3. Additional steps / warnings
4. If billing/contract → direct to manager
```

**Store:** system_prompt in DB with version + tenant_id (prompt versioning, per Sonnet).

---

### [FI-009] Chunking + metadata — baseline ✅ / phase 2 open

**Shipped (2026-03-21):** В проде sentence-aware чанкинг в `backend/embeddings/service.py`: границы по предложениям, мягкий лимит ~500 символов, `overlap_sentences` (default 1). В `embeddings.metadata` (JSON): `chunk_index`, `char_offset`, `char_end`, `filename`, `file_type`.

**Still backlog (research / quality next steps — см. `RAG_QUALITY_RESEARCH.md`):**
- Целевые размеры в **токенах** и overlap 60–120 токенов (рецепт Grok: 450 / 120).
- **Структурный** сплит: заголовки H1–H3, не рвать code blocks.
- **Small-to-Big / sentence-window** при retrieval (малый чанк для поиска, расширенный контекст в промпт).
- Расширенные метаданные: `section_title`, `doc_date`, `content_type`, явный `tenant_id` в мете чанка.

---

### [FI-008] Hybrid search: BM25 + RRF
**Problem:** Pure cosine similarity fails on exact strings — error codes, CLI commands, SDK method names, endpoint paths.
- Replace current keyword fallback with full BM25 (PostgreSQL `tsvector` or `rank-bm25`)
- RRF fusion of vector + BM25 results (rank-based, no normalisation needed)
- Cursor prompt ready: `cursor_prompts/FI-019ext-bm25-hybrid-hnsw.md`
- Spec reference: `specs/hybrid-search-spec.docx` (FR-2, FR-3)

---

### [FI-033] Query expansion / rewriting
**Problem:** Quality depends on question phrasing. "how do I set up auth?" and "what is the authentication flow?" should return the same chunks.

**Options:**
- Query rewriting: normalize before retrieval.
- Query expansion: generate 2–3 paraphrases via LLM → search all in parallel → merge via RRF.

**Note:** Adds latency + cost (extra LLM call before retrieval). Do after FI-008 is live and we have data showing this is still a bottleneck.
Spec reference: `specs/hybrid-search-spec.docx` (FR-1)

---

### [FI-034] LLM-based answer validation
**Idea:** After generation, ask model: "Is there an explicit answer in context?"
- If no → fallback.
- Additional layer on top of cosine threshold.

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
- Adds ~200–400ms latency → run after FI-008 proves BM25+RRF insufficient

**Do after:** FI-008 is live and latency budget is understood.
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
