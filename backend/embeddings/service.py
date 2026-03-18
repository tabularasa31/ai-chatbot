"""Business logic for embedding creation and management."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from openai import OpenAI
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models import Document, DocumentStatus, Embedding

openai_client = OpenAI(api_key=settings.openai_api_key)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 100,
) -> list[str]:
    """
    Split text into overlapping chunks.

    Args:
        text: Input text to chunk.
        chunk_size: Max characters per chunk.
        overlap: Overlap between consecutive chunks.

    Returns:
        List of chunk strings. Empty chunks are skipped.
    """
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end - overlap if overlap < chunk_size else end
    return chunks


def create_embeddings_for_document(
    document_id: uuid.UUID,
    db: Session,
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
    if doc.status != DocumentStatus.ready:
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
    deleted = db.query(Embedding).filter(Embedding.document_id == document_id).delete()
    db.commit()

    chunks = chunk_text(doc.parsed_text)
    if not chunks:
        return []

    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=chunks,
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"OpenAI API unavailable: {str(e)}",
        ) from e

    embeddings: list[Embedding] = []
    for i, item in enumerate(response.data):
        vector = item.embedding  # list of 1536 floats
        chunk = chunks[i] if i < len(chunks) else ""
        emb = Embedding(
            document_id=document_id,
            chunk_text=chunk,
            vector=None,  # pgvector later; store in metadata for now
            metadata_json={"chunk_index": i, "vector": vector},
        )
        db.add(emb)
        embeddings.append(emb)
    db.commit()
    for emb in embeddings:
        db.refresh(emb)
    return embeddings


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
