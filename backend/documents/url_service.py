"""URL source crawling, extraction, chunking, and management."""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import ipaddress
import logging
import math
import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from backend.core.db import SessionLocal
from backend.core.openai_client import get_openai_client
from backend.documents.constants import KNOWLEDGE_DOCUMENT_CAPACITY
from backend.models import (
    Client,
    Document,
    DocumentStatus,
    DocumentType,
    Embedding,
    SourceSchedule,
    SourceStatus,
    UrlSource,
    UrlSourceRun,
)

logger = logging.getLogger(__name__)

MAX_DISCOVERY_DEPTH = 3
DISCOVERY_ESTIMATE_CAP = 200
EMBED_BATCH_SIZE = 100
FETCH_TIMEOUT_SECONDS = 10.0
PREFLIGHT_TIMEOUT_SECONDS = 5.0
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "Chat9Bot/1.0 (+https://getchat9.live)"
ALLOWED_SCHEDULES = {
    SourceSchedule.daily.value,
    SourceSchedule.weekly.value,
    SourceSchedule.manual.value,
}
_SECTION_SPLIT_RE = re.compile(r"(?<=[.?!])\s+|\n{2,}")
MANUAL_EXCLUDED_PAGE_URLS_KEY = "manually_excluded_page_urls"


@dataclass
class UrlPreflightResult:
    normalized_url: str
    normalized_domain: str
    title: str | None
    estimated_pages: int
    warnings: list[str]


@dataclass
class ExtractedPage:
    url: str
    title: str
    text: str
    chunks: list[dict[str, Any]]


@dataclass
class FetchContext:
    stage: str
    url: str


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


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
    _validate_public_hostname(hostname)
    return normalized, parsed.netloc.lower()


def _count_client_documents(db: Session, client_id: uuid.UUID) -> int:
    return db.query(Document).filter(Document.client_id == client_id).count()


def _count_source_documents(db: Session, source_id: uuid.UUID) -> int:
    return db.query(Document).filter(Document.source_id == source_id).count()


def _allowed_source_document_total(
    db: Session,
    *,
    client_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
) -> tuple[int, int]:
    total_documents = _count_client_documents(db, client_id)
    source_documents = _count_source_documents(db, source_id) if source_id else 0
    documents_outside_source = max(0, total_documents - source_documents)
    allowed_total = max(0, KNOWLEDGE_DOCUMENT_CAPACITY - documents_outside_source)
    return allowed_total, max(0, KNOWLEDGE_DOCUMENT_CAPACITY - total_documents)


def _capacity_warning(available_slots: int) -> str:
    if available_slots <= 0:
        return (
            f"Knowledge capacity reached. This client already uses all "
            f"{KNOWLEDGE_DOCUMENT_CAPACITY} document slots."
        )
    return (
        f"Knowledge capacity allows only {available_slots} more document"
        f"{'' if available_slots == 1 else 's'} for this client."
    )


def _normalize_page_url(url: str, base_domain: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.netloc.lower() != base_domain.lower():
        return None
    path = parsed.path or "/"
    if path.endswith("/") and path != "/":
        path = path[:-1]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def _http_client(timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )


def _log_fetch(level: int, message: str, context: FetchContext, **extra: Any) -> None:
    logger.log(level, "%s [%s] %s", message, context.stage, context.url, extra=extra)


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def _resolve_hostname(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=400,
            detail="Couldn't resolve this URL. Check the address and try again.",
        ) from exc

    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for family, _, _, _, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            resolved.add(ipaddress.ip_address(sockaddr[0]))
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail="Couldn't resolve this URL. Check the address and try again.",
        )
    return resolved


def _validate_public_hostname(hostname: str) -> None:
    if not hostname:
        raise HTTPException(status_code=400, detail="Please enter a valid public URL.")

    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        candidates = _resolve_hostname(hostname)
    else:
        candidates = {parsed_ip}

    if any(_is_forbidden_ip(candidate) for candidate in candidates):
        raise HTTPException(
            status_code=400,
            detail="Private, local, and reserved network addresses are not allowed.",
        )


def _request_with_safe_redirects(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    context: FetchContext,
    max_redirects: int = MAX_REDIRECTS,
) -> httpx.Response:
    current_url = url
    for _ in range(max_redirects + 1):
        parsed = urlparse(current_url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        _validate_public_hostname(hostname)
        try:
            response = client.request(method, current_url)
        except httpx.TimeoutException as exc:
            _log_fetch(logging.WARNING, "Request timed out", context, method=method)
            raise HTTPException(
                status_code=400,
                detail="Couldn't reach this URL. Check the address and try again.",
            ) from exc
        except httpx.HTTPError as exc:
            _log_fetch(logging.WARNING, "Request failed", context, method=method, error=str(exc))
            raise HTTPException(
                status_code=400,
                detail="Couldn't reach this URL. Check the address and try again.",
            ) from exc

        if response.status_code not in {301, 302, 303, 307, 308}:
            _enforce_response_size_limit(response, context)
            return response

        location = response.headers.get("location")
        if not location:
            _log_fetch(logging.WARNING, "Redirect missing location header", context, method=method)
            raise HTTPException(status_code=400, detail="URL redirect is missing a target.")
        current_url = urljoin(current_url, location)
        _log_fetch(logging.INFO, "Following validated redirect", context, method=method, location=current_url)

    raise HTTPException(status_code=400, detail="Too many redirects while fetching this URL.")


def _enforce_response_size_limit(response: httpx.Response, context: FetchContext) -> None:
    content_length = response.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_HTML_BYTES:
                _log_fetch(
                    logging.WARNING,
                    "Response rejected by content-length",
                    context,
                    status_code=response.status_code,
                    content_length=content_length,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Response is too large. Maximum size is {MAX_HTML_BYTES // (1024 * 1024)}MB.",
                )
        except ValueError:
            pass

    if len(response.content) > MAX_HTML_BYTES:
        _log_fetch(
            logging.WARNING,
            "Response rejected by downloaded size",
            context,
            status_code=response.status_code,
            size=len(response.content),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Response is too large. Maximum size is {MAX_HTML_BYTES // (1024 * 1024)}MB.",
        )


def _raise_for_upstream_status(response: httpx.Response, context: FetchContext) -> None:
    status_code = response.status_code
    if status_code < 400:
        return

    _log_fetch(logging.WARNING, "Upstream returned error status", context, status_code=status_code)
    if status_code == 404:
        raise HTTPException(status_code=404, detail="The URL returned 404 Not Found.")
    if status_code in {401, 403}:
        raise HTTPException(status_code=400, detail="The URL requires access and can't be indexed.")
    if 400 <= status_code < 500:
        raise HTTPException(
            status_code=400,
            detail=f"The URL returned HTTP {status_code}. Check the address and try again.",
        )
    raise HTTPException(
        status_code=502,
        detail=f"The website is temporarily unavailable (HTTP {status_code}). Try again later.",
    )


def _load_robots_warning(root_url: str) -> str | None:
    parsed = urlparse(root_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    rp = robotparser.RobotFileParser()
    context = FetchContext(stage="preflight:robots", url=robots_url)
    with _http_client(PREFLIGHT_TIMEOUT_SECONDS) as client:
        try:
            response = _request_with_safe_redirects(client, "GET", robots_url, context=context)
        except HTTPException:
            return None
    if response.status_code >= 400 or not response.text.strip():
        return None
    try:
        rp.parse(response.text.splitlines())
    except Exception:
        return None
    if not rp.can_fetch(USER_AGENT, root_url):
        return "robots.txt restricts crawling. We'll index only what is allowed."
    return None


def _fetch_reachable_page(url: str, timeout_seconds: float) -> tuple[str, str | None]:
    context = FetchContext(stage="preflight:page", url=url)
    with _http_client(timeout_seconds) as client:
        response = _request_with_safe_redirects(client, "HEAD", url, context=context)
        if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", ""):
            response = _request_with_safe_redirects(client, "GET", url, context=context)

        _raise_for_upstream_status(response, context)

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and response.request.method != "GET":
            response = _request_with_safe_redirects(client, "GET", url, context=context)
            _raise_for_upstream_status(response, context)
            content_type = response.headers.get("content-type", "")

        text = response.text
        title = None
        if "text/html" in content_type:
            soup = BeautifulSoup(text, "html.parser")
            title_tag = soup.find("title")
            title = title_tag.get_text(" ", strip=True) if title_tag else None

        return text, title


def _is_html_like(response: httpx.Response) -> bool:
    return "text/html" in response.headers.get("content-type", "")


def _extract_links(html: str, current_url: str, domain: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href") or ""
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        joined = urljoin(current_url, href)
        normalized = _normalize_page_url(joined, domain)
        if normalized:
            out.append(normalized)
    return out


def _fetch_sitemap_urls(root_url: str, domain: str) -> list[str]:
    candidates = [urljoin(root_url, "/sitemap.xml"), urljoin(root_url, "/sitemap_index.xml")]
    urls: list[str] = []
    with _http_client(PREFLIGHT_TIMEOUT_SECONDS) as client:
        for candidate in candidates:
            context = FetchContext(stage="preflight:sitemap", url=candidate)
            try:
                response = _request_with_safe_redirects(client, "GET", candidate, context=context)
            except HTTPException:
                continue
            if response.status_code >= 400 or not response.text.strip():
                _log_fetch(
                    logging.INFO,
                    "Skipping sitemap candidate",
                    context,
                    status_code=response.status_code,
                )
                continue
            try:
                root = ET.fromstring(response.text)
            except ET.ParseError:
                _log_fetch(logging.INFO, "Skipping invalid sitemap XML", context)
                continue
            for loc in root.findall(".//{*}loc"):
                if loc.text:
                    normalized = _normalize_page_url(loc.text.strip(), domain)
                    if normalized:
                        urls.append(normalized)
    return urls


def _apply_exclusions(urls: list[str], root_url: str, exclusions: list[str]) -> list[str]:
    if not exclusions:
        return urls
    filtered: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if any(fnmatch.fnmatch(path, pattern.strip()) for pattern in exclusions if pattern.strip()):
            continue
        filtered.append(url)
    return filtered


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
    for url in _apply_exclusions(_fetch_sitemap_urls(normalized_root, domain), normalized_root, exclusions):
        add_url(url)
        if len(ordered) >= page_cap:
            return ordered

    if len(ordered) >= page_cap:
        return ordered

    queue: deque[tuple[str, int]] = deque([(normalized_root, 0)])
    with _http_client(PREFLIGHT_TIMEOUT_SECONDS) as client:
        while queue and len(ordered) < page_cap:
            current_url, depth = queue.popleft()
            if depth >= MAX_DISCOVERY_DEPTH:
                continue
            context = FetchContext(stage="crawl:discover", url=current_url)
            try:
                response = _request_with_safe_redirects(client, "GET", current_url, context=context)
            except HTTPException:
                continue
            if response.status_code >= 400 or not _is_html_like(response):
                _log_fetch(
                    logging.INFO,
                    "Skipping non-indexable discovery page",
                    context,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                )
                continue
            links = _extract_links(response.text, current_url, domain)
            links = _apply_exclusions(links, normalized_root, exclusions)
            for link in links:
                if link in seen:
                    continue
                add_url(link)
                queue.append((link, depth + 1))
                if len(ordered) >= page_cap:
                    break
    return ordered


def preflight_url_source(
    *,
    client: Client,
    url: str,
    exclusions: list[str],
    db: Session,
) -> UrlPreflightResult:
    normalized_url, normalized_domain = _normalize_source_url(url)
    _, remaining_capacity = _allowed_source_document_total(db, client_id=client.id)
    if remaining_capacity <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Knowledge capacity reached. This client already uses all "
                f"{KNOWLEDGE_DOCUMENT_CAPACITY} document slots."
            ),
        )
    duplicate = (
        db.query(UrlSource)
        .filter(UrlSource.client_id == client.id)
        .filter(UrlSource.normalized_domain == normalized_domain)
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="You already have a source from this domain. Manage it in the sources list.",
        )

    _, title = _fetch_reachable_page(normalized_url, PREFLIGHT_TIMEOUT_SECONDS)
    warnings: list[str] = []
    robots_warning = _load_robots_warning(normalized_url)
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


def _schedule_next_run(schedule: SourceSchedule) -> dt.datetime | None:
    now = _utcnow()
    if schedule == SourceSchedule.manual:
        return None
    if schedule == SourceSchedule.daily:
        return now + dt.timedelta(days=1)
    return now + dt.timedelta(days=7)


def _clean_exclusions(exclusions: list[str] | None) -> list[str]:
    if not exclusions:
        return []
    return [item.strip() for item in exclusions if item and item.strip()]


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
        normalized = _normalize_page_url(item, source.normalized_domain)
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
    normalized = _normalize_page_url(url, source.normalized_domain)
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


def _clean_schedule(schedule: str | None) -> SourceSchedule:
    raw = (schedule or SourceSchedule.weekly.value).strip().lower()
    if raw not in ALLOWED_SCHEDULES:
        raise HTTPException(status_code=422, detail="Schedule must be daily, weekly, or manual")
    return SourceSchedule(raw)


def create_url_source(
    *,
    client: Client,
    url: str,
    name: str | None,
    schedule: str | None,
    exclusions: list[str] | None,
    db: Session,
) -> tuple[UrlSource, UrlPreflightResult]:
    cleaned_exclusions = _clean_exclusions(exclusions)
    preflight = preflight_url_source(client=client, url=url, exclusions=cleaned_exclusions, db=db)
    cleaned_schedule = _clean_schedule(schedule)
    source = UrlSource(
        client_id=client.id,
        name=(name or preflight.title or preflight.normalized_domain).strip()[:255],
        url=preflight.normalized_url,
        normalized_domain=preflight.normalized_domain,
        status=SourceStatus.paused if not client.openai_api_key else SourceStatus.queued,
        crawl_schedule=cleaned_schedule,
        exclusion_patterns=cleaned_exclusions or None,
        pages_found=preflight.estimated_pages,
        warning_message="\n".join(preflight.warnings) if preflight.warnings else None,
        next_crawl_at=_schedule_next_run(cleaned_schedule),
        metadata_json={"auto_title": preflight.title},
    )
    if not client.openai_api_key:
        source.error_message = "Indexing paused — check your OpenAI key."
    db.add(source)
    db.commit()
    db.refresh(source)
    return source, preflight


def _approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _detect_platform(html: str, url: str) -> str:
    lower = html.lower()
    hostname = urlparse(url).netloc.lower()
    if "docusaurus" in lower:
        return "docusaurus"
    if "gitbook" in lower or "gitbook.io" in hostname:
        return "gitbook"
    if "mintlify" in lower:
        return "mintlify"
    if "readme.io" in hostname or "readme.com" in hostname:
        return "readme"
    if "vitepress" in lower:
        return "vitepress"
    return "generic"


def _extract_page(url: str, html: str) -> ExtractedPage | None:
    soup = BeautifulSoup(html, "html.parser")
    for selector in ("script", "style", "nav", "footer", "aside", "noscript"):
        for node in soup.select(selector):
            node.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup

    title = ""
    h1 = root.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = title or urlparse(url).path.strip("/") or urlparse(url).netloc

    sections: list[tuple[str, str]] = []
    current_heading = title
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n\n".join(part for part in buffer if part.strip()).strip()
        if text:
            sections.append((current_heading, text))
        buffer = []

    for node in root.find_all(["h1", "h2", "h3", "p", "li", "pre", "table"], recursive=True):
        name = node.name.lower()
        text = node.get_text("\n", strip=True)
        if not text:
            continue
        if name in {"h1", "h2", "h3"}:
            flush()
            current_heading = text
            continue
        buffer.append(text)
    flush()

    if not sections:
        body_text = root.get_text("\n", strip=True)
        if not body_text:
            return None
        sections = [(title, body_text)]

    full_text = "\n\n".join(text for _, text in sections).strip()
    if not full_text:
        return None

    chunks = _build_chunks(title, sections)
    if not chunks:
        return None

    return ExtractedPage(url=url, title=title[:255], text=full_text, chunks=chunks)


def _build_chunks(title: str, sections: list[tuple[str, str]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    chunk_index = 0
    for section_title, text in sections:
        parts = [p.strip() for p in _SECTION_SPLIT_RE.split(text) if p.strip()]
        current: list[str] = []
        current_tokens = 0
        for part in parts:
            part_tokens = _approx_tokens(part)
            if current and current_tokens + part_tokens > 500:
                raw = " ".join(current).strip()
                if raw:
                    chunks.append(
                        {
                            "chunk_index": chunk_index,
                            "raw_text": raw,
                            "section_title": section_title,
                            "chunk_text": _build_chunk_text(raw, title, section_title),
                            "token_count": _approx_tokens(raw),
                            "content_hash": _content_hash(raw),
                        }
                    )
                    chunk_index += 1
                overlap = current[-2:] if len(current) >= 2 else current[-1:]
                current = list(overlap)
                current_tokens = sum(_approx_tokens(item) for item in current)
            current.append(part)
            current_tokens += part_tokens
        if current:
            raw = " ".join(current).strip()
            if raw:
                chunks.append(
                    {
                        "chunk_index": chunk_index,
                        "raw_text": raw,
                        "section_title": section_title,
                        "chunk_text": _build_chunk_text(raw, title, section_title),
                        "token_count": _approx_tokens(raw),
                        "content_hash": _content_hash(raw),
                    }
                )
                chunk_index += 1
    return chunks


def _build_chunk_text(chunk_text: str, page_title: str, section: str) -> str:
    parts: list[str] = []
    if page_title:
        parts.append(f"Page: {page_title}")
    if section and section != page_title:
        parts.append(f"Section: {section}")
    parts.append(chunk_text)
    return "\n\n".join(parts)


def _fetch_page_html(url: str) -> str | None:
    context = FetchContext(stage="crawl:page", url=url)
    with _http_client(FETCH_TIMEOUT_SECONDS) as client:
        try:
            response = _request_with_safe_redirects(client, "GET", url, context=context)
            _raise_for_upstream_status(response, context)
        except HTTPException as exc:
            _log_fetch(logging.INFO, "Skipping page after fetch failure", context, detail=exc.detail)
            return None
    if response.status_code >= 400 or not _is_html_like(response):
        _log_fetch(
            logging.INFO,
            "Skipping page with unsupported response",
            context,
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
        )
        return None
    return response.text


def _embed_chunks(chunks: list[dict[str, Any]], api_key: str | None) -> list[list[float]]:
    if not chunks:
        return []
    client = get_openai_client(api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=[chunk["chunk_text"] for chunk in batch],
        )
        vectors.extend(item.embedding for item in response.data)
    return vectors


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
    content_hash = _content_hash(page.text)
    if existing and _content_hash(existing.parsed_text or "") == content_hash:
        existing.filename = page.title[:255]
        existing.status = DocumentStatus.ready
        existing.file_type = DocumentType.url
        return existing, len(existing.embeddings)

    if existing:
        db.query(Embedding).filter(Embedding.document_id == existing.id).delete()
        doc = existing
    else:
        doc = Document(
            client_id=source.client_id,
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

    vectors = _embed_chunks(page.chunks, api_key)
    for chunk, vector in zip(page.chunks, vectors):
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
    return doc, len(page.chunks)


def list_knowledge_sources(client_id: uuid.UUID, db: Session) -> dict[str, Any]:
    files = (
        db.query(Document)
        .filter(Document.client_id == client_id)
        .filter(Document.source_id.is_(None))
        .order_by(Document.created_at.desc())
        .all()
    )
    url_sources = (
        db.query(UrlSource)
        .filter(UrlSource.client_id == client_id)
        .order_by(UrlSource.created_at.desc())
        .all()
    )
    return {"documents": files, "url_sources": url_sources}


def get_url_source(source_id: uuid.UUID, client_id: uuid.UUID, db: Session) -> UrlSource:
    source = (
        db.query(UrlSource)
        .options(selectinload(UrlSource.runs), selectinload(UrlSource.documents))
        .filter(UrlSource.id == source_id)
        .filter(UrlSource.client_id == client_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


def update_url_source(
    *,
    source_id: uuid.UUID,
    client_id: uuid.UUID,
    name: str | None,
    schedule: str | None,
    exclusions: list[str] | None,
    db: Session,
) -> UrlSource:
    source = get_url_source(source_id, client_id, db)
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


def delete_url_source(source_id: uuid.UUID, client_id: uuid.UUID, db: Session) -> None:
    source = get_url_source(source_id, client_id, db)
    db.delete(source)
    db.commit()


def delete_source_document(
    *,
    source_id: uuid.UUID,
    document_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session,
) -> None:
    source = get_url_source(source_id, client_id, db)
    doc = (
        db.query(Document)
        .options(selectinload(Document.embeddings))
        .filter(Document.id == document_id)
        .first()
    )
    if not doc or doc.client_id != client_id or doc.source_id != source.id:
        raise HTTPException(status_code=404, detail="Page not found")

    _exclude_url_from_future_crawls(source, doc.source_url)
    db.delete(doc)
    db.flush()
    _recalculate_source_counts(source, db)
    db.commit()


def trigger_refresh(
    *,
    source_id: uuid.UUID,
    client: Client,
    db: Session,
) -> UrlSource:
    source = get_url_source(source_id, client.id, db)
    now = _utcnow()
    if source.last_refresh_requested_at and now - source.last_refresh_requested_at < dt.timedelta(hours=1):
        remaining = dt.timedelta(hours=1) - (now - source.last_refresh_requested_at)
        minutes = max(1, int(remaining.total_seconds() // 60))
        raise HTTPException(
            status_code=429,
            detail=f"Refresh available in {minutes} min.",
        )
    source.last_refresh_requested_at = now
    source.status = SourceStatus.paused if not client.openai_api_key else SourceStatus.queued
    if not client.openai_api_key:
        source.error_message = "Indexing paused — check your OpenAI key."
    else:
        source.error_message = None
        source.warning_message = None
    db.commit()
    db.refresh(source)
    return source


def _mark_run_finished(run: UrlSourceRun, *, status: str, error_message: str | None = None) -> None:
    run.status = status
    run.error_message = error_message
    run.finished_at = _utcnow()
    if run.created_at:
        started = run.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=dt.timezone.utc)
        run.duration_seconds = max(0, int((run.finished_at - started).total_seconds()))


class _CrawlAborted(Exception):
    """Raised when the crawl loop terminates early (e.g. bad OpenAI key)."""


def _plan_crawl(
    source: UrlSource,
    db: "Session",
) -> tuple[list[str], list[str], int]:
    """Discover URLs and compute which ones to crawl.

    Returns (urls_to_crawl, all_discovered_urls, remaining_capacity).
    """
    existing_docs = db.query(Document).filter(Document.source_id == source.id).all()
    existing_urls = {doc.source_url for doc in existing_docs if doc.source_url}
    allowed_total, remaining_capacity = _allowed_source_document_total(
        db,
        client_id=source.client_id,
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
    return urls, discovered_urls, remaining_capacity


def _index_pages(
    source: UrlSource,
    run: UrlSourceRun,
    urls: list[str],
    api_key: str,
    db: "Session",
) -> tuple[set[str], list[dict[str, str]], int]:
    """Fetch, extract, and index each URL.

    Returns (indexed_urls, failures, chunks_created).
    Raises _CrawlAborted if the crawl must stop early (e.g. bad OpenAI key).
    """
    root_html = _fetch_page_html(source.url) or ""
    source.metadata_json = {
        **(source.metadata_json or {}),
        "platform": _detect_platform(root_html, source.url) if root_html else "generic",
        "limit_reached": False,
    }

    indexed_urls: set[str] = set()
    failures: list[dict[str, str]] = []
    chunks_created = 0

    for url in urls:
        html = _fetch_page_html(url)
        if not html:
            failures.append({"url": url, "reason": "Could not fetch HTML"})
            continue
        page = _extract_page(url, html)
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
                raise _CrawlAborted
            raise
        indexed_urls.add(url)
        chunks_created += page_chunks
        source.pages_indexed = len(indexed_urls)
        source.chunks_created = chunks_created
        if source.pages_indexed and source.pages_indexed % 5 == 0:
            db.commit()

    return indexed_urls, failures, chunks_created


def _finalize_crawl(
    source: UrlSource,
    run: UrlSourceRun,
    indexed_urls: set[str],
    failures: list[dict[str, str]],
    chunks_created: int,
    discovered_urls: list[str],
    urls: list[str],
    remaining_capacity: int,
    started: float,
    db: "Session",
) -> None:
    """Remove stale documents, update source/run status, and commit."""
    stale_docs = (
        db.query(Document)
        .filter(Document.source_id == source.id)
        .filter(Document.source_url.isnot(None))
        .all()
    )
    for doc in stale_docs:
        if doc.source_url and doc.source_url not in indexed_urls:
            db.delete(doc)

    failure_ratio = (len(failures) / len(urls)) if urls else 0.0
    source.last_crawled_at = _utcnow()
    source.next_crawl_at = _schedule_next_run(source.crawl_schedule)
    source.pages_found = len(urls)
    source.pages_indexed = len(indexed_urls)
    source.chunks_created = chunks_created
    source.warning_message = None
    source.error_message = None
    if len(discovered_urls) > len(urls):
        source.warning_message = (
            f"Knowledge capacity reached. Indexed {len(urls)} of about {len(discovered_urls)} discovered pages."
        )
        source.metadata_json = {
            **(source.metadata_json or {}),
            "limit_reached": True,
            "capacity_limited": True,
            "remaining_capacity": remaining_capacity,
        }
    else:
        source.metadata_json = {
            **(source.metadata_json or {}),
            "limit_reached": False,
            "capacity_limited": False,
            "remaining_capacity": remaining_capacity,
        }

    if failure_ratio > 0.3:
        source.status = SourceStatus.error
        source.error_message = "Indexing failed — most pages were unreachable."
        _mark_run_finished(run, status=SourceStatus.error.value, error_message=source.error_message)
    else:
        source.status = SourceStatus.ready
        _mark_run_finished(run, status=SourceStatus.ready.value)

    run.pages_indexed = len(indexed_urls)
    run.failed_urls = failures
    run.duration_seconds = max(0, int(time.monotonic() - started))
    db.commit()


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
        urls, discovered_urls, remaining_capacity = _plan_crawl(source, db)
        source.pages_found = len(discovered_urls)
        run.pages_found = len(discovered_urls)
        db.commit()

        try:
            indexed_urls, failures, chunks_created = _index_pages(source, run, urls, api_key, db)
        except _CrawlAborted:
            return

        _finalize_crawl(
            source, run, indexed_urls, failures, chunks_created,
            discovered_urls, urls, remaining_capacity, started, db,
        )
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
