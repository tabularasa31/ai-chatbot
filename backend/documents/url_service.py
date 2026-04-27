"""URL source crawling, extraction, chunking, and management."""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException
from openai import APIError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

import backend.documents.embedder as _embedder_mod
import backend.documents.http_client as _http_client_mod
import backend.documents.sitemap as _sitemap_mod
from backend.core.db import SessionLocal
from backend.documents.constants import KNOWLEDGE_DOCUMENT_CAPACITY
from backend.documents.embedder import ExtractedPage, StructuredSource
from backend.documents.http_client import (
    FETCH_TIMEOUT_SECONDS,
    MAX_HTML_BYTES,
    PREFLIGHT_TIMEOUT_SECONDS,
    FetchContext,
)
from backend.documents.parsers import (
    OpenAPIChunk,
    build_openapi_ingestion_payload_from_spec,
    load_openapi_spec,
    looks_like_openapi,
)
from backend.documents.quick_answers import (
    SUPPORTED_QUICK_ANSWER_KEYS,
    QuickAnswerCandidate,
    merge_quick_answer_candidates,
    scan_html_for_quick_answers,
)
from backend.documents.sitemap import DISCOVERY_ESTIMATE_CAP, MAX_DISCOVERY_DEPTH
from backend.gap_analyzer.jobs import run_mode_a_for_tenant_when_queue_empty_best_effort
from backend.gap_analyzer.repository import invalidate_bm25_cache_for_tenant
from backend.models import (
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    QuickAnswer,
    SourceSchedule,
    SourceStatus,
    Tenant,
    UrlSource,
    UrlSourceRun,
)

logger = logging.getLogger(__name__)

# Defined here (not delegated to http_client) so that test patches on
# url_service._validate_public_hostname reach calls inside this function.
def _normalize_source_url(raw_url: str) -> tuple[str, str]:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid URL starting with https://",
        )
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URLs with credentials are not allowed.")
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            "",
            "",
        )
    )
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    _http_client_mod._validate_public_hostname(hostname)
    return normalized, parsed.netloc.lower()


ALLOWED_SCHEDULES = {
    SourceSchedule.daily.value,
    SourceSchedule.weekly.value,
    SourceSchedule.manual.value,
}
MANUAL_EXCLUDED_PAGE_URLS_KEY = "manually_excluded_page_urls"


@dataclass
class UrlPreflightResult:
    normalized_url: str
    normalized_domain: str
    title: str | None
    estimated_pages: int
    warnings: list[str]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _count_tenant_documents(db: Session, tenant_id: uuid.UUID) -> int:
    return db.query(Document).filter(Document.tenant_id == tenant_id).count()


def _count_source_documents(db: Session, source_id: uuid.UUID) -> int:
    return db.query(Document).filter(Document.source_id == source_id).count()


def _allowed_source_document_total(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
) -> tuple[int, int]:
    total_documents = _count_tenant_documents(db, tenant_id)
    source_documents = _count_source_documents(db, source_id) if source_id else 0
    documents_outside_source = max(0, total_documents - source_documents)
    allowed_total = max(0, KNOWLEDGE_DOCUMENT_CAPACITY - documents_outside_source)
    return allowed_total, max(0, KNOWLEDGE_DOCUMENT_CAPACITY - total_documents)


def _capacity_warning(available_slots: int) -> str:
    if available_slots <= 0:
        return (
            f"Knowledge capacity reached. This tenant already uses all "
            f"{KNOWLEDGE_DOCUMENT_CAPACITY} document slots."
        )
    return (
        f"Knowledge capacity allows only {available_slots} more document"
        f"{'' if available_slots == 1 else 's'} for this tenant."
    )


def _clean_exclusions(exclusions: list[str] | None) -> list[str]:
    if not exclusions:
        return []
    return [item.strip() for item in exclusions if item and item.strip()]


def _clean_schedule(schedule: str | None) -> SourceSchedule:
    raw = (schedule or SourceSchedule.weekly.value).strip().lower()
    if raw not in ALLOWED_SCHEDULES:
        raise HTTPException(status_code=422, detail="Schedule must be daily, weekly, or manual")
    return SourceSchedule(raw)


def _schedule_next_run(schedule: SourceSchedule) -> dt.datetime | None:
    now = _utcnow()
    if schedule == SourceSchedule.manual:
        return None
    if schedule == SourceSchedule.daily:
        return now + dt.timedelta(days=1)
    return now + dt.timedelta(days=7)


def _manual_excluded_page_urls(source: UrlSource) -> list[str]:
    metadata = source.metadata_json or {}
    raw_urls = metadata.get(MANUAL_EXCLUDED_PAGE_URLS_KEY)
    if not isinstance(raw_urls, list):
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_urls:
        if not isinstance(item, str):
            continue
        normalized = _sitemap_mod._normalize_page_url(item, source.normalized_domain)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def _store_manual_excluded_page_urls(source: UrlSource, urls: list[str]) -> None:
    source.metadata_json = {
        **(source.metadata_json or {}),
        MANUAL_EXCLUDED_PAGE_URLS_KEY: urls,
    }


def _exclude_url_from_future_crawls(source: UrlSource, url: str | None) -> None:
    if not url:
        return
    normalized = _sitemap_mod._normalize_page_url(url, source.normalized_domain)
    if not normalized:
        return

    excluded_urls = _manual_excluded_page_urls(source)
    if normalized not in excluded_urls:
        excluded_urls.append(normalized)
    _store_manual_excluded_page_urls(source, excluded_urls)


def _recalculate_source_counts(source: UrlSource, db: Session) -> None:
    docs = (
        db.query(Document)
        .options(selectinload(Document.embeddings))
        .filter(Document.source_id == source.id)
        .all()
    )
    source.pages_indexed = len(docs)
    source.chunks_created = sum(len(doc.embeddings) for doc in docs)


# --- URL discovery (kept here so url_service.* patches work in tests) ---

def _discover_urls(root_url: str, exclusions: list[str], page_cap: int) -> list[str]:
    normalized_root, domain = _normalize_source_url(root_url)
    seen: set[str] = set()
    ordered: list[str] = []

    def add_url(url: str) -> None:
        if url in seen or len(ordered) >= page_cap:
            return
        seen.add(url)
        ordered.append(url)

    add_url(normalized_root)
    for url in _sitemap_mod._apply_exclusions(
        _sitemap_mod._fetch_sitemap_urls(normalized_root, domain), normalized_root, exclusions
    ):
        add_url(url)
        if len(ordered) >= page_cap:
            return ordered

    if len(ordered) >= page_cap:
        return ordered

    queue: deque[tuple[str, int]] = deque([(normalized_root, 0)])
    with _http_client_mod._http_client(PREFLIGHT_TIMEOUT_SECONDS) as client:
        while queue and len(ordered) < page_cap:
            current_url, depth = queue.popleft()
            if depth >= MAX_DISCOVERY_DEPTH:
                continue
            context = FetchContext(stage="crawl:discover", url=current_url)
            try:
                response = _http_client_mod._request_with_safe_redirects(
                    client, "GET", current_url, context=context
                )
            except HTTPException:
                continue
            if response.status_code >= 400 or not _http_client_mod._is_html_like(response):
                _http_client_mod._log_fetch(
                    logging.INFO,
                    "Skipping non-indexable discovery page",
                    context,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                )
                continue
            links = _sitemap_mod._extract_links(response.text, current_url, domain)
            links = _sitemap_mod._apply_exclusions(links, normalized_root, exclusions)
            for link in links:
                if link in seen:
                    continue
                add_url(link)
                queue.append((link, depth + 1))
                if len(ordered) >= page_cap:
                    break
    return ordered


# --- OpenAPI structured source (kept here so url_service._http_client patches work in tests) ---

def _fetch_openapi_source(url: str) -> StructuredSource | None:
    context = FetchContext(stage="crawl:structured", url=url)
    with _http_client_mod._http_client(FETCH_TIMEOUT_SECONDS) as client:
        try:
            response = _http_client_mod._request_with_safe_redirects(
                client, "GET", url, context=context
            )
            _http_client_mod._raise_for_upstream_status(response, context)
        except HTTPException as exc:
            _http_client_mod._log_fetch(
                logging.INFO, "Skipping structured source after fetch failure", context, detail=exc.detail
            )
            return None

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type:
        return None

    body = response.content
    if len(body) > MAX_HTML_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Response is too large. Maximum size is {MAX_HTML_BYTES // (1024 * 1024)}MB.",
        )
    if not body.strip():
        return None

    try:
        spec, source_format = load_openapi_spec(body)
    except ValueError:
        return None

    if not looks_like_openapi(spec):
        raise HTTPException(
            status_code=422,
            detail="The URL returned structured JSON/YAML content, but it is not an OpenAPI/Swagger spec.",
        )

    try:
        parsed_text, chunks, parsed_source_format, _ = build_openapi_ingestion_payload_from_spec(
            spec,
            source_format,
        )
    except ValueError as exc:
        logger.info("Structured OpenAPI validation failed for %s: %s", url, exc)
        raise HTTPException(
            status_code=422,
            detail="The URL looks like an OpenAPI document, but it could not be validated.",
        ) from exc

    info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
    title = "Unknown API"
    if isinstance(info, dict):
        title = (info.get("title") or "").strip() or title
    return StructuredSource(
        title=title[:255],
        parsed_text=parsed_text,
        chunks=chunks,
        source_format=parsed_source_format or source_format,
    )


# --- DB upsert (kept here so url_service._embed_chunks patches work in tests) ---

def _upsert_page_document(
    *,
    source: UrlSource,
    page: ExtractedPage,
    db: Session,
    api_key: str | None,
) -> tuple[Document, int]:
    existing = (
        db.query(Document)
        .options(selectinload(Document.embeddings))
        .filter(Document.source_id == source.id)
        .filter(Document.source_url == page.url)
        .first()
    )
    content_hash = _embedder_mod._content_hash(page.text)
    if existing and _embedder_mod._content_hash(existing.parsed_text or "") == content_hash:
        existing.filename = page.title[:255]
        existing.status = DocumentStatus.ready
        existing.file_type = DocumentType.url
        if _embedder_mod._url_knowledge_extract_when_unchanged():
            _embedder_mod._run_tenant_knowledge_extraction_best_effort(
                document_id=existing.id,
                api_key=api_key,
            )
        return existing, len(existing.embeddings)

    if existing:
        db.query(Embedding).filter(Embedding.document_id == existing.id).delete()
        doc = existing
    else:
        doc = Document(
            tenant_id=source.tenant_id,
            source_id=source.id,
            filename=page.title[:255],
            file_type=DocumentType.url,
            status=DocumentStatus.processing,
            source_url=page.url,
        )
        db.add(doc)
        db.flush()

    doc.filename = page.title[:255]
    doc.source_url = page.url
    doc.file_type = DocumentType.url
    doc.parsed_text = page.text
    doc.status = DocumentStatus.embedding
    db.flush()

    vectors = _embedder_mod._embed_chunks(page.chunks, api_key)
    for chunk, vector in zip(page.chunks, vectors, strict=True):
        db.add(
            Embedding(
                document_id=doc.id,
                chunk_text=chunk["chunk_text"],
                vector=vector,
                metadata_json={
                    "chunk_index": chunk["chunk_index"],
                    "filename": doc.filename,
                    "file_type": doc.file_type.value,
                    "source_url": page.url,
                    "page_title": page.title,
                    "section_title": chunk["section_title"],
                    "token_count": chunk["token_count"],
                    "content_hash": chunk["content_hash"],
                    "page_content_hash": content_hash,
                    "raw_text": chunk["raw_text"],
                },
            )
        )
    doc.status = DocumentStatus.ready
    db.flush()
    db.commit()
    invalidate_bm25_cache_for_tenant(source.tenant_id)
    _embedder_mod._run_tenant_knowledge_extraction_best_effort(
        document_id=doc.id,
        api_key=api_key,
    )
    return doc, len(page.chunks)


def _upsert_structured_document(
    *,
    source: UrlSource,
    url: str,
    title: str,
    parsed_text: str,
    chunks: list[OpenAPIChunk],
    db: Session,
    api_key: str | None,
) -> tuple[Document, int]:
    existing = (
        db.query(Document)
        .options(selectinload(Document.embeddings))
        .filter(Document.source_id == source.id)
        .filter(Document.source_url == url)
        .first()
    )
    content_hash = _embedder_mod._content_hash(parsed_text)
    if existing and _embedder_mod._content_hash(existing.parsed_text or "") == content_hash:
        existing.filename = title[:255]
        existing.status = DocumentStatus.ready
        existing.file_type = DocumentType.swagger
        if _embedder_mod._url_knowledge_extract_when_unchanged():
            _embedder_mod._run_tenant_knowledge_extraction_best_effort(
                document_id=existing.id,
                api_key=api_key,
            )
        return existing, len(existing.embeddings)

    if existing:
        db.query(Embedding).filter(Embedding.document_id == existing.id).delete()
        doc = existing
    else:
        doc = Document(
            tenant_id=source.tenant_id,
            source_id=source.id,
            filename=title[:255],
            file_type=DocumentType.swagger,
            status=DocumentStatus.processing,
            source_url=url,
        )
        db.add(doc)
        db.flush()

    doc.filename = title[:255]
    doc.source_url = url
    doc.file_type = DocumentType.swagger
    doc.parsed_text = parsed_text
    doc.status = DocumentStatus.embedding
    db.flush()

    rendered_chunks = _embedder_mod._render_structured_openapi_chunks(
        chunks,
        title=title,
        source_url=url,
        source_format=chunks[0].source_format if chunks else "yaml",
    )
    try:
        vectors = _embedder_mod._embed_chunks(rendered_chunks, api_key)
        for chunk, vector in zip(rendered_chunks, vectors, strict=True):
            db.add(
                Embedding(
                    document_id=doc.id,
                    chunk_text=chunk["chunk_text"],
                    vector=vector,
                    metadata_json={
                        "chunk_index": chunk["chunk_index"],
                        "filename": doc.filename,
                        "file_type": doc.file_type.value,
                        "source_url": url,
                        "source_kind": "url",
                        "source_format": chunk.get("source_format"),
                        "type": chunk.get("type"),
                        "subtype": chunk.get("subtype"),
                        "path": chunk.get("path"),
                        "method": chunk.get("method"),
                        "operation_id": chunk.get("operation_id"),
                        "tags": chunk.get("tags"),
                        "deprecated": chunk.get("deprecated"),
                        "content_types": chunk.get("content_types"),
                        "response_codes": chunk.get("response_codes"),
                        "auth_schemes": chunk.get("auth_schemes"),
                        "has_examples": chunk.get("has_examples"),
                        "spec_version": chunk.get("spec_version"),
                        "page_content_hash": content_hash,
                    },
                )
            )
        doc.status = DocumentStatus.ready
        db.flush()
        db.commit()
        invalidate_bm25_cache_for_tenant(source.tenant_id)
        _embedder_mod._run_tenant_knowledge_extraction_best_effort(
            document_id=doc.id,
            api_key=api_key,
        )
        return doc, len(rendered_chunks)
    except (APIError, SQLAlchemyError, ValueError) as exc:
        logger.warning("Structured source embedding failed", extra={"url": url, "error": str(exc)})
        db.rollback()
        refreshed_doc = db.query(Document).filter(Document.id == doc.id).first()
        if refreshed_doc is not None:
            refreshed_doc.status = DocumentStatus.error
            refreshed_doc.parsed_text = parsed_text
            db.add(refreshed_doc)
            db.commit()
        raise HTTPException(status_code=500, detail="Structured source embedding failed") from None


# --- Public CRUD API ---

def preflight_url_source(
    *,
    tenant: Tenant,
    url: str,
    exclusions: list[str],
    db: Session,
) -> UrlPreflightResult:
    normalized_url, normalized_domain = _normalize_source_url(url)
    _, remaining_capacity = _allowed_source_document_total(db, tenant_id=tenant.id)
    if remaining_capacity <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Knowledge capacity reached. This tenant already uses all "
                f"{KNOWLEDGE_DOCUMENT_CAPACITY} document slots."
            ),
        )
    duplicate = (
        db.query(UrlSource)
        .filter(UrlSource.tenant_id == tenant.id)
        .filter(UrlSource.normalized_domain == normalized_domain)
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="You already have a source from this domain. Manage it in the sources list.",
        )

    _, title = _http_client_mod._fetch_reachable_page(normalized_url, PREFLIGHT_TIMEOUT_SECONDS)
    warnings: list[str] = []
    robots_warning = _sitemap_mod._load_robots_warning(normalized_url)
    if robots_warning:
        warnings.append(robots_warning)

    estimate_urls = _discover_urls(normalized_url, exclusions, DISCOVERY_ESTIMATE_CAP)
    estimated_pages = len(estimate_urls)
    if estimated_pages > remaining_capacity:
        warnings.append(_capacity_warning(remaining_capacity))

    return UrlPreflightResult(
        normalized_url=normalized_url,
        normalized_domain=normalized_domain,
        title=title,
        estimated_pages=estimated_pages,
        warnings=warnings,
    )


def create_url_source(
    *,
    tenant: Tenant,
    url: str,
    name: str | None,
    schedule: str | None,
    exclusions: list[str] | None,
    db: Session,
) -> tuple[UrlSource, UrlPreflightResult]:
    cleaned_exclusions = _clean_exclusions(exclusions)
    preflight = preflight_url_source(tenant=tenant, url=url, exclusions=cleaned_exclusions, db=db)
    cleaned_schedule = _clean_schedule(schedule)
    source = UrlSource(
        tenant_id=tenant.id,
        name=(name or preflight.title or preflight.normalized_domain).strip()[:255],
        url=preflight.normalized_url,
        normalized_domain=preflight.normalized_domain,
        status=SourceStatus.paused if not tenant.openai_api_key else SourceStatus.queued,
        crawl_schedule=cleaned_schedule,
        exclusion_patterns=cleaned_exclusions or None,
        pages_found=preflight.estimated_pages,
        warning_message="\n".join(preflight.warnings) if preflight.warnings else None,
        next_crawl_at=_schedule_next_run(cleaned_schedule),
        metadata_json={"auto_title": preflight.title},
    )
    if not tenant.openai_api_key:
        source.error_message = "Indexing paused — check your OpenAI key."
    db.add(source)
    db.commit()
    db.refresh(source)
    return source, preflight


def list_knowledge_sources(tenant_id: uuid.UUID, db: Session) -> dict[str, Any]:
    files = (
        db.query(Document)
        .filter(Document.tenant_id == tenant_id)
        .filter(Document.source_id.is_(None))
        .order_by(Document.created_at.desc())
        .all()
    )
    url_sources = (
        db.query(UrlSource)
        .filter(UrlSource.tenant_id == tenant_id)
        .order_by(UrlSource.created_at.desc())
        .all()
    )
    return {"documents": files, "url_sources": url_sources}


def get_url_source(source_id: uuid.UUID, tenant_id: uuid.UUID, db: Session) -> UrlSource:
    source = (
        db.query(UrlSource)
        .options(
            selectinload(UrlSource.runs),
            selectinload(UrlSource.documents),
            selectinload(UrlSource.quick_answers),
        )
        .filter(UrlSource.id == source_id)
        .filter(UrlSource.tenant_id == tenant_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


def update_url_source(
    *,
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    name: str | None,
    schedule: str | None,
    exclusions: list[str] | None,
    db: Session,
) -> UrlSource:
    source = get_url_source(source_id, tenant_id, db)
    if name is not None:
        source.name = name.strip()[:255] or None
    if schedule is not None:
        source.crawl_schedule = _clean_schedule(schedule)
        source.next_crawl_at = _schedule_next_run(source.crawl_schedule)
    if exclusions is not None:
        source.exclusion_patterns = _clean_exclusions(exclusions) or None
    db.commit()
    db.refresh(source)
    return source


def delete_url_source(source_id: uuid.UUID, tenant_id: uuid.UUID, db: Session) -> None:
    source = get_url_source(source_id, tenant_id, db)
    db.delete(source)
    db.commit()
    invalidate_bm25_cache_for_tenant(tenant_id)


def delete_source_document(
    *,
    source_id: uuid.UUID,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> None:
    source = get_url_source(source_id, tenant_id, db)
    doc = (
        db.query(Document)
        .options(selectinload(Document.embeddings))
        .filter(Document.id == document_id)
        .first()
    )
    if not doc or doc.tenant_id != tenant_id or doc.source_id != source.id:
        raise HTTPException(status_code=404, detail="Page not found")

    _exclude_url_from_future_crawls(source, doc.source_url)
    db.delete(doc)
    db.flush()
    _recalculate_source_counts(source, db)
    db.commit()
    invalidate_bm25_cache_for_tenant(tenant_id)


def trigger_refresh(
    *,
    source_id: uuid.UUID,
    tenant: Tenant,
    db: Session,
) -> UrlSource:
    source = get_url_source(source_id, tenant.id, db)
    now = _utcnow()
    last_refresh = source.last_refresh_requested_at
    if last_refresh is not None and last_refresh.tzinfo is None:
        last_refresh = last_refresh.replace(tzinfo=dt.UTC)
    if last_refresh and now - last_refresh < dt.timedelta(hours=1):
        remaining = dt.timedelta(hours=1) - (now - last_refresh)
        minutes = max(1, int(remaining.total_seconds() // 60))
        raise HTTPException(
            status_code=429,
            detail=f"Refresh available in {minutes} min.",
        )
    source.last_refresh_requested_at = now
    source.status = SourceStatus.paused if not tenant.openai_api_key else SourceStatus.queued
    if not tenant.openai_api_key:
        source.error_message = "Indexing paused — check your OpenAI key."
    else:
        source.error_message = None
        source.warning_message = None
    db.commit()
    db.refresh(source)
    return source


# --- Crawl internals ---

def _mark_run_finished(run: UrlSourceRun, *, status: str, error_message: str | None = None) -> None:
    run.status = status
    run.error_message = error_message
    run.finished_at = _utcnow()
    if run.created_at:
        started = run.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=dt.UTC)
        run.duration_seconds = max(0, int((run.finished_at - started).total_seconds()))


class _CrawlAbortedError(Exception):
    """Raised when the crawl loop terminates early (e.g. bad OpenAI key)."""


@dataclass
class _CrawlPlan:
    """Result of URL discovery: what to crawl and capacity constraints."""
    urls: list[str]
    discovered_urls: list[str]
    remaining_capacity: int


@dataclass
class _CrawlResult:
    """Outcome of the indexing loop."""
    indexed_urls: set[str]
    failures: list[dict[str, str]]
    chunks_created: int
    quick_answers: dict[str, QuickAnswerCandidate] = field(default_factory=dict)


def _plan_crawl(source: UrlSource, db: Session) -> _CrawlPlan:
    """Discover URLs and compute which ones to crawl."""
    existing_docs = db.query(Document).filter(Document.source_id == source.id).all()
    existing_urls = {doc.source_url for doc in existing_docs if doc.source_url}
    allowed_total, remaining_capacity = _allowed_source_document_total(
        db,
        tenant_id=source.tenant_id,
        source_id=source.id,
    )
    discovered_urls = _discover_urls(
        source.url, _clean_exclusions(source.exclusion_patterns), DISCOVERY_ESTIMATE_CAP
    )
    manually_excluded_urls = set(_manual_excluded_page_urls(source))
    discovered_urls = [url for url in discovered_urls if url not in manually_excluded_urls]
    prioritized_existing_urls = [url for url in discovered_urls if url in existing_urls]
    new_urls = [url for url in discovered_urls if url not in existing_urls]
    urls = prioritized_existing_urls + new_urls[: max(0, allowed_total - len(prioritized_existing_urls))]
    return _CrawlPlan(urls=urls, discovered_urls=discovered_urls, remaining_capacity=remaining_capacity)


def _index_pages(
    source: UrlSource,
    run: UrlSourceRun,
    plan: _CrawlPlan,
    api_key: str,
    db: Session,
) -> _CrawlResult:
    """Fetch, extract, and index each URL.

    Returns a _CrawlResult.
    Raises _CrawlAbortedError if the crawl must stop early — currently triggered by
    HTTPException with status {400, 401, 500} from _upsert_page_document (covers
    OpenAI auth failures and upstream embedding errors, but not exclusively).
    TODO: narrow to explicit OpenAI/auth/embedding error recognition rather than
    status-code matching alone.
    """
    structured_source = _fetch_openapi_source(source.url)
    if structured_source is not None:
        quick_answers = {
            "documentation_url": QuickAnswerCandidate(
                key="documentation_url",
                value=source.url,
                source_url=source.url,
                score=100,
                metadata={"method": "source_url"},
            )
        }
        _, chunk_count = _upsert_structured_document(
            source=source,
            url=source.url,
            title=structured_source.title,
            parsed_text=structured_source.parsed_text,
            chunks=structured_source.chunks,
            db=db,
            api_key=api_key,
        )
        source.metadata_json = {
            **(source.metadata_json or {}),
            "platform": "openapi",
            "limit_reached": False,
            "source_kind": "url",
            "source_format": _embedder_mod._normalize_source_format(
                structured_source.source_format, from_url=True
            ),
        }
        return _CrawlResult(
            indexed_urls={source.url},
            failures=[],
            chunks_created=chunk_count,
            quick_answers=quick_answers,
        )

    source.metadata_json = {
        **(source.metadata_json or {}),
        "limit_reached": False,
    }

    indexed_urls: set[str] = set()
    failures: list[dict[str, str]] = []
    chunks_created = 0
    quick_answers = {
        "documentation_url": QuickAnswerCandidate(
            key="documentation_url",
            value=source.url,
            source_url=source.url,
            score=10,
            metadata={"method": "source_url"},
        )
    }

    for url in plan.urls:
        html = _http_client_mod._fetch_page_html(url)
        if not html:
            failures.append({"url": url, "reason": "Could not fetch HTML"})
            continue
        quick_answers = merge_quick_answer_candidates(
            quick_answers,
            scan_html_for_quick_answers(
                html=html,
                page_url=url,
                root_url=source.url,
            ),
        )
        page = _embedder_mod._extract_page(url, html)
        if not page:
            failures.append({"url": url, "reason": "No readable content extracted"})
            continue
        try:
            _, page_chunks = _upsert_page_document(source=source, page=page, db=db, api_key=api_key)
        except HTTPException as exc:
            if exc.status_code in {400, 401, 500}:
                source.status = SourceStatus.paused
                source.error_message = "Indexing paused — check your OpenAI key."
                _mark_run_finished(run, status=SourceStatus.paused.value, error_message=source.error_message)
                db.commit()
                raise _CrawlAbortedError from None
            raise
        indexed_urls.add(url)
        chunks_created += page_chunks
        source.pages_indexed = len(indexed_urls)
        source.chunks_created = chunks_created
        if source.pages_indexed and source.pages_indexed % 5 == 0:
            db.commit()

    return _CrawlResult(
        indexed_urls=indexed_urls,
        failures=failures,
        chunks_created=chunks_created,
        quick_answers=quick_answers,
    )


def _replace_quick_answers_for_source(
    *,
    source: UrlSource,
    quick_answers: dict[str, QuickAnswerCandidate],
    db: Session,
) -> None:
    db.query(QuickAnswer).filter(QuickAnswer.source_id == source.id).delete(synchronize_session=False)
    for candidate in quick_answers.values():
        if candidate.key not in SUPPORTED_QUICK_ANSWER_KEYS:
            continue
        db.add(
            QuickAnswer(
                tenant_id=source.tenant_id,
                source_id=source.id,
                key=candidate.key,
                value=candidate.value,
                source_url=candidate.source_url,
                metadata_json=candidate.metadata,
                detected_at=_utcnow(),
            )
        )


def _finalize_crawl(
    source: UrlSource,
    run: UrlSourceRun,
    plan: _CrawlPlan,
    result: _CrawlResult,
    started: float,
    db: Session,
) -> None:
    """Remove stale documents, update source/run status, and commit."""
    stale_docs = (
        db.query(Document)
        .filter(Document.source_id == source.id)
        .filter(Document.source_url.isnot(None))
        .all()
    )
    for doc in stale_docs:
        if doc.source_url and doc.source_url not in result.indexed_urls:
            db.delete(doc)

    failure_ratio = (len(result.failures) / len(plan.urls)) if plan.urls else 0.0
    source.last_crawled_at = _utcnow()
    source.next_crawl_at = _schedule_next_run(source.crawl_schedule)
    source.pages_found = len(plan.urls)
    source.pages_indexed = len(result.indexed_urls)
    source.chunks_created = result.chunks_created
    source.warning_message = None
    source.error_message = None
    if len(plan.discovered_urls) > len(plan.urls):
        source.warning_message = (
            f"Knowledge capacity reached. Indexed {len(plan.urls)} of about {len(plan.discovered_urls)} discovered pages."
        )
        source.metadata_json = {
            **(source.metadata_json or {}),
            "limit_reached": True,
            "capacity_limited": True,
            "remaining_capacity": plan.remaining_capacity,
        }
    else:
        source.metadata_json = {
            **(source.metadata_json or {}),
            "limit_reached": False,
            "capacity_limited": False,
            "remaining_capacity": plan.remaining_capacity,
        }

    if failure_ratio > 0.3:
        source.status = SourceStatus.error
        source.error_message = _summarize_crawl_failure(result.failures)
        _mark_run_finished(run, status=SourceStatus.error.value, error_message=source.error_message)
    else:
        _replace_quick_answers_for_source(source=source, quick_answers=result.quick_answers, db=db)
        source.status = SourceStatus.ready
        _mark_run_finished(run, status=SourceStatus.ready.value)

    run.pages_indexed = len(result.indexed_urls)
    run.failed_urls = result.failures
    run.duration_seconds = max(0, int(time.monotonic() - started))
    db.commit()


def _summarize_crawl_failure(failures: list[dict[str, str]]) -> str:
    reason_counts: dict[str, int] = {}
    for failure in failures:
        reason = (failure.get("reason") or "").strip()
        if not reason:
            continue
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    if not reason_counts:
        return "Indexing failed — most pages could not be indexed."

    dominant_reason = max(reason_counts.items(), key=lambda item: item[1])[0]
    if dominant_reason == "Could not fetch HTML":
        return "Indexing failed — most pages could not be fetched or returned an unsupported format."
    if dominant_reason == "No readable content extracted":
        return "Indexing failed — most pages did not contain readable content."
    return "Indexing failed — most pages could not be indexed."


def crawl_url_source(source_id: uuid.UUID, api_key: str | None) -> None:
    db = SessionLocal()
    try:
        source = (
            db.query(UrlSource)
            .options(selectinload(UrlSource.documents))
            .filter(UrlSource.id == source_id)
            .first()
        )
        if not source:
            return
        if not api_key:
            source.status = SourceStatus.paused
            source.error_message = "Indexing paused — check your OpenAI key."
            db.commit()
            return

        source.status = SourceStatus.indexing
        source.error_message = None
        source.warning_message = None
        run = UrlSourceRun(source_id=source.id, status=SourceStatus.indexing.value, failed_urls=[])
        db.add(run)
        db.commit()
        db.refresh(source)
        db.refresh(run)

        started = time.monotonic()
        plan = _plan_crawl(source, db)
        source.pages_found = len(plan.discovered_urls)
        run.pages_found = len(plan.discovered_urls)
        db.commit()

        try:
            result = _index_pages(source, run, plan, api_key, db)
        except _CrawlAbortedError:
            return

        _finalize_crawl(source, run, plan, result, started, db)
        if source.status == SourceStatus.ready:
            try:
                run_mode_a_for_tenant_when_queue_empty_best_effort(source.tenant_id)
            except Exception:
                logger.warning(
                    "Gap Analyzer Mode A trigger failed for source_id=%s tenant_id=%s",
                    source.id,
                    source.tenant_id,
                    exc_info=True,
                )
    except HTTPException as exc:
        logger.warning("URL crawl rejected for source %s: %s", source_id, exc.detail)
        source = db.query(UrlSource).filter(UrlSource.id == source_id).first()
        if source:
            source.status = SourceStatus.error
            source.error_message = str(exc.detail)
            run = (
                db.query(UrlSourceRun)
                .filter(UrlSourceRun.source_id == source_id)
                .order_by(UrlSourceRun.created_at.desc())
                .first()
            )
            if run and run.finished_at is None:
                _mark_run_finished(run, status=SourceStatus.error.value, error_message=str(exc.detail))
            db.commit()
    except Exception as exc:
        logger.exception("URL crawl failed for source %s", source_id)
        source = db.query(UrlSource).filter(UrlSource.id == source_id).first()
        if source:
            source.status = SourceStatus.error
            source.error_message = "Indexing failed — unexpected crawler error."
            run = (
                db.query(UrlSourceRun)
                .filter(UrlSourceRun.source_id == source_id)
                .order_by(UrlSourceRun.created_at.desc())
                .first()
            )
            if run and run.finished_at is None:
                _mark_run_finished(run, status=SourceStatus.error.value, error_message=str(exc))
            db.commit()
    finally:
        db.close()
