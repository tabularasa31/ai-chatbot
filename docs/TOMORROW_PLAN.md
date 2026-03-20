# План на завтра — порядок выполнения

Все промпты в папке `cursor_prompts/`

## День 1 — Фундамент
1. `cursor_prompts/deps-remove-pypdf2-update-openai.md`
2. `cursor_prompts/fix-cors-split-public-private.md`
3. `cursor_prompts/FI-038-powered-by-chat9-footer.md`

## День 2 — Поиск и индексы
4. `cursor_prompts/migration-pgvector-vector-column-hnsw.md` ← сначала, блокер!
5. `cursor_prompts/FI-019-pgvector-cleanup.md`
6. `cursor_prompts/FI-019ext-bm25-hybrid-hnsw.md`

## День 3 — RAG качество
7. `cursor_prompts/FI-009-improved-chunking.md`
8. `cursor_prompts/FI-034-llm-answer-validation.md`

## День 4 — Инфраструктура
9. `cursor_prompts/widget-rate-limiting.md`
10. `cursor_prompts/ci-cd-github-actions.md`

## Финал
11. **Deploy** — `git checkout deploy && git merge main && git push origin deploy`
