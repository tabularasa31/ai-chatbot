"""Table-aware body chunking shared by the pdf and markdown chunkers.

A "table block" is a run of two or more consecutive lines that each look like
a markdown pipe-table row (``| a | b |``). Table blocks become standalone
chunks so a retrieved table row is never diluted by surrounding prose; the
remaining text is chunked with the sentence-based splitter. Oversized tables
are split by rows with the header repeated in every part.
"""

from __future__ import annotations

import re

from backend.chunkers.base import PdfChunkInfo
from backend.chunkers.plaintext import chunk_plaintext

_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")
_MIN_TABLE_LINES = 2


def _split_segments(text: str) -> list[tuple[str, int, int]]:
    """Split ``text`` into ("text" | "table", start, end) segments."""
    segments: list[tuple[str, int, int]] = []
    text_start = 0
    table_start: int | None = None
    table_lines = 0
    pos = 0

    def close_table(end: int) -> None:
        nonlocal text_start, table_start, table_lines
        if table_start is None:
            return
        if table_lines >= _MIN_TABLE_LINES:
            if table_start > text_start:
                segments.append(("text", text_start, table_start))
            segments.append(("table", table_start, end))
            text_start = end
        table_start = None
        table_lines = 0

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if _TABLE_LINE_RE.match(stripped):
            if table_start is None:
                table_start = pos
                table_lines = 0
            table_lines += 1
        else:
            close_table(pos)
        pos += len(line)
    close_table(len(text))
    if len(text) > text_start:
        segments.append(("text", text_start, len(text)))
    return segments


def _split_table_rows(
    table_text: str,
    base_offset: int,
    chunk_size: int,
) -> list[tuple[str, int, int]]:
    """Split an oversized table into row groups, repeating the header.

    Returns (chunk_text, char_offset, char_end) triples; offsets cover the
    row span of each group (the repeated header is prefix-only context).
    """
    if len(table_text.strip()) <= chunk_size:
        stripped = table_text.strip()
        lead = len(table_text) - len(table_text.lstrip())
        return [(stripped, base_offset + lead, base_offset + lead + len(stripped))]

    lines: list[tuple[str, int, int]] = []
    pos = 0
    for line in table_text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if stripped.strip():
            lines.append((stripped, pos, pos + len(stripped)))
        pos += len(line)

    header: list[str] = []
    body = lines
    if len(lines) >= 2 and _TABLE_SEPARATOR_RE.match(lines[1][0]):
        header = [lines[0][0], lines[1][0]]
        body = lines[2:]
    if not body:
        body = lines
        header = []

    header_len = sum(len(h) + 1 for h in header)
    parts: list[tuple[str, int, int]] = []
    group: list[tuple[str, int, int]] = []
    group_len = 0
    include_header_span = bool(header)

    def flush() -> None:
        nonlocal group, group_len, include_header_span
        if not group:
            return
        rows_text = "\n".join(g[0] for g in group)
        chunk = "\n".join([*header, rows_text]) if header else rows_text
        start = base_offset + group[0][1]
        if include_header_span and header:
            # First part: the span also covers the header rows themselves.
            start = base_offset + lines[0][1]
            include_header_span = False
        parts.append((chunk, start, base_offset + group[-1][2]))
        group = []
        group_len = 0

    for row in body:
        row_len = len(row[0]) + 1
        if group and header_len + group_len + row_len > chunk_size:
            flush()
        group.append(row)
        group_len += row_len
    flush()
    return parts


def chunk_text_with_tables(
    text: str,
    chunk_size: int,
    overlap_sentences: int,
    base_offset: int = 0,
) -> list[PdfChunkInfo]:
    """Chunk ``text``: tables become standalone chunks, prose is sentence-split.

    ``base_offset`` shifts all reported offsets (for callers chunking a slice
    of a larger document). ``chunk_index`` starts at 0 — callers renumber when
    concatenating results.
    """
    chunks: list[PdfChunkInfo] = []
    for kind, start, end in _split_segments(text):
        segment = text[start:end]
        if kind == "table":
            for part_text, p_start, p_end in _split_table_rows(segment, base_offset + start, chunk_size):
                chunks.append(
                    {
                        "text": part_text,
                        "chunk_index": len(chunks),
                        "char_offset": p_start,
                        "char_end": p_end,
                        "subtype": "table",
                    }
                )
        else:
            for piece in chunk_plaintext(segment, chunk_size=chunk_size, overlap_sentences=overlap_sentences):
                chunks.append(
                    {
                        "text": piece["text"],
                        "chunk_index": len(chunks),
                        "char_offset": base_offset + start + piece["char_offset"],
                        "char_end": base_offset + start + piece["char_end"],
                    }
                )
    return chunks
