"""Heading-aware markdown chunker.

Splits on ATX headings (``#`` .. ``######``), keeps a heading-path stack
(``H1 > H2 > H3``) and prepends it to every chunk so retrieval sees the
section context and citations show where the chunk came from. Section bodies
larger than the chunk budget are recursively split with the sentence-based
splitter; pipe tables inside a section become standalone chunks.

Headings inside fenced code blocks (``` / ~~~) are ignored. Setext headings
(underline style) are not detected — they chunk as regular prose.
"""

from __future__ import annotations

import re

from backend.chunkers.base import MarkdownChunkInfo
from backend.chunkers.plaintext import chunk_plaintext
from backend.chunkers.tables import chunk_text_with_tables

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")


def _split_sections(text: str) -> list[tuple[str, int, int]]:
    """Return (heading_path, body_start, body_end) for each section.

    The preamble before the first heading gets an empty path. Heading lines
    themselves are not part of any body — their content lives in the path.
    """
    sections: list[tuple[str, int, int]] = []
    stack: list[tuple[int, str]] = []
    fence_marker: str | None = None
    section_path = ""
    section_start = 0
    pos = 0

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        fence = _FENCE_RE.match(stripped)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            pos += len(line)
            continue
        heading = _ATX_HEADING_RE.match(stripped) if fence_marker is None else None
        if heading:
            sections.append((section_path, section_start, pos))
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section_path = " > ".join(t for _, t in stack)
            section_start = pos + len(line)
        pos += len(line)

    sections.append((section_path, section_start, len(text)))
    return sections


def chunk_markdown(
    text: str,
    chunk_size: int = 700,
    overlap_sentences: int = 1,
) -> list[MarkdownChunkInfo]:
    """Chunk markdown by heading sections with heading-path context.

    Every chunk under a heading starts with the heading path followed by a
    blank line; ``char_offset``/``char_end`` cover the body span only (the
    path prefix is synthesized context). Documents without headings degrade
    to plain sentence-based chunking.
    """
    if not text or not text.strip():
        return []

    chunks: list[MarkdownChunkInfo] = []
    for path, start, end in _split_sections(text):
        body = text[start:end]
        if not body.strip():
            continue
        for piece in chunk_text_with_tables(
            body,
            chunk_size=chunk_size,
            overlap_sentences=overlap_sentences,
            base_offset=start,
        ):
            chunk: MarkdownChunkInfo = {
                "text": f"{path}\n\n{piece['text']}" if path else piece["text"],
                "chunk_index": len(chunks),
                "char_offset": piece["char_offset"],
                "char_end": piece["char_end"],
            }
            if path:
                chunk["heading_path"] = path
            if piece.get("subtype"):
                chunk["subtype"] = piece["subtype"]
            chunks.append(chunk)

    if not chunks:
        # Degenerate documents (e.g. headings only): fall back to plain
        # chunking so the document still gets indexed.
        return [
            MarkdownChunkInfo(**piece)
            for piece in chunk_plaintext(text, chunk_size=chunk_size, overlap_sentences=overlap_sentences)
        ]
    return chunks
