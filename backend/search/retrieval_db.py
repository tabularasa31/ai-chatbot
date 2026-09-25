"""DB-backed retrieval: pgvector/cosine search, entity overlap, KB script detection."""

from __future__ import annotations

import logging
import time
import uuid
from time import perf_counter

from sqlalchemy import Text as SAText
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.core.scripts import NO_SCRIPT_BUCKET, detect_script_bucket
from backend.models import Document, Embedding
from backend.search.bm25 import BM25_CANDIDATE_POOL
from backend.search.fusion import _sort_scored_embeddings
from backend.search.types import VectorCandidateSet
from backend.utils.math import cosine_similarity

# A popular entity (e.g. "Pro plan" on a tenant with 10k chunks) could
# otherwise pull every row into memory before the Python intersection
# scoring step. The downstream RRF only consumes top RRF_CANDIDATE_POOL_MULTIPLIER
# * top_k anyway, so any cap >> that pool is safe.
ENTITY_SEARCH_CANDIDATE_LIMIT = 1000

logger = logging.getLogger(__name__)

# module-level caches: str(tenant_id) -> (value, monotonic_ts)
_TENANT_KB_SCRIPT_CACHE: dict[str, tuple[str | None, float]] = {}
_TENANT_KB_SCRIPTS_CACHE: dict[str, tuple[frozenset[str], float]] = {}
_TENANT_KB_SCRIPT_CACHE_TTL = 300.0  # 5 minutes
_KB_SCRIPT_SAMPLE_SIZE = 20
# Each script in this set costs one cross-lingual rewrite call per chat
# turn, so a script has to carry a real share of the corpus to earn one.
# A stray document must not put the whole tenant on the hook; a genuine
# minority-language section still clears the bar.
_KB_SCRIPT_MIN_SHARE = 0.05


def invalidate_tenant_kb_script_cache(tenant_id: uuid.UUID) -> None:
    """Drop the cached KB scripts for this tenant (call after document upload/delete)."""
    key = str(tenant_id)
    _TENANT_KB_SCRIPT_CACHE.pop(key, None)
    _TENANT_KB_SCRIPTS_CACHE.pop(key, None)


def invalidate_tenant_search_caches(tenant_id: uuid.UUID) -> None:
    """Drop every tenant-scoped search cache (BM25 corpus + KB script detection).

    Call this after any KB change (upload, delete, reindex, crawl) so BM25
    scoring and cross-lingual rewrite detection reflect the new corpus
    immediately instead of waiting out their TTLs.
    """
    from backend.gap_analyzer.repository import invalidate_bm25_cache_for_tenant

    invalidate_bm25_cache_for_tenant(tenant_id)
    invalidate_tenant_kb_script_cache(tenant_id)


async def _async_pgvector_search(
    tenant_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: AsyncSession,
) -> list[tuple[Embedding, float]]:
    """Native pgvector cosine-distance search (HNSW index)."""
    try:
        distance_expr = Embedding.vector.cosine_distance(query_vector)
        stmt = (
            select(Embedding, distance_expr.label("distance"))
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(Embedding.vector.isnot(None))
            .order_by(distance_expr)
            .limit(top_k)
            .options(selectinload(Embedding.document))
        )
        result = await db.execute(stmt)
        rows = result.all()
        return [(row[0], max(0.0, 1.0 - row[1])) for row in rows]
    except Exception:
        logger.exception("async pgvector search failed; falling back to Python cosine search")
        return await _async_python_cosine_search(tenant_id, query_vector, top_k, db)


async def _async_python_cosine_search(
    tenant_id: uuid.UUID,
    query_vector: list[float],
    top_k: int,
    db: AsyncSession,
) -> list[tuple[Embedding, float]]:
    """Pure-Python cosine search over metadata vectors (SQLite fallback)."""
    stmt = (
        select(Embedding)
        .join(Document, Embedding.document_id == Document.id)
        .filter(Document.tenant_id == tenant_id)
        .options(selectinload(Embedding.document))
    )
    result = await db.execute(stmt)
    embeddings = result.scalars().all()

    scored: list[tuple[Embedding, float]] = []
    for emb in embeddings:
        if emb.vector is not None:
            vector: list[float] | None = list(emb.vector)
            meta_vec = (emb.metadata_json or {}).get("vector")
            if meta_vec is not None and meta_vec != vector:
                logger.warning(
                    "embedding %s: emb.vector diverges from metadata_json[vector]",
                    emb.id,
                )
        else:
            meta = emb.metadata_json or {}
            vector = meta.get("vector")

        if not vector or not isinstance(vector, list) or len(vector) != len(query_vector):
            continue
        scored.append((emb, cosine_similarity(query_vector, vector)))

    return _sort_scored_embeddings(scored)[:top_k]


async def _async_build_vector_candidate_set(
    tenant_id: uuid.UUID,
    variant_vectors: list[list[float]],
    db: AsyncSession,
    *,
    is_sqlite: bool = False,
) -> VectorCandidateSet:
    """Run one vector search per variant vector and dedupe by max similarity."""
    vector_started_at = perf_counter()
    vector_search_fn = _async_python_cosine_search if is_sqlite else _async_pgvector_search
    vector_candidate_map: dict[uuid.UUID, tuple[Embedding, float]] = {}
    vector_search_call_count = 0
    for variant_vector in variant_vectors:
        vector_search_call_count += 1
        for embedding, similarity in await vector_search_fn(
            tenant_id,
            variant_vector,
            BM25_CANDIDATE_POOL,
            db,
        ):
            existing = vector_candidate_map.get(embedding.id)
            if existing is None or similarity > existing[1]:
                vector_candidate_map[embedding.id] = (embedding, similarity)
    return VectorCandidateSet(
        candidates=_sort_scored_embeddings(list(vector_candidate_map.values()))[
            :BM25_CANDIDATE_POOL
        ],
        call_count=vector_search_call_count,
        duration_ms=round((perf_counter() - vector_started_at) * 1000, 2),
    )


async def async_entity_overlap_search(
    tenant_id: uuid.UUID,
    query_entities: list[str],
    top_k: int,
    db: AsyncSession,
    *,
    is_sqlite: bool = False,
) -> list[tuple[Embedding, float]]:
    """Retrieve chunks whose ``entities`` overlap with the query's NER list."""
    if not query_entities or not tenant_id:
        return []

    if is_sqlite:
        stmt = (
            select(Embedding)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .options(selectinload(Embedding.document))
        )
    else:
        stmt = (
            select(Embedding)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(
                Embedding.entities.op("?|")(
                    cast(list(query_entities), ARRAY(SAText()))
                )
            )
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .limit(ENTITY_SEARCH_CANDIDATE_LIMIT)
            .options(selectinload(Embedding.document))
        )

    result = await db.execute(stmt)
    candidates = result.scalars().all()

    query_set = set(query_entities)
    scored: list[tuple[Embedding, float]] = []
    for emb in candidates:
        chunk_entities = emb.entities or []
        if not isinstance(chunk_entities, list):
            continue
        overlap = len(query_set.intersection(chunk_entities))
        if overlap == 0:
            continue
        scored.append((emb, float(overlap)))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]


async def _async_tenant_has_embeddings(
    tenant_id: uuid.UUID, db: AsyncSession
) -> bool:
    """Cheap existence check: does the tenant have any indexed chunk?

    Gates the NER submission for the entity-overlap channel — tenants with
    zero embeddings would hit the empty-vector early-return downstream.
    """
    try:
        stmt = (
            select(Embedding.id)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar() is not None
    except Exception:
        logger.warning("async_tenant_has_embeddings_check_failed", exc_info=True)
        return False


async def _async_kb_bucket_counts_from_scripts(
    tenant_id: uuid.UUID, db: AsyncSession
) -> dict[str, int] | None:
    """Count documents by writing system using ``Document.script``.

    Returns ``None`` when no row carries a usable script — either because
    none is stored, or because every document is letterless. Callers then fall
    back to chunk sampling, which is also what KBs indexed before parse-time
    script detection landed rely on.
    """
    try:
        stmt = (
            select(Document.script, func.count())
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.script.isnot(None))
            .filter(Document.script != NO_SCRIPT_BUCKET)
            .group_by(Document.script)
        )
        result = await db.execute(stmt)
        rows = result.all()
    except Exception:
        return None
    if not rows:
        return None
    return {script: count for script, count in rows}


async def _async_kb_bucket_counts_from_chunk_sample(
    tenant_id: uuid.UUID, db: AsyncSession
) -> dict[str, int] | None:
    """Legacy fallback: sample chunk text when no Document.language is set."""
    try:
        stmt = (
            select(Embedding.chunk_text)
            .join(Document, Embedding.document_id == Document.id)
            .filter(Document.tenant_id == tenant_id)
            .limit(_KB_SCRIPT_SAMPLE_SIZE)
        )
        result = await db.execute(stmt)
        sample = result.all()
    except Exception:
        return None
    if not sample:
        return None
    counts: dict[str, int] = {}
    for (chunk_text,) in sample:
        bucket = detect_script_bucket(chunk_text or "")
        if bucket != NO_SCRIPT_BUCKET:
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


async def _async_tenant_has_unlabeled_documents(
    tenant_id: uuid.UUID, db: AsyncSession
) -> bool:
    """True when at least one document for this tenant has script IS NULL.

    Used to decide whether labeled-only counts can be trusted: a partially
    labeled KB (legacy unlabeled rows + a few new uploads with script set)
    must still consider the unlabeled half, otherwise a single new document
    on top of a hundred legacy ones in another writing system misclassifies
    the whole KB.
    """
    try:
        stmt = (
            select(Document.id)
            .filter(Document.tenant_id == tenant_id)
            .filter(Document.script.is_(None))
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar() is not None
    except Exception:
        return False


async def _async_resolve_kb_bucket_sources(
    tenant_id: uuid.UUID, db: AsyncSession
) -> list[dict[str, int]]:
    """Return per-bucket counts, one distribution per source that contributed.

    Sources are kept apart rather than summed because they are not on the same
    scale: labeled documents are counted in full, while the legacy fallback
    can only ever see ``_KB_SCRIPT_SAMPLE_SIZE`` chunks. Sharing one total
    would let the labeled half drown the sampled half in any ratio test.
    """
    labeled = await _async_kb_bucket_counts_from_scripts(tenant_id, db)
    if labeled is None:
        # Pure legacy KB — no documents have a script stored.
        sampled = await _async_kb_bucket_counts_from_chunk_sample(tenant_id, db)
        return [sampled] if sampled else []
    if not await _async_tenant_has_unlabeled_documents(tenant_id, db):
        # Fully labeled — labeled counts are authoritative.
        return [labeled]
    # Partial labeling — sample alongside so unlabeled docs still contribute.
    sampled = await _async_kb_bucket_counts_from_chunk_sample(tenant_id, db)
    return [labeled, sampled] if sampled else [labeled]


async def _async_resolve_kb_bucket_counts(
    tenant_id: uuid.UUID, db: AsyncSession
) -> dict[str, int]:
    """Return per-bucket document counts across every contributing source."""
    merged: dict[str, int] = {}
    for source in await _async_resolve_kb_bucket_sources(tenant_id, db):
        for bucket, count in source.items():
            merged[bucket] = merged.get(bucket, 0) + count
    return merged


async def async_detect_tenant_kb_script(
    tenant_id: uuid.UUID, db: AsyncSession
) -> str | None:
    """Return the predominant script bucket of a tenant's KB.

    Backed by ``Document.script`` written at parse time; falls back to chunk
    sampling for KBs that pre-date parse-time detection. Cached per tenant to
    avoid a DB round-trip on every chat turn. Returns None when no documents
    map to a known script bucket.
    """
    key = str(tenant_id)
    now = time.monotonic()
    cached = _TENANT_KB_SCRIPT_CACHE.get(key)
    if cached is not None and now - cached[1] < _TENANT_KB_SCRIPT_CACHE_TTL:
        return cached[0]

    counts = await _async_resolve_kb_bucket_counts(tenant_id, db)
    result: str | None = None
    if counts:
        dominant = max(counts, key=counts.__getitem__)
        if dominant != NO_SCRIPT_BUCKET:
            result = dominant

    _TENANT_KB_SCRIPT_CACHE[key] = (result, now)
    return result


async def async_detect_tenant_kb_scripts(
    tenant_id: uuid.UUID, db: AsyncSession
) -> frozenset[str]:
    """Return every script bucket present in the tenant's KB.

    Mirrors :func:`async_detect_tenant_kb_script` but returns the full set so
    callers can issue cross-lingual rewrites for *each* KB language a query
    does not natively cover (mixed-script KBs in particular).

    Scripts below :data:`_KB_SCRIPT_MIN_SHARE` of the corpus are dropped: the
    caller spends one LLM call per returned script on every turn, and a single
    stray document is not worth that. The share is taken within each source
    separately — see :func:`_async_resolve_kb_bucket_sources`.
    """
    key = str(tenant_id)
    now = time.monotonic()
    cached = _TENANT_KB_SCRIPTS_CACHE.get(key)
    if cached is not None and now - cached[1] < _TENANT_KB_SCRIPT_CACHE_TTL:
        return cached[0]

    result: frozenset[str] = frozenset()
    for source in await _async_resolve_kb_bucket_sources(tenant_id, db):
        counted = {b: n for b, n in source.items() if b != NO_SCRIPT_BUCKET}
        total = sum(counted.values())
        result |= frozenset(
            b for b, n in counted.items() if n >= total * _KB_SCRIPT_MIN_SHARE
        )
    _TENANT_KB_SCRIPTS_CACHE[key] = (result, now)
    return result
