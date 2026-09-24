"""Content extraction, chunking, embedding, and tenant knowledge extraction."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from backend.chunkers.html import clean_html_root, html_to_markdown_text
from backend.chunkers.registry import get_chunker
from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.core.openai_retry import call_openai_with_retry
from backend.documents.parsers import OpenAPIChunk
from backend.knowledge.entity_extractor import extract_entities_from_passage
from backend.models import Document, Embedding
from backend.search.service import invalidate_tenant_search_caches

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 100


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


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_title(soup: Any, root: Any, url: str) -> str:
    title = ""
    h1 = root.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)
    return title or urlparse(url).path.strip("/") or urlparse(url).netloc


def _extract_page(url: str, html: str) -> ExtractedPage | None:
    """Extract a crawled page the same way uploaded HTML is chunked.

    Shares ``html_to_markdown_text`` (readability cleanup, nested-node guard,
    table rendering) and the heading-aware markdown chunker with uploads so
    crawled pages get identical chunk boundaries and heading-path metadata.
    """
    soup, root = clean_html_root(html)
    title = _page_title(soup, root, url)[:255]

    markdown_text = html_to_markdown_text(html)
    if not markdown_text.strip():
        return None

    chunks: list[dict[str, Any]] = list(get_chunker("html")(markdown_text))
    if not chunks:
        return None

    # gap_analyzer and observability read ``section_title`` from chunk
    # metadata; the markdown chunker only carries the full heading path, so
    # derive the nearest heading (its last segment) here, same as the
    # crawler's own pre-chunker extraction used to.
    for chunk in chunks:
        heading_path = chunk.get("heading_path")
        chunk["section_title"] = heading_path.rsplit(" > ", 1)[-1] if heading_path else title

    return ExtractedPage(url=url, title=title, text=markdown_text, chunks=chunks)


def _normalize_source_format(source_format: str, *, from_url: bool) -> str:
    if not from_url:
        return source_format
    if source_format == "json":
        return "url-json"
    if source_format == "yaml":
        return "url-yaml"
    return f"url-{source_format}"


def _chunk_text_value(chunk: dict[str, Any]) -> str:
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
) -> list[Embedding]:
    """Delete a document's embeddings and add freshly-chunked ones (flush, no commit).

    Single persistence path for the upload and URL-crawl ingestion flows.
    """
    db.query(Embedding).filter(Embedding.document_id == doc.id).delete()
    if not chunks:
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

    One commit per chunk — NER is slow (~1-2s/chunk), so this avoids
    holding a single long transaction and lets partial progress survive
    a crash mid-loop. Failures degrade to ``entities=[]``, never raise.
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
            # Defense in depth: never let one bad chunk corrupt the whole ingest.
            logger.warning(
                "entity_extraction_unexpected_error",
                extra={"embedding_id": str(emb.id)},
            )
            ents = []
        emb.entities = ents
        try:
            db.commit()
        except Exception:
            # Roll back this chunk only; siblings keep their NER output.
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
    """Invalidate search caches, populate entities, and enqueue knowledge extraction (in that order)."""
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
