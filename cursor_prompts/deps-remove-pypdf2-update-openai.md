# Deps: Remove PyPDF2, update openai — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b chore/deps-pypdf2-openai
```

**IMPORTANT:** Follow these commands in EXACT ORDER:
1. Checkout main branch
2. Pull latest from origin/main
3. Create NEW branch from main

**DO NOT:**
- Skip `git pull origin main`
- Reuse branches from previous attempts
- Work on any branch other than the newly created one

---

## CODE DISCIPLINE

**Scope (you MAY modify):**
- `backend/requirements.txt` — remove PyPDF2, update pypdf and openai versions
- `backend/documents/parsers.py` — update import from PyPDF2 → pypdf

**Do NOT touch:**
- Any other files
- migrations
- Frontend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** Two issues in dependencies:
1. `PyPDF2==3.0.1` is officially deprecated — authors renamed the package to `pypdf`. Both are listed in `requirements.txt` simultaneously (duplication). Code in `parsers.py` imports from the deprecated `PyPDF2`.
2. `openai==1.6.0` is from December 2023, current stable is `1.70+`. Outdated version may lack bug fixes and newer API features.

**Current state in `requirements.txt`:**
```
openai==1.6.0
pypdf==3.17.1
PyPDF2==3.0.1
```

**Current import in `backend/documents/parsers.py`:**
```python
from PyPDF2 import PdfReader
```

---

## WHAT TO DO

### 1. Update `backend/requirements.txt`

Remove `PyPDF2==3.0.1` entirely.
Update versions:
```
openai>=1.70.0
pypdf>=4.0.0
```

### 2. Update import in `backend/documents/parsers.py`

```python
# Before:
from PyPDF2 import PdfReader

# After:
from pypdf import PdfReader
```

The API is compatible — `PdfReader` works identically in `pypdf`. No other changes needed in `parsers.py`.

### 3. Verify no other PyPDF2 imports exist

```bash
grep -r "PyPDF2" backend/
```

Should return no results after the change.

---

## TESTING

Before pushing:
- [ ] `pip install -r requirements.txt` completes without errors
- [ ] `grep -r "PyPDF2" backend/` returns no results
- [ ] `pytest -q` passes (all tests green)
- [ ] PDF parsing still works: upload a PDF document in tests

---

## GIT PUSH

```bash
git add backend/requirements.txt backend/documents/parsers.py
git commit -m "chore: remove deprecated PyPDF2, update pypdf>=4.0.0 and openai>=1.70.0"
git push origin chore/deps-pypdf2-openai
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- `pypdf` is the direct successor of `PyPDF2` by the same authors. `PdfReader` API is identical.
- `openai` 1.x has stable API — breaking changes between 1.6 and 1.70 are minimal. Core methods (`embeddings.create`, `chat.completions.create`) unchanged.

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Removed deprecated PyPDF2 dependency, migrated to its successor `pypdf>=4.0.0`. Updated `openai` to `>=1.70.0`.

## Changes
- `backend/requirements.txt` — removed `PyPDF2==3.0.1`, updated `pypdf>=4.0.0`, `openai>=1.70.0`
- `backend/documents/parsers.py` — import `PdfReader` from `pypdf` instead of `PyPDF2`

## Testing
- [ ] All tests pass (pytest)
- [ ] No PyPDF2 imports remain in codebase
- [ ] PDF parsing functional

## Notes
pypdf is the official successor of PyPDF2 with identical PdfReader API.
```
