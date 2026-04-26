"""Content extraction, chunking, embedding, and tenant knowledge extraction."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.documents.parsers import OpenAPIChunk
from backend.models import DocumentType

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 100
_SECTION_SPLIT_RE = re.compile(r"(?<=[.?!])\s+|\n{2,}")


@dataclass
class ExtractedPage:
    url: str
    title: str
    text: str
    chunks: list[dict[str, Any]]


@dataclass
class StructuredSource:
    title: str
    parsed_text: str
    chunks: list[OpenAPIChunk]
    source_format: str


def _approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_chunk_text(chunk_text: str, page_title: str, section: str) -> str:
    parts: list[str] = []
    if page_title:
        parts.append(f"Page: {page_title}")
    if section and section != page_title:
        parts.append(f"Section: {section}")
    parts.append(chunk_text)
    return "\n\n".join(parts)


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


def _normalize_source_format(source_format: str, *, from_url: bool) -> str:
    if not from_url:
        return source_format
    if source_format == "json":
        return "url-json"
    if source_format == "yaml":
        return "url-yaml"
    return f"url-{source_format}"


def _build_structured_openapi_chunks(
    openapi_chunks: list[OpenAPIChunk],
    *,
    filename: str,
    source_url: str,
    source_format: str,
) -> list[dict[str, Any]]:
    normalized_source_format = _normalize_source_format(source_format, from_url=True)
    out: list[dict[str, Any]] = []
    for index, chunk in enumerate(openapi_chunks):
        out.append(
            {
                "chunk_index": index,
                "chunk_text": chunk.text,
                "type": "api_endpoint",
                "subtype": "primary",
                "path": chunk.path,
                "method": chunk.method,
                "operation_id": chunk.operation_id,
                "tags": chunk.tags,
                "deprecated": chunk.deprecated,
                "content_types": chunk.content_types,
                "response_codes": chunk.response_codes,
                "auth_schemes": chunk.auth_schemes,
                "has_examples": chunk.has_examples,
                "filename": filename,
                "file_type": DocumentType.swagger.value,
                "source_kind": "url",
                "source_format": normalized_source_format,
                "spec_version": chunk.spec_version,
                "source_url": source_url,
            }
        )
    return out


def _render_structured_openapi_chunks(
    openapi_chunks: list[OpenAPIChunk],
    *,
    title: str,
    source_url: str,
    source_format: str,
) -> list[dict[str, Any]]:
    return _build_structured_openapi_chunks(
        openapi_chunks,
        filename=title[:255],
        source_url=source_url,
        source_format=source_format,
    )


def _embed_chunks(chunks: list[dict[str, Any]], api_key: str | None) -> list[list[float]]:
    if not chunks:
        return []
    oai = get_openai_client(api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        response = oai.embeddings.create(
            model=settings.embedding_model,
            input=[chunk["chunk_text"] for chunk in batch],
        )
        vectors.extend(item.embedding for item in response.data)
    return vectors


def _url_knowledge_extract_when_unchanged() -> bool:
    """If true, run tenant knowledge extraction even when page/spec content hash is unchanged.

    Default false to avoid extra LLM cost on every scheduled re-crawl. Set env to ``1``/``true``
    once after deploy to backfill ``tenant_faq`` / profile for already-indexed URL sources.
    """
    raw = os.getenv("URL_KNOWLEDGE_EXTRACT_WHEN_UNCHANGED", "")
    return raw.strip().lower() in ("1", "true", "yes")


def _run_tenant_knowledge_extraction_best_effort(
    *,
    document_id: uuid.UUID,
    api_key: str | None,
) -> None:
    """
    Match file-upload embedding flow: after chunks exist, merge profile + FAQ candidates.

    URL crawls bypass ``run_embeddings_background``; without this hook, GitBook/docs
    URLs index for RAG but never populate ``tenant_faq`` / profile extraction.

    Uses a **fresh** DB session from ``backend.core.db`` so ``db.rollback()`` inside
    ``insert_new_faq_candidates`` / extraction error paths cannot undo the crawler session.
    """
    if not api_key:
        return
    from backend.core import db as core_db

    db_extract = core_db.SessionLocal()
    try:
        from backend.tenant_knowledge.extract_tenant_knowledge import (
            run_extract_client_knowledge_for_document,
        )

        run_extract_client_knowledge_for_document(
            document_id=document_id,
            db=db_extract,
            api_key=api_key,
        )
    except Exception:
        logger.warning(
            "Tenant knowledge extraction failed for URL document_id=%s",
            document_id,
            exc_info=True,
        )
    finally:
        db_extract.close()
