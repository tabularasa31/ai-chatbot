# backend/chunkers — per-content-type chunking for RAG ingestion

Retrieval quality is ~50% chunk quality: badly cut chunks cannot be fixed by a
better embedding model or re-ranker. This package replaces the old
one-splitter-fits-all approach with a chunker per content type.

## Which chunker handles what

| `Document.file_type` | Chunker | Strategy |
|----------------------|---------|----------|
| `markdown` | `chunk_markdown` | Splits on ATX headings (`#`..`######`), tracks the heading stack, and prepends the heading path (`H1 > H2 > H3`) to every chunk. Oversized sections are recursively sentence-split. Pipe tables become standalone chunks. Headings inside code fences are ignored. |
| `html` | `chunk_markdown` | HTML is converted to markdown-ish text at **parse** time (`parsers.parse_html` → `html_to_markdown_text`): boilerplate (`nav`, `footer`, `aside`, `script`, `form`, `button`, ...) is stripped, `<main>`/`<article>` is preferred as the content root, headings become ATX lines, `<table>` becomes a pipe table. The markdown chunker then applies as-is. |
| `pdf` | `chunk_pdf` | `parsers.parse_pdf` is layout-aware (pdfplumber): two-column pages are detected and read column-by-column, tables are rendered as markdown pipe tables. The chunker turns each table into a standalone chunk (`subtype: "table"`; oversized tables split by rows with the header repeated) and sentence-splits the prose. Falls back to plain pypdf extraction on any pdfplumber failure. |
| `plaintext`, `docx` | `chunk_plaintext` | Sentence-boundary chunking with a soft character budget and sentence overlap. |
| *unknown* | `chunk_plaintext` | Registry fallback — nothing ever goes unchunked. |
| `swagger` | — | Deliberately **not** in the registry: OpenAPI chunking rehydrates per-operation metadata from the rendered preview text, so it stays special-cased in `backend/embeddings/service.py::_build_swagger_chunks`. |

The URL crawler (`backend/documents/embedder.py`) keeps its own section
chunker (different output shape, feeds `url_service` directly) but shares the
DOM cleanup via `clean_html_root` from this package.

## Chunk shape

Every chunker returns `list[ChunkInfo]`: `text`, `chunk_index`, `char_offset`,
`char_end`. Offsets always point into the source body span; structure-aware
chunkers may prepend synthesized context (heading path, table header) to
`text`, so `text` is not guaranteed to equal the raw slice. Extra keys
(`heading_path`, `subtype`) flow into `Embedding.metadata_json` automatically.

## Chunk-size budgets

Per-type budgets live in `registry.py::CHUNKING_CONFIG` (soft limits — a
single sentence longer than the budget is never split). Tune them there when
re-evaluating retrieval quality.

## How to add a new content type

1. Write a chunker `def chunk_foo(text: str, ...) -> list[ChunkInfo]` in a new
   module in this package (compose `chunk_plaintext` / `chunk_text_with_tables`
   for the heavy lifting where possible).
2. Add a budget entry to `CHUNKING_CONFIG` and a
   `register_chunker("foo", partial(chunk_foo, **_params("foo")))` line in
   `registry.py`.
3. If it is an uploadable format: add the extension to
   `backend/documents/routes.py::EXT_TO_TYPE`, the type to
   `backend/documents/service.py::ALLOWED_TYPES`, a parser branch in
   `_parse_content`, and the enum value to `backend/models/enums.py::DocumentType`
   (stored as VARCHAR — no migration needed).
4. Add tests to `tests/test_chunkers.py`.

## Re-indexing caveat

Chunking runs at embed time. Changing a strategy affects only newly embedded
documents; existing chunks stay until the document is re-embedded
(delete-and-recreate via `POST /embeddings/documents/{id}` or a re-crawl).
Before/after retrieval evals must bracket the re-index, not the deploy.
