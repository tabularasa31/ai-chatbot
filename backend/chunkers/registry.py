"""Content-type → chunker registry.

Adding a new content type = write a chunker and ``register_chunker`` it here;
no changes to the embedding pipeline core. ``swagger`` is the one deliberate
exception: OpenAPI chunking needs per-operation metadata rehydrated from the
rendered preview text, so it stays special-cased in
``backend/embeddings/service.py`` (``_build_swagger_chunks``).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from backend.chunkers.base import ChunkInfo
from backend.chunkers.markdown import chunk_markdown
from backend.chunkers.pdf import chunk_pdf
from backend.chunkers.plaintext import chunk_plaintext

# A chunker takes the document's parsed text and returns ordered chunks.
Chunker = Callable[[str], list[ChunkInfo]]

# Optimal chunking parameters per document type.
# Tune these values here when re-evaluating retrieval quality.
CHUNKING_CONFIG: dict[str, dict[str, int]] = {
    "swagger": {"chunk_size": 500, "overlap_sentences": 0},
    "markdown": {"chunk_size": 700, "overlap_sentences": 1},
    "html": {"chunk_size": 700, "overlap_sentences": 1},
    "pdf": {"chunk_size": 1000, "overlap_sentences": 1},
    # future types
    "logs": {"chunk_size": 300, "overlap_sentences": 0},
    "code": {"chunk_size": 600, "overlap_sentences": 1},
}
CHUNKING_DEFAULT: dict[str, int] = {"chunk_size": 700, "overlap_sentences": 1}


def _params(content_type: str) -> dict[str, int]:
    return CHUNKING_CONFIG.get(content_type, CHUNKING_DEFAULT)


_REGISTRY: dict[str, Chunker] = {}


def register_chunker(content_type: str, chunker: Chunker) -> None:
    """Register (or override) the chunker for a content type."""
    _REGISTRY[content_type] = chunker


def get_chunker(content_type: str) -> Chunker:
    """Return the chunker for ``content_type``; plaintext fallback if unknown."""
    fallback = partial(chunk_plaintext, **CHUNKING_DEFAULT)
    return _REGISTRY.get(content_type, fallback)


register_chunker("plaintext", partial(chunk_plaintext, **_params("plaintext")))
register_chunker("docx", partial(chunk_plaintext, **_params("docx")))
register_chunker("markdown", partial(chunk_markdown, **_params("markdown")))
# Uploaded HTML is rendered to markdown-ish text at parse time
# (parsers.parse_html), so the heading-aware chunker applies as-is.
register_chunker("html", partial(chunk_markdown, **_params("html")))
register_chunker("pdf", partial(chunk_pdf, **_params("pdf")))
