"""Business logic for document upload and parsing."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models import Document, DocumentStatus, DocumentType
from backend.documents.parsers import parse_markdown, parse_pdf, parse_swagger

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_TYPES = {"pdf", "markdown", "swagger"}


def _parse_content(content: bytes, file_type: str) -> str:
    """Parse content based on file_type. Raises ValueError on parse error."""
    if file_type == "pdf":
        return parse_pdf(content)
    if file_type == "markdown":
        return parse_markdown(content)
    if file_type == "swagger":
        return parse_swagger(content)
    raise ValueError(f"Unsupported file type: {file_type}")


def upload_document(
    client_id: uuid.UUID,
    filename: str,
    content: bytes,
    file_type: str,
    db: Session,
) -> Document:
    """
    Upload and parse a document.

    Validates file_type (pdf, markdown, swagger only) and size (max 50MB).
    Saves document with status=processing, parses, then updates to ready or error.
    """
    if file_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: pdf, markdown, swagger",
        )
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size is 50MB",
        )

    doc_type = DocumentType[file_type]
    doc = Document(
        client_id=client_id,
        filename=filename,
        file_type=doc_type,
        status=DocumentStatus.processing,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        parsed = _parse_content(content, file_type)
        doc.parsed_text = parsed
        doc.status = DocumentStatus.ready
    except ValueError:
        doc.status = DocumentStatus.error
    db.commit()
    db.refresh(doc)
    return doc


def get_documents(client_id: uuid.UUID, db: Session) -> list[Document]:
    """Return all documents for this client, ordered by created_at DESC."""
    return (
        db.query(Document)
        .filter(Document.client_id == client_id)
        .order_by(Document.created_at.desc())
        .all()
    )


def get_document(
    document_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> Document:
    """
    Get document by id. Verifies ownership (document.client_id == client_id).
    Raises 404 if not found or not owner.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or doc.client_id != client_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def delete_document(
    document_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> None:
    """
    Delete document. Verifies ownership before delete.
    CASCADE: embeddings are deleted automatically (already in DB schema).
    Raises 404 if not found or not owner.
    """
    doc = get_document(document_id, client_id, db)
    db.delete(doc)
    db.commit()
