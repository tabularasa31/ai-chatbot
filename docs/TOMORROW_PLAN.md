# Execution Plan — Sprint Log

> This file tracks completed sprints and what's next.

---

## ✅ Sprint 1 — Infrastructure & Quality (2026-03-19 → 2026-03-20)

All done and deployed to production.

| Prompt | Status |
|--------|--------|
| `deps-remove-pypdf2-update-openai.md` | ✅ Done |
| `FI-038-powered-by-chat9-footer.md` | ✅ Done (`frontend/components/ChatWidget.tsx`) |
| `migration-pgvector-vector-column-hnsw.md` | ✅ Done |
| `FI-019-pgvector-cleanup.md` | ✅ Done |
| `FI-019ext-bm25-hybrid-hnsw.md` (BM25 + RRF) | ✅ Done 2026-03-21 |
| FI-KYC (widget identity, HMAC token) | ✅ Done 2026-03-21 |
| Forgot password (FI-AUTH) | ✅ Done |
| Sign in button (FI-UI) | ✅ Done |
| FI-EMBED-MVP (zero-config widget) | ✅ Done |
| REFACTOR: datetime, CORS, exceptions | ✅ Done |
| REFACTOR: N+1 queries | ✅ Done |
| **Deploy** main → deploy | ✅ Done 2026-03-20 |

---

## 🔜 Sprint 2 — Next

| Task | Priority | Notes |
|------|----------|-------|
| Test FI-EMBED-MVP on real domain | P1 | Waiting for domain admin |
| FI-021 Background embeddings | P1 | Async processing |
| FI-039 Daily Summary Email | P2 | Brevo |
| FI-040 Client Analytics | P2 | Dashboard metrics |
| FI-041 Status Page Integration | P2 | Incident awareness |
| CI/CD (GitHub Actions) | P3 | pytest + ruff + eslint on PR |

---

_Updated: 2026-03-21_
