"""FastAPI embedding management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_user
from backend.clients.service import get_client_by_user
from backend.core.db import get_db
from backend.documents.service import get_document
from backend.embeddings.schemas import EmbeddingListResponse, EmbeddingResponse
from backend.embeddings.service import (
    create_embeddings_for_document,
    delete_embeddings_for_document,
    get_embeddings_for_document,
)
from backend.models import User

embeddings_router = APIRouter(tags=["embeddings"])


def _chunk_preview(text: str, max_len: int = 100) -> str:
    """Return first max_len chars of chunk for response."""
    if len(text) <= max_len:
        return text
    return text[:max_len]


@embeddings_router.post(
    "/documents/{document_id}",
    status_code=200,
)
def create_embeddings_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Trigger embedding creation for a document (protected JWT).

    Returns document_id, chunks_created, status.
    Errors: 404 (doc not found/not owner), 400 (doc not ready/no text), 503 (OpenAI unavailable).
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    get_document(document_id, client.id, db)  # 404 if not found or not owner

    embeddings = create_embeddings_for_document(document_id, db)
    return {
        "document_id": str(document_id),
        "chunks_created": len(embeddings),
        "status": "ready",
    }


@embeddings_router.get(
    "/documents/{document_id}",
    response_model=EmbeddingListResponse,
)
def list_embeddings_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> EmbeddingListResponse:
    """
    List all embeddings for a document (protected JWT).

    Errors: 404 (doc not found or not owner).
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    embeddings = get_embeddings_for_document(document_id, client.id, db)
    return EmbeddingListResponse(
        embeddings=[
            EmbeddingResponse(
                id=emb.id,
                document_id=emb.document_id,
                chunk_text=_chunk_preview(emb.chunk_text),
                created_at=emb.created_at,
            )
            for emb in embeddings
        ],
        total_chunks=len(embeddings),
    )


@embeddings_router.delete("/documents/{document_id}")
def delete_embeddings_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Delete all embeddings for a document (protected JWT).

    Returns deleted count. Errors: 404 (doc not found or not owner).
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    get_document(document_id, client.id, db)  # 404 if not found or not owner
    deleted = delete_embeddings_for_document(document_id, db)
    return {"deleted": deleted}

