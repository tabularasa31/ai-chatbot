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

### [FI-009] Improved chunking + metadata
**Recommendations from all 6 models:**
- `chunk_size = 450 tokens`, `chunk_overlap = 120 tokens` (Grok — most specific).
- Recursive splitter: `["\n\n", "\n", ". ", " ", ""]`.
- Structural chunking: split by headers (H1–H3), don't break code blocks.
- Small-to-Big / Sentence-window: small chunk for search, ±2 sentences at retrieval.
- Metadata per chunk: `section_title`, `doc_id`, `doc_date`, `content_type`, `tenant_id`.

---

### [FI-033] Query expansion / rewriting
**Problem:** Quality depends on question phrasing.

**Options:**
- Query rewriting: normalize before retrieval.
- Query expansion: 2–3 paraphrases + RRF across all results.

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

### [FI-008] Hybrid search (partially exists, extend)
- Current keyword fallback < 0.3 — extend to full BM25.
- RRF or weighted `0.7 vector + 0.3 BM25`.
- After retrieval: cross-encoder rerank (Cohere Rerank 3.5 or flashrank).

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
