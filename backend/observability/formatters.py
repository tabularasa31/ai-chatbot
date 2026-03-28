"""Formatting helpers for observability payloads."""

from __future__ import annotations

from typing import Any

from backend.models import Embedding

TEXT_PREVIEW_MAX_LEN = 200
EMBEDDING_PREVIEW_DIMS = 8


def truncate_text(text: str | None, max_len: int = TEXT_PREVIEW_MAX_LEN) -> str:
    """Return a concise text preview for tracing payloads."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def format_embedding_preview(
    embedding: Embedding,
    *,
    score: float | None = None,
    score_name: str = "score",
) -> dict[str, Any]:
    """Serialize an embedding/document pair into a Langfuse-friendly dict."""
    document = embedding.document
    meta = embedding.metadata_json or {}
    payload: dict[str, Any] = {
        "id": str(embedding.id),
        "document_id": str(embedding.document_id),
        "source_url": getattr(document, "source_url", None),
        "page_title": meta.get("page_title") or getattr(document, "filename", None),
        "section_title": meta.get("section_title"),
        "chunk_index": meta.get("chunk_index"),
        "text_preview": truncate_text(embedding.chunk_text),
    }
    if score is not None:
        payload[score_name] = round(score, 4)
    return payload


def format_embedding_results(
    results: list[tuple[Embedding, float]],
    *,
    score_name: str,
) -> list[dict[str, Any]]:
    """Serialize ranked embedding results for tracing."""
    return [
        format_embedding_preview(embedding, score=score, score_name=score_name)
        for embedding, score in results
    ]


def format_query_embedding_preview(vector: list[float] | None) -> list[float]:
    """Store only a short prefix of the query embedding for readability."""
    if not vector:
        return []
    return [round(float(value), 4) for value in vector[:EMBEDDING_PREVIEW_DIMS]]
