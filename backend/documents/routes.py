"""FastAPI document management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.auth.middleware import get_current_user
from backend.clients.service import get_client_by_user
from backend.documents.schemas import (
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
)
from backend.documents.service import (
    delete_document,
    get_document,
    get_documents,
    upload_document,
)
from backend.core.db import get_db
from backend.models import User

documents_router = APIRouter(tags=["documents"])

EXT_TO_TYPE = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".json": "swagger",
    ".yaml": "swagger",
    ".yml": "swagger",
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _detect_file_type(filename: str) -> str | None:
    """Detect file_type from extension. Returns None if unsupported."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_TYPE.get(ext)


@documents_router.post("", response_model=DocumentResponse, status_code=201)
def upload_document_route(
    file: UploadFile,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentResponse:
    """
    Upload a document (protected JWT, multipart/form-data).

    Returns 201 Created. Errors: 400 unsupported type/size, 404 no client, 422 parse error.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    file_type = _detect_file_type(file.filename or "")
    if not file_type:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: .pdf, .md, .json, .yaml, .yml",
        )

    content = file.file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size is 50MB",
        )

    try:
        doc = upload_document(
            client_id=client.id,
            filename=file.filename or "unnamed",
            content=content,
            file_type=file_type,
            db=db,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return DocumentResponse(
        id=doc.id,
        filename=doc.filename,
        file_type=doc.file_type.value,
        status=doc.status.value,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@documents_router.get("", response_model=DocumentListResponse)
def list_documents_route(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentListResponse:
    """
    List documents for current user's client (protected JWT).
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        return DocumentListResponse(documents=[])

    docs = get_documents(client.id, db)
    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=d.id,
                filename=d.filename,
                file_type=d.file_type.value,
                status=d.status.value,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in docs
        ]
    )


@documents_router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document_detail_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentDetailResponse:
    """
    Get single document with parsed_text preview (protected JWT).
    404 if not found or not owner.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    doc = get_document(document_id, client.id, db)
    preview = None
    if doc.parsed_text:
        preview = doc.parsed_text[:500] + ("..." if len(doc.parsed_text) > 500 else "")

    return DocumentDetailResponse(
        id=doc.id,
        filename=doc.filename,
        file_type=doc.file_type.value,
        status=doc.status.value,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        parsed_text=preview,
    )


@documents_router.delete("/{document_id}", status_code=204, response_model=None)
def delete_document_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """
    Delete document (protected JWT).
    204 No Content. 404 if not found or not owner.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    delete_document(document_id, client.id, db)
