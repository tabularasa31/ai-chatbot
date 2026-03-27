"""FastAPI document management endpoints."""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session, selectinload

from backend.auth.middleware import get_current_user, require_verified_user
from backend.clients.service import get_client_by_user
from backend.core.db import get_db
from backend.core.limiter import limiter
from backend.documents.schemas import (
    DocumentDetailResponse,
    DocumentHealthStatusResponse,
    DocumentListResponse,
    DocumentResponse,
    KnowledgeSourcesResponse,
    SourcePageResponse,
    UrlSourceCreateRequest,
    UrlSourceDetailResponse,
    UrlSourceResponse,
    UrlSourceRunResponse,
    UrlSourceUpdateRequest,
)
from backend.documents.service import (
    delete_document,
    get_document,
    get_documents,
    run_document_health_check,
    upload_document,
)
from backend.documents.url_service import (
    crawl_url_source,
    create_url_source,
    delete_source_document,
    delete_url_source,
    get_url_source,
    list_knowledge_sources,
    trigger_refresh,
    update_url_source,
)
from backend.models import Document, UrlSource, UrlSourceRun, User

documents_router = APIRouter(tags=["documents"])

EXT_TO_TYPE = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".json": "swagger",
    ".yaml": "swagger",
    ".yml": "swagger",
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _detect_file_type(filename: str) -> Optional[str]:
    """Detect file_type from extension. Returns None if unsupported."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_TYPE.get(ext)


def _document_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        filename=doc.filename,
        file_type=doc.file_type.value,
        status=doc.status.value,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        health_status=doc.health_status,
    )


def _url_source_response(source: UrlSource) -> UrlSourceResponse:
    return UrlSourceResponse(
        id=source.id,
        name=source.name or source.url,
        url=source.url,
        status=source.status.value,
        schedule=source.crawl_schedule.value,
        pages_found=source.pages_found,
        pages_indexed=source.pages_indexed,
        chunks_created=source.chunks_created,
        last_crawled_at=source.last_crawled_at,
        next_crawl_at=source.next_crawl_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
        warning_message=source.warning_message,
        error_message=source.error_message,
        exclusion_patterns=list(source.exclusion_patterns or []),
    )


def _url_source_run_response(run: UrlSourceRun) -> UrlSourceRunResponse:
    return UrlSourceRunResponse(
        id=run.id,
        status=run.status,
        pages_found=run.pages_found,
        pages_indexed=run.pages_indexed,
        failed_urls=list(run.failed_urls or []),
        duration_seconds=run.duration_seconds,
        error_message=run.error_message,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@documents_router.post("", response_model=DocumentResponse, status_code=201)
@limiter.limit("20/hour")
def upload_document_route(
    request: Request,
    file: UploadFile,
    current_user: Annotated[User, Depends(require_verified_user)],
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
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 50MB")

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

    return _document_response(doc)


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
    return DocumentListResponse(documents=[_document_response(d) for d in docs])


@documents_router.get("/sources", response_model=KnowledgeSourcesResponse)
def list_knowledge_sources_route(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KnowledgeSourcesResponse:
    """List file documents and URL sources for the Knowledge page."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    payload = list_knowledge_sources(client.id, db)
    return KnowledgeSourcesResponse(
        documents=[_document_response(doc) for doc in payload["documents"]],
        url_sources=[_url_source_response(source) for source in payload["url_sources"]],
    )


@documents_router.post("/sources/url", response_model=UrlSourceResponse, status_code=201)
def create_url_source_route(
    payload: UrlSourceCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UrlSourceResponse:
    """Create a new URL source and start indexing in the background."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    source, _ = create_url_source(
        client=client,
        url=str(payload.url),
        name=payload.name,
        schedule=payload.schedule,
        exclusions=payload.exclusions,
        db=db,
    )
    if client.openai_api_key:
        background_tasks.add_task(crawl_url_source, source.id, client.openai_api_key)
    return _url_source_response(source)


@documents_router.get("/sources/{source_id}", response_model=UrlSourceDetailResponse)
def get_url_source_route(
    source_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UrlSourceDetailResponse:
    """Return detail, recent crawl history, and indexed pages for one URL source."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    source = get_url_source(source_id, client.id, db)
    docs = (
        db.query(Document)
        .options(selectinload(Document.embeddings))
        .filter(Document.source_id == source.id)
        .order_by(Document.updated_at.desc())
        .limit(50)
        .all()
    )
    recent_runs = sorted(
        source.runs,
        key=lambda run: run.created_at or run.updated_at,
        reverse=True,
    )[:5]
    return UrlSourceDetailResponse(
        **_url_source_response(source).model_dump(),
        recent_runs=[_url_source_run_response(run) for run in recent_runs],
        pages=[
            SourcePageResponse(
                id=doc.id,
                title=doc.filename,
                url=doc.source_url or "",
                chunk_count=len(doc.embeddings),
                updated_at=doc.updated_at,
            )
            for doc in docs
        ],
    )


@documents_router.patch("/sources/{source_id}", response_model=UrlSourceResponse)
def update_url_source_route(
    source_id: uuid.UUID,
    payload: UrlSourceUpdateRequest,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UrlSourceResponse:
    """Update editable URL source settings."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    source = update_url_source(
        source_id=source_id,
        client_id=client.id,
        name=payload.name,
        schedule=payload.schedule,
        exclusions=payload.exclusions,
        db=db,
    )
    return _url_source_response(source)


@documents_router.post("/sources/{source_id}/refresh", response_model=UrlSourceResponse)
def refresh_url_source_route(
    source_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UrlSourceResponse:
    """Trigger an immediate re-crawl for a URL source."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    source = trigger_refresh(source_id=source_id, client=client, db=db)
    if client.openai_api_key:
        background_tasks.add_task(crawl_url_source, source.id, client.openai_api_key)
    return _url_source_response(source)


@documents_router.delete("/sources/{source_id}", status_code=204, response_model=None)
def delete_url_source_route(
    source_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete a URL source and all indexed pages/chunks associated with it."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    delete_url_source(source_id, client.id, db)


@documents_router.delete("/sources/{source_id}/pages/{document_id}", status_code=204, response_model=None)
def delete_source_page_route(
    source_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Delete one indexed page from a URL source and exclude it from future refreshes."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    delete_source_document(
        source_id=source_id,
        document_id=document_id,
        client_id=client.id,
        db=db,
    )


@documents_router.get("/{document_id}/health", response_model=DocumentHealthStatusResponse)
def get_document_health_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentHealthStatusResponse:
    """
    Return stored health_status for a document (does not re-run the check).
    404 if health_status is null.
    """
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    doc = get_document(document_id, client.id, db)
    if doc.health_status is None or not isinstance(doc.health_status, dict):
        raise HTTPException(status_code=404, detail="Health check not yet available")
    hs = doc.health_status
    return DocumentHealthStatusResponse(
        score=hs.get("score"),
        checked_at=str(hs.get("checked_at", "")),
        warnings=list(hs.get("warnings") or []),
        error=hs.get("error"),
    )


@documents_router.post("/{document_id}/health/run", response_model=DocumentHealthStatusResponse)
def run_document_health_check_route(
    document_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_verified_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DocumentHealthStatusResponse:
    """Run health check synchronously and return updated health_status."""
    client = get_client_by_user(current_user.id, db)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    get_document(document_id, client.id, db)
    if not client.openai_api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key not configured. Add your key in dashboard settings.",
        )
    result = run_document_health_check(document_id, db, client.openai_api_key)
    return DocumentHealthStatusResponse(
        score=result.get("score"),
        checked_at=str(result.get("checked_at", "")),
        warnings=list(result.get("warnings") or []),
        error=result.get("error"),
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
        health_status=doc.health_status,
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
