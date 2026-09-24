"""Content extraction, chunking, embedding, and tenant knowledge extraction."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from backend.chunkers.html import clean_html_root
from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
from backend.documents.parsers import OpenAPIChunk
from backend.knowledge.entity_extractor import extract_entities_from_passage
from backend.models import Document, DocumentType, Embedding
from backend.search.service import invalidate_tenant_search_caches

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
    soup, root = clean_html_root(html)

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


def _chunk_text_value(chunk: dict[str, Any]) -> str:
    """Return a chunk's embedding/storage text.

    Upload chunkers key it ``text``; the crawl-side chunk builders here key
    it ``chunk_text`` — support both so ``persist_document_embeddings`` can
    take chunks from either source.
    """
    return str(chunk.get("chunk_text") or chunk.get("text") or "")


def _embed_chunks(chunks: list[dict[str, Any]], api_key: str | None) -> list[list[float]]:
    if not chunks:
        return []
    oai = get_openai_client(api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        inputs = [_chunk_text_value(chunk) for chunk in batch]
        response = call_openai_with_retry(
            "document_embed_chunks",
            lambda inputs=inputs: oai.embeddings.create(
                model=settings.embedding_model,
                input=inputs,
            ),
            call_type="embedding",
        )
        vectors.extend(item.embedding for item in response.data)
    return vectors


def persist_document_embeddings(
    doc: Document,
    chunks: list[dict[str, Any]],
    api_key: str | None,
    db: Session,
    *,
    extra_meta: dict[str, Any] | None = None,
    commit: bool = True,
) -> list[Embedding]:
    """Delete a document's embeddings and persist freshly-chunked ones.

    Single persistence path for both the upload and URL-crawl ingestion
    flows: batches the OpenAI embeddings call (``EMBED_BATCH_SIZE``) through
    ``call_openai_with_retry`` and writes one ``Embedding`` row per chunk.
    ``extra_meta`` carries document/page-level metadata_json fields the
    caller wants merged into every chunk (e.g. ``source_url``,
    ``page_content_hash``) — per-chunk fields already on each chunk dict
    (anything but ``text``/``chunk_text``) pass through as-is.

    ``commit=False`` flushes (assigning IDs) but leaves the commit to the
    caller — for callers that still have more document-row changes (e.g.
    ``doc.status``) to fold into the same transaction/commit.
    """
    db.query(Embedding).filter(Embedding.document_id == doc.id).delete()
    if not chunks:
        if commit:
            db.commit()
        else:
            db.flush()
        return []

    vectors = _embed_chunks(chunks, api_key)
    embeddings: list[Embedding] = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        meta: dict[str, Any] = {
            "chunk_index": chunk.get("chunk_index", i),
            "filename": doc.filename,
            "file_type": doc.file_type.value,
            **{k: v for k, v in chunk.items() if k not in ("text", "chunk_text")},
        }
        if doc.language:
            meta.setdefault("language", doc.language)
        if extra_meta:
            meta.update(extra_meta)
        meta["embedding_model"] = settings.embedding_model
        emb = Embedding(
            document_id=doc.id,
            chunk_text=_chunk_text_value(chunk),
            vector=vector,
            metadata_json=meta,
        )
        db.add(emb)
        embeddings.append(emb)
    if commit:
        db.commit()
        for emb in embeddings:
            db.refresh(emb)
    else:
        db.flush()
    return embeddings


def _populate_entities_for_embeddings(
    *,
    embeddings: list[Embedding],
    api_key: str,
    tenant_id: str | None,
    db: Session,
) -> None:
    """Populate ``Embedding.entities`` via per-chunk NER (best-effort).

    Iterates over the just-saved embeddings, calls
    ``extract_entities_from_passage`` for each chunk, and writes the
    returned list into ``entities``. Per-chunk failures degrade to ``[]``
    inside ``extract_entities_from_passage`` itself, so this loop never
    raises — at worst we get a row with ``entities=[]`` (the same as a
    legacy row) and the entity-overlap channel gets no signal for that
    chunk. Embeddings are already committed before this runs, so an
    abort here is non-destructive.

    **Commit policy:** one commit per chunk. NER is the slow part
    (~1-2s/chunk via gpt-4.1-mini), and holding a single transaction
    open across all chunks would lock the connection for ~150s on a
    100-chunk megadoc — connection pool hogging + dirty-row liveness
    issues. Per-chunk commits trade N round-trips for short-lived
    transactions; the round-trip cost (~milliseconds each) is dwarfed
    by NER latency, so the trade is free. As a side benefit, partial
    progress survives a crash mid-loop: chunks already processed keep
    their entities, the rest stay at the server-default empty list and
    can be backfilled by a re-index.
    """
    updated = 0
    failed_commits = 0
    for emb in embeddings:
        try:
            ents = extract_entities_from_passage(
                emb.chunk_text or "",
                api_key,
                tenant_id=tenant_id,
            )
        except Exception:
            # Defense in depth: extract_entities_from_passage is documented
            # to swallow its own exceptions, but a broken caller / monkeypatch
            # in tests could still leak. Never let one bad chunk corrupt the
            # whole document's ingest.
            logger.warning(
                "entity_extraction_unexpected_error",
                extra={"embedding_id": str(emb.id)},
            )
            ents = []
        emb.entities = ents
        try:
            db.commit()
        except Exception:
            # One failed commit shouldn't kill the rest of the document.
            # Roll back this chunk's update and keep going — the row stays
            # at the server-default empty list, which is the same as legacy
            # rows and safe for the Step 5 ``?|`` predicate.
            logger.warning(
                "entity_extraction_commit_failed",
                extra={"embedding_id": str(emb.id)},
            )
            db.rollback()
            failed_commits += 1
            continue
        if ents:
            updated += 1
    logger.info(
        "entity_extraction_populated",
        extra={
            "chunks": len(embeddings),
            "non_empty": updated,
            "failed_commits": failed_commits,
        },
    )


def after_document_indexed(
    doc: Document,
    embeddings: list[Embedding],
    *,
    api_key: str | None,
    db: Session,
) -> None:
    """Post-index steps shared by the upload and URL-crawl ingestion flows.

    Invalidates the tenant's search caches (BM25 + KB-script), populates
    per-chunk entities (Step 4 of the entity-aware retrieval epic), and
    enqueues tenant-knowledge extraction as a durable job. Best-effort
    throughout: embeddings are already committed by
    ``persist_document_embeddings`` by the time this runs, so nothing here
    blocks or reverts the ingest.

    Cache invalidation runs first, before the (much slower) NER pass, so the
    stale-cache window doesn't include per-chunk entity-extraction latency.
    """
    invalidate_tenant_search_caches(doc.tenant_id)
    if embeddings and api_key:
        _populate_entities_for_embeddings(
            embeddings=embeddings,
            api_key=api_key,
            tenant_id=str(doc.tenant_id) if doc.tenant_id else None,
            db=db,
        )
    _run_tenant_knowledge_extraction_best_effort(
        document_id=doc.id,
        tenant_id=doc.tenant_id,
        api_key=api_key,
    )


def _url_knowledge_extract_when_unchanged() -> bool:
    return settings.url_knowledge_extract_when_unchanged


def _run_tenant_knowledge_extraction_best_effort(
    *,
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    api_key: str | None,
) -> None:
    """Enqueue knowledge extraction job after URL crawl embedding.

    Replaces the former inline sync call: enqueues a durable ARQ job so
    extraction failures retry instead of being silently dropped, and the
    embed pipeline is no longer blocked on LLM calls.

    Graceful degradation: if Redis is unavailable, logs WARNING and returns
    without raising so the embed pipeline is never broken by queue issues.
    """
    if not api_key or not tenant_id:
        return
    try:
        from backend.jobs.knowledge_extraction import enqueue_knowledge_extraction_sync

        enqueue_knowledge_extraction_sync(
            document_id=document_id,
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning(
            "knowledge_enqueue_failed document_id=%s",
            document_id,
            exc_info=True,
        )
