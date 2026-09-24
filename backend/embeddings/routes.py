"""FastAPI embedding management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_tenant
from backend.core.db import get_db
from backend.documents.service import get_document
from backend.embeddings.service import run_embeddings_background
from backend.models import DocumentStatus, Tenant

embeddings_router = APIRouter(tags=["embeddings"])


@embeddings_router.post(
    "/documents/{document_id}",
    status_code=202,
)
def create_embeddings_route(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Trigger embedding creation for a document (protected JWT).

    Returns 202 Accepted immediately; embedding runs in the background.
    Poll GET /documents/{id} until status is `ready` or `error`.
    Errors: 404 (doc not found/not owner), 400 (doc not ready/no text).
    """
    if not tenant.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )

    doc = get_document(document_id, tenant.id, db)  # 404 if not found or not owner
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

    doc.status = DocumentStatus.embedding
    db.commit()

    background_tasks.add_task(run_embeddings_background, document_id, tenant.openai_api_key)
    return {
        "document_id": str(document_id),
        "status": "embedding",
    }
