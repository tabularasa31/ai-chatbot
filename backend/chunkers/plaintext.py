"""Sentence-based character-budget chunker — the universal fallback."""

from __future__ import annotations

from backend.chunkers.base import ChunkInfo, joined_char_len, sentence_spans


def chunk_plaintext(
    text: str,
    chunk_size: int = 500,
    overlap_sentences: int = 1,
) -> list[ChunkInfo]:
    """
    Split text into chunks by sentences (not raw characters).

    Returns list of dicts with text, chunk_index, char_offset, char_end
    (offsets in original ``text``). ``chunk_size`` is a soft limit: a single
    sentence longer than the budget is never split.
    """
    spans = sentence_spans(text)
    if not spans:
        return []

    chunks: list[ChunkInfo] = []
    current: list[tuple[str, int, int]] = []
    current_len = 0

    for sentence, s_start, s_end in spans:
        sentence_len = len(sentence)
        if current_len + sentence_len > chunk_size and current:
            chunk_text_str = " ".join(s[0] for s in current)
            chunks.append(
                {
                    "text": chunk_text_str,
                    "chunk_index": len(chunks),
                    "char_offset": current[0][1],
                    "char_end": current[-1][2],
                }
            )
            overlap = current[-overlap_sentences:] if overlap_sentences > 0 else []
            current = list(overlap)
            current_len = joined_char_len(current)

        current.append((sentence, s_start, s_end))
        current_len += sentence_len + 1

    if current:
        chunk_text_str = " ".join(s[0] for s in current)
        chunks.append(
            {
                "text": chunk_text_str,
                "chunk_index": len(chunks),
                "char_offset": current[0][1],
                "char_end": current[-1][2],
            }
        )

    return chunks
