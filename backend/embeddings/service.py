"""Business logic for embedding creation and management."""

from __future__ import annotations

import re
import uuid
from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.openai_client import get_openai_client
from backend.models import Document, DocumentStatus, Embedding

# Optimal chunking parameters per document type.
# Tune these values here when re-evaluating retrieval quality.
CHUNKING_CONFIG: dict[str, dict[str, int]] = {
    "swagger": {"chunk_size": 500, "overlap_sentences": 0},
    "markdown": {"chunk_size": 700, "overlap_sentences": 1},
    "pdf":      {"chunk_size": 1000, "overlap_sentences": 1},
    # future types
    "logs":     {"chunk_size": 300, "overlap_sentences": 0},
    "code":     {"chunk_size": 600, "overlap_sentences": 1},
}
_CHUNKING_DEFAULT: dict[str, int] = {"chunk_size": 700, "overlap_sentences": 1}


class ChunkInfo(TypedDict):
    """One text chunk with position in the original document."""

    text: str
    chunk_index: int
    char_offset: int
    char_end: int


def _sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """
    Split like chunk_text (sentence boundaries); return (sentence, start, end) in `text`.
    """
    if not text.strip():
        return []
    lead = len(text) - len(text.lstrip())
    trail = len(text) - len(text.rstrip())
    body = text[lead : len(text) - trail]
    raw_parts = re.split(r"(?<=[.?!])\s+|\n{2,}", body)
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


def _joined_char_len(parts: list[tuple[str, int, int]]) -> int:
    if not parts:
        return 0
    return sum(len(p[0]) for p in parts) + (len(parts) - 1)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap_sentences: int = 1,
) -> list[ChunkInfo]:
    """
    Split text into chunks by sentences (not raw characters).

    Returns list of dicts with text, chunk_index, char_offset, char_end (offsets in original `text`).
    """
    spans = _sentence_spans(text)
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
            current_len = _joined_char_len(current)

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


def create_embeddings_for_document(
    document_id: uuid.UUID,
    db: Session,
    *,
    api_key: str,
) -> list[Embedding]:
    """
    Create embeddings for a document's parsed text.

    Fetches document, chunks parsed_text, calls OpenAI embeddings API,
    saves Embedding records. Replaces existing embeddings for the document.

    Raises:
        HTTPException 404: Document not found.
        HTTPException 400: Document status != ready or parsed_text empty.
        HTTPException 503: OpenAI API error.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in (DocumentStatus.ready, DocumentStatus.embedding):
        raise HTTPException(
            status_code=400,
            detail="Document is not ready for embedding. Status must be 'ready'.",
        )
    if not doc.parsed_text or not doc.parsed_text.strip():
        raise HTTPException(
            status_code=400,
            detail="Document has no parsed text to embed.",
        )

    # Delete existing embeddings (re-embed on demand)
    db.query(Embedding).filter(Embedding.document_id == document_id).delete()
    db.commit()

    cfg = CHUNKING_CONFIG.get(doc.file_type.value, _CHUNKING_DEFAULT)
    chunks = chunk_text(doc.parsed_text, **cfg)
    if not chunks:
        return []

    chunk_texts = [c["text"] for c in chunks]

    openai_client = get_openai_client(api_key)
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=chunk_texts,
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"OpenAI API unavailable: {str(e)}",
        ) from e

    embeddings: list[Embedding] = []
    for i, item in enumerate(response.data):
        vector = item.embedding  # list of 1536 floats
        chunk = chunks[i] if i < len(chunks) else None
        text_part = chunk["text"] if chunk else ""
        meta_base = (
            {
                "chunk_index": chunk["chunk_index"],
                "char_offset": chunk["char_offset"],
                "char_end": chunk["char_end"],
                "filename": doc.filename,
                "file_type": doc.file_type.value,
            }
            if chunk
            else {"chunk_index": i}
        )
        emb = Embedding(
            document_id=document_id,
            chunk_text=text_part,
            vector=vector,
            metadata_json=meta_base,
        )
        db.add(emb)
        embeddings.append(emb)
    db.commit()
    for emb in embeddings:
        db.refresh(emb)
    try:
        from backend.documents.service import run_document_health_check

        run_document_health_check(document_id, db, api_key)
    except Exception:
        pass
    return embeddings


def run_embeddings_background(document_id: uuid.UUID, api_key: str) -> None:
    """
    Background task: create embeddings using a dedicated DB session.

    Sets document status to `ready` on success or `error` on failure.
    Must be called via FastAPI BackgroundTasks (not from a request handler directly).
    """
    import logging

    from backend.core.db import SessionLocal

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        create_embeddings_for_document(document_id, db, api_key=api_key)
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = DocumentStatus.ready
            db.commit()
        # Phase 1 (best-effort): update tenant knowledge after successful indexing.
        # Never break embeddings pipeline if extraction fails.
        try:
            from backend.tenant_knowledge.extract_tenant_knowledge import (
                run_extract_tenant_knowledge_for_document,
            )

            run_extract_tenant_knowledge_for_document(
                document_id=document_id,
                db=db,
                api_key=api_key,
            )
        except Exception:
            # Extraction is intentionally best-effort.
            logger.warning(
                "Tenant knowledge extraction failed for document_id=%s",
                document_id,
                exc_info=True,
            )
    except Exception:
        logger.exception("Background embedding failed for document %s", document_id)
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = DocumentStatus.error
            db.commit()
    finally:
        db.close()


def get_embeddings_for_document(
    document_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> list[Embedding]:
    """
    Get all embeddings for a document. Verifies document ownership.

    Raises:
        HTTPException 404: Document not found or not owned by client.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or doc.client_id != client_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return (
        db.query(Embedding)
        .filter(Embedding.document_id == document_id)
        .order_by(Embedding.created_at.asc())
        .all()
    )


def delete_embeddings_for_document(
    document_id: uuid.UUID,
    db: Session,
) -> int:
    """
    Delete all embeddings for a document.

    Returns:
        Count of deleted embeddings.
    """
    result = db.query(Embedding).filter(Embedding.document_id == document_id).delete()
    db.commit()
    return result
