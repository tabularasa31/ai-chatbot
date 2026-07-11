"""Shared chunk types and sentence-splitting helpers for all chunkers."""

from __future__ import annotations

import re
from typing import TypedDict


class ChunkInfo(TypedDict):
    """One text chunk with position in the original document.

    ``char_offset``/``char_end`` always point into the *source body text* the
    chunk was cut from. Structure-aware chunkers (markdown, pdf) may prepend
    context (heading path, table header) to ``text``, so ``text`` is not
    guaranteed to equal ``source[char_offset:char_end]`` for those types —
    the slice covers the body span the chunk represents.
    """

    text: str
    chunk_index: int
    char_offset: int
    char_end: int


class MarkdownChunkInfo(ChunkInfo, total=False):
    """Markdown chunk: carries the heading path the chunk lives under."""

    heading_path: str
    subtype: str


class PdfChunkInfo(ChunkInfo, total=False):
    """PDF chunk: ``subtype`` marks structural chunks (e.g. ``table``)."""

    subtype: str


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+|\n{2,}")


def sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """Split on sentence boundaries; return (sentence, start, end) in ``text``."""
    if not text.strip():
        return []
    lead = len(text) - len(text.lstrip())
    trail = len(text) - len(text.rstrip())
    body = text[lead : len(text) - trail]
    raw_parts = _SENTENCE_SPLIT_RE.split(body)
    sentences = [p.strip() for p in raw_parts if p.strip()]
    if not sentences:
        return []

    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for sent in sentences:
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        idx = body.find(sent, cursor)
        if idx < 0:
            idx = cursor
        start = lead + idx
        end = start + len(sent)
        spans.append((sent, start, end))
        cursor = idx + len(sent)
    return spans


def joined_char_len(parts: list[tuple[str, int, int]]) -> int:
    if not parts:
        return 0
    return sum(len(p[0]) for p in parts) + (len(parts) - 1)
