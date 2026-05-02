"""Business logic for embedding creation and management."""

from __future__ import annotations

import logging
import re
import uuid
from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.openai_client import get_openai_client
from backend.documents.parsers import (
    OPENAPI_REQUEST_DETAIL_MARKER,
    OPENAPI_RESPONSE_DETAIL_MARKER,
    extract_openapi_chunks_from_rendered_text,
)
from backend.gap_analyzer.jobs import run_mode_a_for_tenant_when_queue_empty_best_effort
from backend.gap_analyzer.repository import invalidate_bm25_cache_for_tenant
from backend.knowledge.entity_extractor import extract_entities_from_passage
from backend.models import Document, DocumentStatus, DocumentType, Embedding

logger = logging.getLogger(__name__)

# Optimal chunking parameters per document type.
# Tune these values here when re-evaluating retrieval quality.
CHUNKING_CONFIG: dict[str, dict[str, int]] = {
    "swagger": {"chunk_size": 500, "overlap_sentences": 0},
    "markdown": {"chunk_size": 700, "overlap_sentences": 1},
    "pdf":      {"chunk_size": 1000, "overlap_sentences": 1},
    # future types
    "logs":     {"chunk_size": 300, "overlap_sentences": 0},
    "code":     {"chunk_size": 600, "overlap_sentences": 1},
}
_CHUNKING_DEFAULT: dict[str, int] = {"chunk_size": 700, "overlap_sentences": 1}
_OPENAPI_DETAIL_SPLIT_LIMIT = 2200


def _should_keep_openapi_as_single_chunk(
    text_body: str,
    *,
    has_forced_detail_split: bool,
) -> bool:
    return len(text_body) <= _OPENAPI_DETAIL_SPLIT_LIMIT and not has_forced_detail_split


class ChunkInfo(TypedDict):
    """One text chunk with position in the original document."""

    text: str
    chunk_index: int
    char_offset: int
    char_end: int


def _sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """
    Split like chunk_text (sentence boundaries); return (sentence, start, end) in `text`.
    """
    if not text.strip():
        return []
    lead = len(text) - len(text.lstrip())
    trail = len(text) - len(text.rstrip())
    body = text[lead : len(text) - trail]
    raw_parts = re.split(r"(?<=[.?!])\s+|\n{2,}", body)
    sentences = [p.strip() for p in raw_parts if p.strip()]
    if not sentences:
        return []

    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for sent in sentences:
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        idx = body.find(sent, cursor)
        if idx < 0:
            idx = cursor
        start = lead + idx
        end = start + len(sent)
        spans.append((sent, start, end))
        cursor = idx + len(sent)
    return spans


def _joined_char_len(parts: list[tuple[str, int, int]]) -> int:
    if not parts:
        return 0
    return sum(len(p[0]) for p in parts) + (len(parts) - 1)


def chunk_text(
    text: str,
    chunk_size: int = 500,
    overlap_sentences: int = 1,
) -> list[ChunkInfo]:
    """
    Split text into chunks by sentences (not raw characters).

    Returns list of dicts with text, chunk_index, char_offset, char_end (offsets in original `text`).
    """
    spans = _sentence_spans(text)
    if not spans:
        return []

    chunks: list[ChunkInfo] = []
    current: list[tuple[str, int, int]] = []
    current_len = 0

    for sentence, s_start, s_end in spans:
        sentence_len = len(sentence)
        if current_len + sentence_len > chunk_size and current:
            chunk_text_str = " ".join(s[0] for s in current)
            chunks.append(
                {
                    "text": chunk_text_str,
                    "chunk_index": len(chunks),
                    "char_offset": current[0][1],
                    "char_end": current[-1][2],
                }
            )
            overlap = current[-overlap_sentences:] if overlap_sentences > 0 else []
            current = list(overlap)
            current_len = _joined_char_len(current)

        current.append((sentence, s_start, s_end))
        current_len += sentence_len + 1

    if current:
        chunk_text_str = " ".join(s[0] for s in current)
        chunks.append(
            {
                "text": chunk_text_str,
                "chunk_index": len(chunks),
                "char_offset": current[0][1],
                "char_end": current[-1][2],
            }
        )

    return chunks


def _build_swagger_chunks(text: str) -> list[dict[str, object]]:
    chunks, source_format, spec_version = extract_openapi_chunks_from_rendered_text(text)
    if not chunks:
        return chunk_text(text, **CHUNKING_CONFIG["swagger"])

    rendered_chunks: list[dict[str, object]] = []
    for operation_chunk in chunks:
        base_meta = {
            "type": "api_endpoint",
            "path": operation_chunk.path,
            "method": operation_chunk.method,
            "operation_id": operation_chunk.operation_id,
            "tags": operation_chunk.tags,
            "deprecated": operation_chunk.deprecated,
            "content_types": operation_chunk.content_types,
            "response_codes": operation_chunk.response_codes,
            "auth_schemes": operation_chunk.auth_schemes,
            "has_examples": operation_chunk.has_examples,
            "source_format": source_format,
            "spec_version": spec_version,
        }

        text_body = operation_chunk.text
        request_detail_idx = text_body.find(OPENAPI_REQUEST_DETAIL_MARKER)
        response_detail_idx = text_body.find(OPENAPI_RESPONSE_DETAIL_MARKER)
        has_forced_detail_split = request_detail_idx >= 0 or response_detail_idx >= 0

        if _should_keep_openapi_as_single_chunk(
            text_body,
            has_forced_detail_split=has_forced_detail_split,
        ):
            rendered_chunks.append({"text": text_body, "subtype": "primary", **base_meta})
            continue

        request_marker = "\nRequest Body:\n"
        response_marker = "\nResponses:\n"
        request_idx = text_body.find(request_marker)
        response_idx = text_body.find(response_marker)

        # Keep the primary chunk focused on endpoint-level context only.
        # As soon as request/response sections or their richer detail markers begin,
        # we cut the primary chunk and move the heavier schema material into the
        # specialized secondary chunks below.
        primary_end = min(
            [idx for idx in (request_idx, response_idx, request_detail_idx, response_detail_idx) if idx >= 0],
            default=len(text_body),
        )
        primary_text = text_body[:primary_end].strip()
        if primary_text:
            rendered_chunks.append({"text": primary_text, "subtype": "primary", **base_meta})
        if request_idx >= 0 and request_detail_idx >= 0:
            request_summary_end = response_idx if response_idx > request_idx else request_detail_idx
            request_detail_end = response_detail_idx if response_detail_idx > request_detail_idx else len(text_body)
            request_parts = [
                f"Endpoint: {operation_chunk.method.upper()} {operation_chunk.path}",
                text_body[request_idx:request_summary_end].strip(),
                text_body[request_detail_idx:request_detail_end].strip(),
            ]
            request_text = "\n".join(part for part in request_parts if part)
            rendered_chunks.append({"text": request_text, "subtype": "request_schema", **base_meta})
        elif request_idx >= 0 and len(text_body) > _OPENAPI_DETAIL_SPLIT_LIMIT:
            request_end = response_idx if response_idx > request_idx else len(text_body)
            request_text = (
                f"Endpoint: {operation_chunk.method.upper()} {operation_chunk.path}\n"
                + text_body[request_idx:request_end].strip()
            )
            rendered_chunks.append({"text": request_text, "subtype": "request_schema", **base_meta})

        if response_idx >= 0 and response_detail_idx >= 0:
            response_summary_end = request_detail_idx if request_detail_idx > response_idx else response_detail_idx
            response_parts = [
                f"Endpoint: {operation_chunk.method.upper()} {operation_chunk.path}",
                text_body[response_idx:response_summary_end].strip(),
                text_body[response_detail_idx:].strip(),
            ]
            response_text = "\n".join(part for part in response_parts if part)
            rendered_chunks.append({"text": response_text, "subtype": "response_schema", **base_meta})
        elif response_idx >= 0 and len(text_body) > _OPENAPI_DETAIL_SPLIT_LIMIT:
            response_text = (
                f"Endpoint: {operation_chunk.method.upper()} {operation_chunk.path}\n"
                + text_body[response_idx:].strip()
            )
            rendered_chunks.append({"text": response_text, "subtype": "response_schema", **base_meta})

    return rendered_chunks


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


def create_embeddings_for_document(
    document_id: uuid.UUID,
    db: Session,
    *,
    api_key: str,
) -> list[Embedding]:
    """
    Create embeddings for a document's parsed text.

    Fetches document, chunks parsed_text, calls OpenAI embeddings API,
    saves Embedding records. Replaces existing embeddings for the document.

    Raises:
        HTTPException 404: Document not found.
        HTTPException 400: Document status != ready or parsed_text empty.
        HTTPException 503: OpenAI API error.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
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

    # Delete existing embeddings (re-embed on demand)
    db.query(Embedding).filter(Embedding.document_id == document_id).delete()
    db.commit()
    invalidate_bm25_cache_for_tenant(doc.tenant_id)

    if doc.file_type == DocumentType.swagger:
        chunks = _build_swagger_chunks(doc.parsed_text)
    else:
        cfg = CHUNKING_CONFIG.get(doc.file_type.value, _CHUNKING_DEFAULT)
        chunks = chunk_text(doc.parsed_text, **cfg)
    if not chunks:
        return []

    chunk_texts = [str(c["text"]) for c in chunks]

    openai_client = get_openai_client(api_key)
    try:
        response = openai_client.embeddings.create(
            model=settings.embedding_model,
            input=chunk_texts,
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"OpenAI API unavailable: {e!s}",
        ) from e

    embeddings: list[Embedding] = []
    for i, item in enumerate(response.data):
        vector = item.embedding  # list of 1536 floats
        chunk = chunks[i] if i < len(chunks) else None
        text_part = str(chunk["text"]) if chunk else ""
        meta_base = (
            {
                "chunk_index": i,
                "filename": doc.filename,
                "file_type": doc.file_type.value,
                **{
                    key: value
                    for key, value in chunk.items()
                    if key != "text"
                },
            }
            if chunk
            else {"chunk_index": i}
        )
        if doc.language:
            meta_base.setdefault("language", doc.language)
        emb = Embedding(
            document_id=document_id,
            chunk_text=text_part,
            vector=vector,
            metadata_json=meta_base,
        )
        db.add(emb)
        embeddings.append(emb)
    db.commit()
    for emb in embeddings:
        db.refresh(emb)

    # Step 4 of entity-aware retrieval epic: populate the per-chunk entity
    # index. Best-effort and post-commit — embeddings are already durable
    # by this point, so a NER outage cannot block ingest. Each call is
    # bounded by the OpenAI client read timeout (no hot-path wall clock,
    # we are in a background worker). Failures collapse to ``[]`` inside
    # ``extract_entities_from_passage`` and are logged there.
    #
    # Sequential by design for V1: parallelism would speed up megadocs
    # (100+ chunks) ~10x but adds a thread pool we don't need yet — the
    # bottleneck for typical 5-30 chunk docs is the embedding API call,
    # not NER. Revisit if onboarding latency becomes user-visible.
    _populate_entities_for_embeddings(
        embeddings=embeddings,
        api_key=api_key,
        tenant_id=str(doc.tenant_id) if doc.tenant_id else None,
        db=db,
    )
    try:
        from backend.documents.service import run_document_health_check

        run_document_health_check(document_id, db)
    except Exception:
        pass
    return embeddings


def run_embeddings_background(document_id: uuid.UUID, api_key: str) -> None:
    """
    Background task: create embeddings using a dedicated DB session.

    Sets document status to `ready` on success or `error` on failure.
    Must be called via FastAPI BackgroundTasks (not from a request handler directly).
    """
    import logging

    from backend.core.db import SessionLocal

    logger = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        create_embeddings_for_document(document_id, db, api_key=api_key)
        doc = db.query(Document).filter(Document.id == document_id).first()
        tenant_id = doc.tenant_id if doc is not None else None
        if doc:
            doc.status = DocumentStatus.ready
            db.commit()
        # Enqueue knowledge extraction as a durable ARQ job so failures
        # retry and the embed pipeline is not blocked on LLM calls.
        if tenant_id is not None:
            try:
                from backend.jobs.knowledge_extraction import (
                    enqueue_knowledge_extraction_sync,
                )

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
        if tenant_id is not None:
            try:
                run_mode_a_for_tenant_when_queue_empty_best_effort(tenant_id)
            except Exception:
                logger.warning(
                    "Gap Analyzer Mode A trigger failed for document_id=%s tenant_id=%s",
                    document_id,
                    tenant_id,
                    exc_info=True,
                )
    except Exception:
        logger.exception("Background embedding failed for document %s", document_id)
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.status = DocumentStatus.error
            db.commit()
    finally:
        db.close()


def get_embeddings_for_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: Session,
) -> list[Embedding]:
    """
    Get all embeddings for a document. Verifies document ownership.

    Raises:
        HTTPException 404: Document not found or not owned by tenant.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or doc.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return (
        db.query(Embedding)
        .filter(Embedding.document_id == document_id)
        .order_by(Embedding.created_at.asc())
        .all()
    )


def delete_embeddings_for_document(
    document_id: uuid.UUID,
    db: Session,
) -> int:
    """
    Delete all embeddings for a document.

    Returns:
        Count of deleted embeddings.
    """
    tenant_id = (
        db.query(Document.tenant_id).filter(Document.id == document_id).scalar()
    )
    result = db.query(Embedding).filter(Embedding.document_id == document_id).delete()
    db.commit()
    invalidate_bm25_cache_for_tenant(tenant_id)
    return result
