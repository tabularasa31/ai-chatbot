"""Layout-aware PDF chunker.

Operates on the parsed text produced by ``backend.documents.parsers.parse_pdf``,
which renders detected tables as markdown pipe tables and resolves multi-column
layouts at parse time. Tables become standalone chunks (``subtype: "table"``);
prose is sentence-chunked.
"""

from __future__ import annotations

from backend.chunkers.base import PdfChunkInfo
from backend.chunkers.tables import chunk_text_with_tables


def chunk_pdf(
    text: str,
    chunk_size: int = 1000,
    overlap_sentences: int = 1,
) -> list[PdfChunkInfo]:
    if not text or not text.strip():
        return []
    return chunk_text_with_tables(
        text,
        chunk_size=chunk_size,
        overlap_sentences=overlap_sentences,
    )
