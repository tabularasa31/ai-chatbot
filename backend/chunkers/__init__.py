"""Per-content-type chunkers for RAG ingestion. See README.md in this package."""

from backend.chunkers.base import ChunkInfo, MarkdownChunkInfo, PdfChunkInfo
from backend.chunkers.html import clean_html_root, html_to_markdown_text
from backend.chunkers.markdown import chunk_markdown
from backend.chunkers.pdf import chunk_pdf
from backend.chunkers.plaintext import chunk_plaintext
from backend.chunkers.registry import (
    CHUNKING_CONFIG,
    CHUNKING_DEFAULT,
    Chunker,
    get_chunker,
    register_chunker,
)

__all__ = [
    "CHUNKING_CONFIG",
    "CHUNKING_DEFAULT",
    "ChunkInfo",
    "Chunker",
    "MarkdownChunkInfo",
    "PdfChunkInfo",
    "chunk_markdown",
    "chunk_pdf",
    "chunk_plaintext",
    "clean_html_root",
    "get_chunker",
    "html_to_markdown_text",
    "register_chunker",
]
