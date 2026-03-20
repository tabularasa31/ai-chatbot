# FI-009: Improved Chunking + Metadata — Cursor Prompt

⚠️ **CRITICAL: YOU MUST FOLLOW THE SETUP EXACTLY AS WRITTEN. NO SHORTCUTS.**

---

## SETUP

```bash
cd <repo-root>
git checkout main
git pull origin main
git checkout -b feature/fi-009-improved-chunking
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
- `backend/embeddings/service.py` — update `chunk_text()` and `create_embeddings_for_document()`
- `tests/test_embeddings.py` — update tests for new chunk format

**Do NOT touch:**
- `backend/models.py`
- migrations
- `backend/search/service.py`
- `backend/chat/service.py`
- Frontend files

**If you think something outside Scope must be changed, STOP and describe it in a comment instead of editing code.**

---

## CONTEXT

**Problem:** Current `chunk_text()` splits by character count — chunks can start/end mid-word or mid-sentence, losing context at boundaries. `metadata_json` only stores `{"chunk_index": i}` with no position info.

**Current implementation:**
```python
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap if overlap < chunk_size else end
    return chunks

# Used as:
chunks = chunk_text(doc.parsed_text)
# chunks is list[str]
for i, item in enumerate(response.data):
    emb = Embedding(
        chunk_text=chunks[i],
        vector=item.embedding,
        metadata_json={"chunk_index": i},
    )
```

---

## WHAT TO DO

### 1. Rewrite `chunk_text` to split by sentences

New function returns `list[dict]` instead of `list[str]`:

```python
def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap_sentences: int = 1,
) -> list[dict]:
    """
    Split text into chunks by sentences (not raw characters).

    Returns list of dicts:
    {
        "text": str,          # chunk content
        "chunk_index": int,   # position in document
        "char_offset": int,   # start position in original text
        "char_end": int,      # end position in original text
    }
    """
    if not text.strip():
        return []

    import re
    # Split into sentences on . ? ! followed by space or newline, or on \n\n
    sentences = re.split(r'(?<=[.?!])\s+|\n{2,}', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return []

    chunks = []
    current_sentences: list[str] = []
    current_len = 0
    char_offset = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        # If adding this sentence exceeds chunk_size and we already have content — flush
        if current_len + sentence_len > chunk_size and current_sentences:
            chunk_text_str = " ".join(current_sentences)
            chunks.append({
                "text": chunk_text_str,
                "chunk_index": len(chunks),
                "char_offset": char_offset,
                "char_end": char_offset + len(chunk_text_str),
            })
            # Overlap: keep last N sentences for next chunk
            overlap = current_sentences[-overlap_sentences:] if overlap_sentences > 0 else []
            char_offset += len(chunk_text_str) + 1
            current_sentences = overlap
            current_len = sum(len(s) for s in current_sentences)

        current_sentences.append(sentence)
        current_len += sentence_len + 1  # +1 for space

    # Flush remaining
    if current_sentences:
        chunk_text_str = " ".join(current_sentences)
        chunks.append({
            "text": chunk_text_str,
            "chunk_index": len(chunks),
            "char_offset": char_offset,
            "char_end": char_offset + len(chunk_text_str),
        })

    return chunks
```

### 2. Update `create_embeddings_for_document`

```python
chunks = chunk_text(doc.parsed_text)  # now list[dict]
if not chunks:
    return []

chunk_texts = [c["text"] for c in chunks]  # extract strings for OpenAI

# ... OpenAI call with chunk_texts ...

for i, item in enumerate(response.data):
    chunk = chunks[i]
    emb = Embedding(
        document_id=document_id,
        chunk_text=chunk["text"],
        vector=item.embedding,
        metadata_json={
            "chunk_index": chunk["chunk_index"],
            "char_offset": chunk["char_offset"],
            "char_end": chunk["char_end"],
            "filename": doc.filename,
            "file_type": doc.file_type.value,
        },
    )
    db.add(emb)
```

---

## TESTING

Before pushing:
- [ ] `chunk_text("")` returns `[]`
- [ ] `chunk_text("Hello world.")` returns list with at least 1 dict with keys `text`, `chunk_index`, `char_offset`, `char_end`
- [ ] Chunks don't split mid-word
- [ ] `pytest -q` passes
- [ ] Update existing tests in `tests/test_embeddings.py` to handle `list[dict]` instead of `list[str]`

---

## GIT PUSH

```bash
git add backend/embeddings/service.py tests/test_embeddings.py
git commit -m "feat: sentence-aware chunking with position metadata (FI-009)"
git push origin feature/fi-009-improved-chunking
```

**STRICT ORDER:**
1. Add files
2. Commit with message
3. Push to origin
4. Do NOT skip any step

---

## NOTES

- Chunk size is a soft limit — a single sentence longer than `chunk_size` won't be split further
- `overlap_sentences=1` means the last sentence of chunk N becomes the first sentence of chunk N+1
- No DB migration needed — `metadata_json` is already JSON, just stores more fields now

---

## PR DESCRIPTION

After completing the implementation, provide the Pull Request description in English (Markdown format):

```markdown
## Summary
Replaced character-based chunking with sentence-aware chunking. Added position metadata (char_offset, char_end, filename, file_type) to each embedding.

## Changes
- `backend/embeddings/service.py` — rewritten chunk_text(), updated create_embeddings_for_document()
- `tests/test_embeddings.py` — updated for new list[dict] chunk format

## Testing
- [ ] Tests pass
- [ ] Chunks don't split mid-sentence
- [ ] Metadata stored correctly in embeddings

## Notes
No migration needed. metadata_json is JSON, backwards-compatible.
```
