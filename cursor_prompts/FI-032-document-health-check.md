# CURSOR PROMPT: [FI-032] Document Health Check

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd ~/Projects/ai-chatbot
git checkout main
git pull origin main
git checkout -b feature/fi-032-doc-health
```

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/documents/service.py` — add health check logic
- `backend/documents/routes.py` — add GET `/documents/{doc_id}/health` endpoint
- `backend/models.py` — add `health_status` JSON field to `Document`
- `backend/migrations/versions/` — new Alembic migration
- `frontend/app/dashboard/documents/page.tsx` — show health indicators
- `frontend/lib/api.ts` — add health check API call
- `tests/` — add tests for health check logic

**Do NOT touch:**
- `backend/auth/`
- `backend/search/`
- `backend/embeddings/`
- `backend/chat/`
- `backend/widget/`
- Any existing tests

---

## CONTEXT

Clients upload documents and sometimes don't know why the bot gives bad answers. The problem is often the document itself: poor structure, missing sections, broken links, or outdated content.

Document Health Check runs after document upload (and on demand) — it asks GPT-4o-mini to analyze the document and return structured warnings. These warnings are shown in the dashboard so the client knows what to fix.

**Health check is non-blocking:** upload still succeeds, embeddings are still created. Health check runs in the background after embedding is complete.

---

## WHAT TO DO

### 1. Add `health_status` to Document model (`backend/models.py`)

Add after `status` column:

```python
health_status = Column(
    JSON,
    nullable=True,
    default=None,
)
```

`health_status` schema:
```json
{
  "score": 85,
  "checked_at": "2026-03-20T22:00:00Z",
  "warnings": [
    {
      "type": "missing_contact_info",
      "severity": "medium",
      "message": "No support email or contact info found in the document."
    },
    {
      "type": "poor_structure",
      "severity": "low",
      "message": "Several sections are very long without subheadings. This may reduce answer accuracy."
    }
  ]
}
```

Warning `type` values (use these exact strings):
- `missing_contact_info` — no email, phone, or contact section
- `poor_structure` — long sections without headers
- `incomplete_sections` — sections that look truncated or unfinished
- `no_examples` — no examples or concrete details (pure abstract text)
- `outdated_content` — mentions specific dates or versions that look old

Warning `severity` values: `"low"` | `"medium"` | `"high"`

`score` is 0–100: start at 100, subtract for each warning (high=-20, medium=-10, low=-5). Minimum 0.

### 2. Alembic migration

```python
def upgrade():
    op.add_column(
        "documents",
        sa.Column("health_status", sa.JSON(), nullable=True),
    )

def downgrade():
    op.drop_column("documents", "health_status")
```

### 3. Health check function (`backend/documents/service.py`)

```python
def run_document_health_check(
    document_id: uuid.UUID,
    db: Session,
    api_key: str,
) -> dict:
    """
    Run GPT-based health check on a document's chunk texts.
    Updates document.health_status in DB.
    Returns the health_status dict.
    """
```

**Implementation:**

1. Load all chunk_texts for the document (via Embedding records)
2. Concatenate first ~3000 tokens worth of text (truncate to avoid huge API calls)
3. Send to GPT-4o-mini with this prompt:

```
Analyze this documentation excerpt and identify issues that could reduce the quality of AI-powered search and Q&A.

Return a JSON object with this exact structure:
{
  "warnings": [
    {"type": "<type>", "severity": "<low|medium|high>", "message": "<human-readable explanation>"}
  ]
}

Check for:
- missing_contact_info: No support email, phone, or contact section
- poor_structure: Long sections (500+ words) without subheadings
- incomplete_sections: Sections that appear cut off or unfinished
- no_examples: Important features described abstractly with no examples
- outdated_content: References to specific old dates or deprecated versions

Only report issues that are clearly present. Return empty warnings array if the document looks good.
Return ONLY the JSON, no other text.

Documentation excerpt:
<text>
```

4. Parse the JSON response
5. Compute score (100 - sum of severity penalties)
6. Store result in `document.health_status` with `checked_at` timestamp
7. Return the health_status dict

**Error handling:** If GPT call fails or JSON parse fails, set `health_status = {"score": null, "checked_at": "...", "warnings": [], "error": "health check failed"}` and don't raise an exception.

### 4. API route (`backend/documents/routes.py`)

```
GET /documents/{doc_id}/health
```

- Requires auth (client must own the document)
- Returns current `health_status` from DB (does NOT re-run the check)
- If `health_status` is null → return 404 with message "Health check not yet available"

```
POST /documents/{doc_id}/health/run
```

- Requires auth
- Triggers health check synchronously (for now — async later)
- Returns updated `health_status`

### 5. Trigger health check after embedding (`backend/documents/service.py` or `embeddings/service.py`)

After embeddings are successfully created for a document, call `run_document_health_check()`.

Find where document status is set to `ready` after embedding — add the health check call there. Keep it non-blocking: wrap in try/except so embedding success is not affected by health check failure.

### 6. Frontend: Document list (`frontend/app/dashboard/documents/page.tsx`)

For each document, show a health indicator badge next to the document name:
- `score >= 80` → 🟢 green dot (or "Good")
- `50 <= score < 80` → 🟡 yellow dot (or "Fair")
- `score < 50` → 🔴 red dot (or "Needs attention")
- `health_status == null` → grey dot (or "Checking...")

On click/hover, show warnings list in a tooltip or expandable panel.

Add a "Re-check" button per document that calls POST `/documents/{doc_id}/health/run`.

### 7. API client (`frontend/lib/api.ts`)

```typescript
documents: {
  // existing methods...
  getHealth: (docId: string) => apiRequest('GET', `/documents/${docId}/health`),
  runHealth: (docId: string) => apiRequest('POST', `/documents/${docId}/health/run`),
}
```

---

## TESTING

Before pushing:
- [ ] `pytest -q` — all existing tests pass
- [ ] New test: `run_document_health_check()` with mocked OpenAI returns correct structure
- [ ] New test: score calculation (high warning = -20, etc.)
- [ ] New test: GET `/documents/{doc_id}/health` returns 404 when health_status is null
- [ ] New test: document ownership enforced (can't check another client's doc)
- [ ] Manual test: upload a document → wait for processing → check health indicator in dashboard

---

## GIT PUSH

```bash
git add backend/models.py backend/documents/ backend/migrations/versions/ \
        frontend/app/dashboard/documents/ frontend/lib/api.ts tests/
git commit -m "feat: add document health check with GPT analysis (FI-032)"
git push origin feature/fi-032-doc-health
```

---

## NOTES

- Health check is best-effort — never blocks upload or breaks embeddings
- GPT-4o-mini is cheap enough for this (small prompt, small output)
- First version is synchronous — if it's too slow, move to background task later
- Don't expose raw GPT response to the frontend — only the structured health_status dict

---

## PR DESCRIPTION

```markdown
## Summary
Adds automatic document health check: after embedding, GPT-4o-mini analyzes the document for structural issues and shows warnings in the dashboard. Helps clients understand why their bot might be giving poor answers.

## Changes
- `backend/models.py` — add `health_status` JSON column to `Document`
- `backend/migrations/versions/XXX` — Alembic migration
- `backend/documents/service.py` — health check logic + GPT call
- `backend/documents/routes.py` — GET `/documents/{doc_id}/health` + POST `.../health/run`
- `frontend/app/dashboard/documents/page.tsx` — health score badge + warnings panel
- `frontend/lib/api.ts` — health check API methods

## Testing
- [ ] pytest passes (all existing + new tests)
- [ ] Manual: upload doc → health check runs → warnings shown in dashboard

## Notes
Health check is non-blocking. Failures are logged but don't affect document processing.
```
